"""Tests for J-Link probe enumeration and selection."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helia_profiler.errors import CaptureError, ConfigError, DeterministicCaptureError
from helia_profiler.target.probe.flash import flash_binary
from helia_profiler.target.probe.jlink import (
    JLinkProbe,
    JLinkProbeMatch,
    attached_session,
    inspect_probe_target,
    find_jlink_exe,
    list_connected_probes,
    resolve_probe_serial,
)
from helia_profiler.platform import CoreArch, SocFamily


def _probe(serial: str, product: str = "J-Link") -> JLinkProbe:
    return JLinkProbe(serial=serial, product=product)


def _match(serial: str, core: CoreArch | None, product: str = "J-Link") -> JLinkProbeMatch:
    return JLinkProbeMatch(probe=_probe(serial, product), detected_core=core)


def _bin_only(tmp_path: Path) -> Path:
    """A built target with a .bin sibling but no NSX flash script.

    Module-level rather than a member of the fallback class: three test classes
    need this layout, and the two that are not the fallback's own were reaching
    across into it by class name.  It cannot simply move into
    ``_FlashRecipeFixtures`` either -- that base already defines a ``_flash``
    with an incompatible signature, so making the fallback class inherit it
    would dress an unrelated shadowing up as an override.
    """
    binary = tmp_path / "hpx_profiler_power"
    binary.write_bytes(b"\x00")
    binary.with_suffix(".bin").write_bytes(b"\x00")
    return binary


class _AsRequested:
    """Sentinel: "J-Link reports the address that was asked for".

    Distinct from ``None``, which the flash fixtures use to mean "J-Link named
    no bank at all" -- a real and separately tested case.
    """


AS_REQUESTED = _AsRequested()


class _HpxWarnings(logging.Handler):
    """Collect hpx warnings inside a block, from a helper with no ``caplog``."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.messages)

    def __enter__(self) -> _HpxWarnings:
        self._logger = logging.getLogger("hpx")
        self._level = self._logger.level
        self._logger.setLevel(logging.WARNING)
        self._logger.addHandler(self)
        return self

    def __exit__(self, *exc: object) -> bool:
        self._logger.removeHandler(self)
        self._logger.setLevel(self._level)
        return False


def test_attached_session_does_not_reset_or_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.reset_calls = 0
            self.restart_calls = 0
            self.close_calls = 0

        def reset(self, halt: bool = False) -> None:
            del halt
            self.reset_calls += 1

        def restart(self) -> None:
            self.restart_calls += 1

        def close(self) -> None:
            self.close_calls += 1

    session = FakeSession()
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.create_debug_memory_session",
        lambda: session,
    )
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.open_jlink_with_retry",
        lambda *args, **kwargs: None,
    )

    with attached_session(device="AP510NFA-CBR", attach_timeout_s=1.0) as attached:
        assert attached is session

    assert session.reset_calls == 0
    assert session.restart_calls == 0
    assert session.close_calls == 1


def test_list_connected_probes_is_nongui_and_parses_multiple_products() -> None:
    output = """
J-Link[0]: Connection: USB, Serial number: 1160003180, ProductName: J-Link-OB-Apollo4-CortexM
J-Link[1]: Connection: USB, Serial number: 1160003409, ProductName: J-Link-OB-Apollo4-CortexM
"""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        assert kwargs["input"] == "ShowEmuList\nexit\n"
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    with (
        patch("helia_profiler.target.probe.jlink.find_jlink_exe", return_value="JLinkExe"),
        patch("subprocess.run", side_effect=fake_run),
    ):
        probes = list_connected_probes()

    assert calls == [["JLinkExe", "-NoGui", "1"]]
    assert [probe.serial for probe in probes] == ["1160003180", "1160003409"]
    assert {probe.product for probe in probes} == {"J-Link-OB-Apollo4-CortexM"}


def test_find_jlink_exe_accepts_windows_executable_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JLINK_PATH", raising=False)
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.shutil.which",
        lambda name: r"C:\SEGGER\JLink.exe" if name == "JLink.exe" else None,
    )

    assert find_jlink_exe() == r"C:\SEGGER\JLink.exe"


def test_find_jlink_exe_prefers_explicit_path(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = tmp_path / "custom-jlink"
    explicit.write_text("")
    monkeypatch.setenv("JLINK_PATH", str(explicit))

    assert find_jlink_exe() == str(explicit)


class TestResolveProbeSerial:
    def test_requested_serial_must_exist(self) -> None:
        probes = [_probe("111111", "Probe A"), _probe("222222", "Probe B")]
        with patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=probes):
            with pytest.raises(ConfigError, match="was not found") as exc_info:
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                    requested_serial="999999",
                )
        hint = exc_info.value.hint or ""
        assert "111111" in hint
        assert "222222" in hint

    def test_requested_serial_must_match_target_core(self) -> None:
        probe = _probe("111111", "Apollo4")
        with (
            patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=[probe]),
            patch(
                "helia_profiler.target.probe.jlink._inspect_probe_target",
                return_value=_match("111111", CoreArch.CORTEX_M4, "Apollo4"),
            ),
        ):
            with pytest.raises(ConfigError, match="does not match the requested target"):
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                    requested_serial="111111",
                )

    def test_requested_serial_returns_when_target_matches(self) -> None:
        probe = _probe("111111", "Apollo5")
        with (
            patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=[probe]),
            patch(
                "helia_profiler.target.probe.jlink._inspect_probe_target",
                return_value=_match("111111", CoreArch.CORTEX_M55, "Apollo5"),
            ),
        ):
            assert (
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                    requested_serial="111111",
                )
                == "111111"
            )

    def test_auto_selects_unique_matching_probe(self) -> None:
        probes = [_probe("111111", "Apollo4"), _probe("222222", "Apollo5")]

        def inspect(probe: JLinkProbe, *, device: str) -> JLinkProbeMatch:
            return _match(
                probe.serial,
                CoreArch.CORTEX_M55 if probe.serial == "222222" else CoreArch.CORTEX_M4,
                probe.product,
            )

        with (
            patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=probes),
            patch(
                "helia_profiler.target.probe.jlink._inspect_probe_target",
                side_effect=inspect,
            ),
        ):
            assert (
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                )
                == "222222"
            )

    def test_ambiguous_matching_probes_raise(self) -> None:
        probes = [_probe("111111", "Probe A"), _probe("222222", "Probe B")]

        def inspect(probe: JLinkProbe, *, device: str) -> JLinkProbeMatch:
            return _match(probe.serial, CoreArch.CORTEX_M55, probe.product)

        with (
            patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=probes),
            patch(
                "helia_profiler.target.probe.jlink._inspect_probe_target",
                side_effect=inspect,
            ),
        ):
            with pytest.raises(ConfigError, match="match the requested target") as exc_info:
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                )
        hint = exc_info.value.hint or ""
        assert "111111" in hint
        assert "222222" in hint

    def test_no_matching_probe_raises_with_detected_cores(self) -> None:
        probes = [_probe("111111", "Probe A"), _probe("222222", "Probe B")]

        def inspect(probe: JLinkProbe, *, device: str) -> JLinkProbeMatch:
            return _match(probe.serial, CoreArch.CORTEX_M4, probe.product)

        with (
            patch("helia_profiler.target.probe.jlink.list_connected_probes", return_value=probes),
            patch(
                "helia_profiler.target.probe.jlink._inspect_probe_target",
                side_effect=inspect,
            ),
        ):
            with pytest.raises(
                ConfigError, match="Could not find a connected J-Link probe"
            ) as exc_info:
                resolve_probe_serial(
                    device="AP510NFA-CBR",
                    expected_core=CoreArch.CORTEX_M55,
                )
        hint = exc_info.value.hint or ""
        assert "cortex-m4" in hint


class TestFlashBinaryFallback:
    """The .bin fallback used when NSX's generated flash script is missing.

    The load address is per-SoC-family, so the fallback must use the address
    the caller resolved from the target rather than a hardcoded one -- an
    Apollo5 MRAM address programs nothing usable on an Apollo3/Apollo4 part.
    """

    @staticmethod
    def _jlink_output(bank_addr: int | None) -> str:
        """JLinkExe's success output, naming *bank_addr* the way it really does.

        The bank line must be here.  A stub carrying only the ``Total:`` summary
        sends every caller down ``_verify_flash_address``'s fail-open branch,
        which warns and returns -- so the address check would be untested by
        each of these while still looking exercised.  ``None`` requests that
        no-bank output deliberately; only the fail-open test should ask for it.
        """
        if bank_addr is None:
            return "J-Link: Flash download: Total 1 range"
        return (
            f"J-Link: Flash download: Bank 0 @ 0x{bank_addr:08X}: "
            "1 range affected (4096 bytes)\n"
            "J-Link: Flash download: Total 1 range"
        )

    @classmethod
    def _flash(
        cls,
        binary: Path,
        *,
        device: str,
        load_addr: int | None,
        bank_addr: int | None | _AsRequested = AS_REQUESTED,
    ) -> str:
        """Run the fallback against a stubbed JLinkExe; return the script.

        *bank_addr* is the address J-Link reports programming into, defaulting
        to the one requested (the correct-flash case).  Pass it explicitly when
        the expected address does not come from *load_addr* -- the recipe path
        -- or as ``None`` to request the no-bank output.
        """
        if isinstance(bank_addr, _AsRequested):
            bank_addr = load_addr
        ok = SimpleNamespace(returncode=0, stdout=cls._jlink_output(bank_addr), stderr="")
        with (
            _HpxWarnings() as warnings,
            patch("helia_profiler.target.probe.flash.run_jlink_script", return_value=ok) as run,
        ):
            flash_binary(binary, device=device, load_addr=load_addr)
        if bank_addr is not None:
            # Whatever else each caller asserts, the flash must have gone
            # through the real address check rather than the fail-open branch.
            # Without this, dropping the bank line from ``_jlink_output`` would
            # leave every test here green while silently testing nothing --
            # which is what the stub used to do.
            assert "UNVERIFIED FLASH DESTINATION" not in warnings.text
        return run.call_args.args[0]

    @pytest.mark.parametrize(
        ("board", "family", "expected"),
        [
            ("apollo3p_evb", SocFamily.AP3, "0x0000C000"),
            ("apollo4p_blue_kxr_evb", SocFamily.AP4, "0x00018000"),
            ("apollo510_evb", SocFamily.AP5, "0x00410000"),
        ],
    )
    def test_uses_the_load_address_of_each_soc_family(
        self, tmp_path: Path, board: str, family: SocFamily, expected: str
    ) -> None:
        from helia_profiler.platform import get_soc_for_board

        soc = get_soc_for_board(board)
        assert soc.family is family
        load_addr = soc.capabilities.memory.app_flash_load_addr

        binary = _bin_only(tmp_path)
        script = self._flash(binary, device=soc.jlink_device, load_addr=load_addr)

        # Pin the WHOLE recipe, not just the address.  Every element is
        # load-bearing: ExitOnError 1 is the primary failure gate; the .bin
        # sibling (never the extension-less ELF, which once silently programmed
        # nothing on Apollo510); the quoted path; and the trailing Reset+Go
        # without which the target is left halted and the capture measures a
        # stopped CPU.  A substring assertion lets any of those be deleted.
        assert script == (
            "ExitOnError 1\nReset\n"
            f'LoadFile "{binary.with_suffix(".bin")}", {expected}\n'
            "Reset\nGo\nExit\n"
        )
        # An AP5 address on a non-AP5 part is exactly the bug this guards.
        if family is not SocFamily.AP5:
            assert "0x00410000" not in script

    def test_zero_is_a_real_address_not_an_unknown_one(self, tmp_path: Path) -> None:
        """Only ``None`` means "unknown"; 0 is an address like any other.

        No registered family maps to 0 today, so this pins the distinction
        rather than a live case -- but 0 is a real NSX ``NSX_SEGGER_PF_ADDR``
        value (apollo2's), so writing the guard as ``if not load_addr`` would
        one day refuse to flash a part instead of programming it at 0.
        """
        binary = _bin_only(tmp_path)
        script = self._flash(binary, device="AMA3B2KK-KBR", load_addr=0)

        assert '", 0x00000000\n' in script

    def test_probe_settings_are_forwarded_to_the_jlink_run(self, tmp_path: Path) -> None:
        """The script is only half the recipe -- the probe kwargs matter too.

        Asserting script text alone lets ``device`` (or the serial, which
        selects among several attached probes) be hardcoded or dropped while
        every other test stays green, sending the flash to the wrong target.
        """
        ok = SimpleNamespace(returncode=0, stdout=self._jlink_output(0x00018000), stderr="")
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script", return_value=ok
        ) as run:
            flash_binary(
                _bin_only(tmp_path),
                device="AMAP42KP-KBR",
                load_addr=0x00018000,
                jlink_serial="1160002204",
                speed_khz=1234,
                interface="JTAG",
                timeout_s=99,
            )

        kwargs = run.call_args.kwargs
        assert kwargs["device"] == "AMAP42KP-KBR"
        assert kwargs["jlink_serial"] == "1160002204"
        assert kwargs["speed_khz"] == 1234
        assert kwargs["interface"] == "JTAG"
        assert kwargs["timeout_s"] == 99

    def test_missing_bin_sibling_raises_naming_both_paths(self, tmp_path: Path) -> None:
        """Neither an NSX recipe nor a .bin sibling: refuse, naming both.

        Without this the ``.bin`` existence check can be deleted silently --
        ``LoadFile`` on a nonexistent file would then be caught only by
        JLinkExe's own exit status rather than a clear message here.
        """
        binary = tmp_path / "hpx_profiler_power"
        binary.write_bytes(b"\x00")  # ELF only; no .bin sibling

        # The specific subclass matters: it is what tells flash_power's
        # recovery path not to power-cycle and retry a config gap (#151).
        with pytest.raises(DeterministicCaptureError) as exc_info:
            self._flash(binary, device="AMA3B2KK-KBR", load_addr=0x00018000)

        message = str(exc_info.value)
        assert "flash_cmds.jlink" in message
        assert "hpx_profiler_power.bin" in message

    def test_unknown_load_address_raises_naming_recipe_and_device(self, tmp_path: Path) -> None:
        # DeterministicCaptureError, not the base: see the missing-.bin test.
        with pytest.raises(DeterministicCaptureError) as exc_info:
            self._flash(_bin_only(tmp_path), device="SOME-NEW-PART", load_addr=None)

        message = str(exc_info.value)
        assert "flash_cmds.jlink" in message
        assert "SOME-NEW-PART" in message
        assert "app_flash_load_addr" in (exc_info.value.hint or "")

    def test_unknown_load_address_hint_names_the_yaml_a_user_can_type(self, tmp_path: Path) -> None:
        """The hint must name config keys, not the internal dataclass field.

        ``MemoryCapabilities.app_flash_load_addr`` is a Python attribute -- a
        user cannot write it anywhere, so naming it sends them looking for a
        setting that does not exist.  #153 makes ``load_addr=None`` reachable
        for a custom SoC for the first time, and the two ways to supply the
        address there are ``target.custom_socs.<name>.app_flash_load_addr`` and
        ``based_on``.  Both must be named, and the config fix must lead: for a
        custom SoC that landed here, re-running the build fixes nothing.
        """
        with pytest.raises(CaptureError) as exc_info:
            self._flash(_bin_only(tmp_path), device="SOME-NEW-PART", load_addr=None)

        hint = exc_info.value.hint or ""
        assert "target.custom_socs" in hint
        assert "based_on" in hint
        assert "MemoryCapabilities" not in hint
        # Config first, re-run-the-build second -- ordering is the finding.
        assert hint.index("target.custom_socs") < hint.index("re-run the build")

    def test_the_unknown_address_refusal_never_renders_with_an_empty_device(
        self, tmp_path: Path
    ) -> None:
        """``jlink_device`` is "" for exactly the entry that reaches this branch.

        ``platform.custom`` inherits the device string from ``based_on`` and
        defaults it to the empty string when there is no ``based_on`` -- which
        is the same under-specified ``custom_socs`` entry that leaves the
        address unset, so the two arrive together rather than independently.
        Interpolated blind the sentence renders as "...known for device  to fall
        back to": a hole where the part's name belongs, in the one message whose
        entire job is telling the user which knob to turn.

        The empty device is worth SAYING, not just routing around: it is a
        second symptom of the one cause, and a reader who sees it named will fix
        the entry rather than only the address.
        """
        with pytest.raises(CaptureError) as exc_info:
            self._flash(_bin_only(tmp_path), device="", load_addr=None)

        message = str(exc_info.value)
        assert "for device  " not in message
        assert "known for this target's SoC to fall back to" in message
        assert "no J-Link device string either" in message
        # A named device must still be named -- the empty case is the exception,
        # not a reason to stop identifying the part when hpx knows it.
        with pytest.raises(CaptureError) as named_exc:
            self._flash(_bin_only(tmp_path), device="SOME-NEW-PART", load_addr=None)
        assert "device SOME-NEW-PART" in str(named_exc.value)

    def test_a_missing_bank_line_warns_rather_than_failing_the_flash(
        self, tmp_path: Path, caplog
    ) -> None:
        """The one no-bank test, kept explicit now the stub carries a bank line.

        ``_jlink_output`` names a bank for every other test here so they run
        through the real address check; this pins the fail-open branch they used
        to hit by accident, and that its warning says the destination is
        UNVERIFIED rather than reporting a mere parse miss.
        """
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(
                _bin_only(tmp_path),
                device="AMAP42KP-KBR",
                load_addr=0x00018000,
                bank_addr=None,
            )

        assert "UNVERIFIED FLASH DESTINATION" in caplog.text
        assert "0x00018000" in caplog.text

    def test_generated_script_is_used_verbatim_and_ignores_load_addr(self, tmp_path: Path) -> None:
        binary = _bin_only(tmp_path)
        script_dir = tmp_path / "jlink" / "hpx_profiler_power"
        script_dir.mkdir(parents=True)
        # Shaped like NSX's flash_cmds.jlink.in: quoted ABSOLUTE path to this
        # build's .bin.  The address is deliberately not any SoC's, to prove the
        # recipe -- not the caller's load_addr -- decides where the image goes.
        recipe = (
            "ExitOnError 1\nReset\n"
            f'LoadFile "{binary.with_suffix(".bin")}", 0xDEADBEEF\n'
            "Reset\nGo\nExit\n"
        )
        (script_dir / "flash_cmds.jlink").write_text(recipe)

        # The recipe wins whatever load_addr is -- including a resolved one, the
        # only case production actually hits.  Hand-rolling instead of running
        # the generated recipe is the regression the docstring warns about.
        # J-Link reports the RECIPE's bank, so passing the address check here is
        # itself evidence that the recipe -- not load_addr -- set the expectation.
        assert (
            self._flash(binary, device="AMA3B2KK-KBR", load_addr=0x00018000, bank_addr=0xDEADBEEF)
            == recipe
        )
        # load_addr=None must not raise when the generated recipe exists.
        assert (
            self._flash(binary, device="AMA3B2KK-KBR", load_addr=None, bank_addr=0xDEADBEEF)
            == recipe
        )


def test_every_registered_soc_has_its_nsx_app_flash_load_address() -> None:
    """Pin the VALUE per SoC, not merely that some value exists.

    Asserting only ``is not None`` would let a default creep into the family
    lookup (``.get(family, 0x00410000)``) and hand every unmapped family the
    Apollo5 address -- reinstating the exact bug this guards, invisibly.  These
    are NSX's own ``NSX_SEGGER_PF_ADDR`` values from ``cmake/socs/facts/``.

    A new SoC must be added here deliberately: hpx's family axis is coarser
    than NSX's (apollo330P is its own NSX family but AP5 here), so "same
    family" is not evidence of "same address" -- atomiq110 is tagged AP5 for
    its core tier while heading a separate series, and loads at 0x22000000.
    """
    from helia_profiler.platform import list_socs

    expected = {
        "apollo3p": 0x0000C000,
        "apollo4p": 0x00018000,
        "apollo4l": 0x00018000,
        "apollo510": 0x00410000,
        "apollo510b": 0x00410000,
        "apollo5b": 0x00410000,
        "apollo330P": 0x00410000,
    }
    actual = {soc.name: soc.capabilities.memory.app_flash_load_addr for soc in list_socs()}

    assert actual == expected


def test_a_per_soc_address_overrides_its_family(monkeypatch: pytest.MonkeyPatch) -> None:
    """A part whose flash window departs from its family must win over it.

    The motivating case is atomiq110, which is not an Apollo5 part at all --
    it heads a separate Atomiq series and is only tagged AP5 as a placeholder
    for its Cortex-M55 core tier.  It loads at 0x22000000, so without per-SoC
    precedence it would silently inherit Apollo5's 0x00410000.

    Uses a mixed-case registered name so that normalizing the lookup key
    (``soc.name.lower()``) fails here instead of silently never matching the
    mixed-case parts in the registry.
    """
    from helia_profiler.platform import capabilities, get_soc

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo330P", 0x22000000)

    assert get_soc("apollo330P").capabilities.memory.app_flash_load_addr == 0x22000000


def test_the_atomiq110_override_value_is_pinned() -> None:
    """Pin atomiq110's address while it is still unreachable data.

    It is not a registered SoC yet (PR #98 adds it), so no behavioural test can
    reach this entry and a wrong value would land on main unnoticed.  The value
    is nsx's ``NSX_SEGGER_PF_ADDR`` for the part and is the **nbl** origin,
    because atomiq110.cmake makes nbl the default profile -- the FPGA
    realization is flashed straight over J-Link with no secure bootloader.  Its
    *sbl* scripts sit at 0x22010000, reserved for future AT110 silicon, so
    "correcting" this to the sbl address would break the part that exists.
    """
    from helia_profiler.platform import capabilities

    assert capabilities._SOC_APP_FLASH_LOAD_ADDR["atomiq110"] == 0x22000000


def test_a_per_soc_address_of_zero_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """0 is a real address in the override tier too, not "no override".

    The family tier already distinguishes 0 from None; writing this tier's
    check as ``if override:`` would silently drop a legitimate 0 and fall
    through to the family address -- the exact silent inheritance the override
    map exists to prevent.
    """
    from helia_profiler.platform import capabilities, get_soc

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo510", 0)

    assert get_soc("apollo510").capabilities.memory.app_flash_load_addr == 0


def test_a_custom_soc_cannot_forge_a_per_soc_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user-named custom SoC must not pick up a built-in's override.

    ``target.custom_socs`` names are user-chosen and replace built-ins in the
    merged registry, so matching the override by name alone would let the name
    outrank the ``family`` the user explicitly declared -- and hand their part
    an address belonging to a different SoC, past the ``load_addr is None``
    guard.

    The entry deliberately states no address and names no ``based_on``: those
    are the only two ways a custom SoC gets an address of its own, and either
    of them answers before the origin gate is ever consulted.  With both
    absent, the *only* thing that can produce a non-``None`` answer here is the
    name matching the patched override -- which is exactly the forgery, so the
    gate is what this observes.  (An earlier revision carried
    ``based_on: apollo4p``, which made the assertion a statement about
    inheritance and left the gate untested: deleting it kept the test green.)
    """
    from helia_profiler.platform import capabilities
    from helia_profiler.platform.custom import build_custom_platform_registry

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo510", 0x22000000)
    registry = build_custom_platform_registry(
        {
            "custom_socs": {
                "apollo510": {
                    "family": "ap4",
                    "core": "cortex-m4",
                    "pmu_tier": "dwt",
                    "has_mve": False,
                    "c_define": "AM_PART_OEM4",
                    "cmsis_header": "oem4.h",
                    "memory": {"mram_kb": 2000, "sram_kb": 1024, "dtcm_kb": 384},
                    "clocks": [
                        {
                            "name": "cpu",
                            "speeds": [{"name": "lp", "mhz": 96}],
                            "default": "lp",
                        }
                    ],
                    "rtt_scan_ranges": [[0x10000000, 0x100000]],
                }
            }
        }
    )

    soc = registry.socs["apollo510"]
    assert soc.family is SocFamily.AP4
    assert soc.capabilities.memory.app_flash_load_addr is None


def test_a_family_with_no_registered_address_resolves_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmapped family must yield None so the flash fallback refuses to run.

    Pinning the registered values alone cannot see this: a default in the
    lookup (``.get(family, 0x00410000)``) leaves every mapped family correct
    while silently handing the Apollo5 address to any family added later --
    reinstating exactly the bug this field exists to prevent.
    """
    from helia_profiler.platform import capabilities, get_soc

    trimmed = dict(capabilities._FAMILY_APP_FLASH_LOAD_ADDR)
    assert trimmed.pop(SocFamily.AP5) is not None
    monkeypatch.setattr(capabilities, "_FAMILY_APP_FLASH_LOAD_ADDR", trimmed)

    assert get_soc("apollo510").capabilities.memory.app_flash_load_addr is None


def test_inspect_probe_target_wraps_private_inspector() -> None:
    probe = _probe("111111", "Apollo5")
    match = _match("111111", CoreArch.CORTEX_M55, "Apollo5")
    with patch("helia_profiler.target.probe.jlink._inspect_probe_target", return_value=match) as inspect:
        assert inspect_probe_target(probe, device="AP510NFA-CBR") is match
    inspect.assert_called_once_with(probe, device="AP510NFA-CBR")


def test_inspect_probe_target_retries_unknown_target() -> None:
    probe = _probe("111111", "Apollo5")
    results = [
        SimpleNamespace(returncode=0, stdout="Connecting to target...", stderr=""),
        SimpleNamespace(returncode=0, stdout="Found Cortex-M55", stderr=""),
    ]
    with (
        patch("helia_profiler.target.probe.jlink.find_jlink_exe", return_value="JLinkExe"),
        patch("helia_profiler.target.probe.jlink.subprocess.run", side_effect=results) as run,
        patch("helia_profiler.target.probe.jlink.time.sleep") as sleep,
    ):
        match = inspect_probe_target(probe, device="AP510NFA-CBR")

    assert match.detected_core is CoreArch.CORTEX_M55
    assert run.call_count == 2
    sleep.assert_called_once()


class _FlashRecipeFixtures:
    """Captured hardware output and the NSX build layout the recipe path reads.

    Not collected by pytest (no ``Test`` prefix); the three classes below share
    it so the JLinkExe wordings stay defined once, exactly as captured.
    """

    # Secure Apollo5 parts always erase+reprogram, printing the Total summary.
    _PROGRAMMED = (
        "J-Link: Flash download: Bank 0 @ 0x00410000: 1 range affected (761856 bytes)\n"
        "J-Link: Flash download: Total: 4.088s (Prepare: 0.139s, Compare: 0.000s, "
        "Erase: 0.000s, Program: 3.549s, Verify: 0.308s, Restore: 0.090s)\n"
        "O.K.\n"
    )
    # AP4-class parts skip byte-identical images: only the skip notice, no
    # "Total:" line.  (Secure Apollo5 parts never print this notice.)
    _SKIPPED_IDENTICAL = (
        "J-Link: Flash download: Bank 0 @ 0x00018000: Skipped. Contents already match\n"
        "O.K.\n"
    )
    # JLinkExe prints this on ANY successful connection, before flashing
    # anything — a recipe that connects and programs nothing looks like this.
    _CONNECTION_ONLY = "Connecting to J-Link via USB...O.K.\n"

    @classmethod
    def _programmed_at(cls, address: str) -> str:
        """The captured "programmed" output, retargeted to another address.

        Derived from the real capture rather than hand-written so the surrounding
        wording that the parser has to survive stays byte-identical.
        """
        return cls._PROGRAMMED.replace("0x00410000", address)

    @staticmethod
    def _build(tmp_path: Path, *, recipe: str | None = None, addr: str = "0x00410000") -> Path:
        """A built power target laid out the way NSX lays one out.

        ELF and ``.bin`` side by side in the build dir, recipe under
        ``jlink/<target>/``, and the recipe's ``LoadFile`` naming the ``.bin``
        by ABSOLUTE path — which is what NSX's flash_cmds.jlink.in emits.
        """
        binary = tmp_path / "hpx_profiler_power"
        binary.write_bytes(b"\x00")
        bin_path = binary.with_suffix(".bin")
        bin_path.write_bytes(b"\x00")
        script_dir = tmp_path / "jlink" / "hpx_profiler_power"
        script_dir.mkdir(parents=True, exist_ok=True)
        if recipe is None:
            recipe = f'ExitOnError 1\nReset\nLoadFile "{bin_path}", {addr}\nReset\nGo\nExit\n'
        # UTF-8 on the write side too, the way NSX emits it -- otherwise the
        # non-ASCII-path test would be measuring the locale codec twice and
        # cancel its own bug out on Windows.
        (script_dir / "flash_cmds.jlink").write_text(recipe, encoding="utf-8")
        return binary

    def _flash(
        self,
        tmp_path,
        output: str,
        *,
        recipe: str | None = None,
        addr: str = "0x00410000",
        load_addr: int | None = None,
    ) -> None:
        binary = self._build(tmp_path, recipe=recipe, addr=addr)
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script",
            return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
        ):
            # These exercise the NSX-recipe path, which needs no load address.
            flash_binary(
                binary, device="AP510NFA-CBR", load_addr=load_addr, jlink_serial="1160002204"
            )


class TestFlashBinaryVerification(_FlashRecipeFixtures):
    """flash_binary must demand explicit flash evidence, not a bare connection O.K.

    Command failures are already gated by exit status (``ExitOnError 1`` in
    every recipe plus ``run_jlink_script``'s CaptureError on nonzero rc);
    these tests pin the output tripwire that catches recipes which connect
    successfully but never issue a program step (issue #101).  All three
    captured outputs above were taken from real hardware.
    """

    def test_programmed_output_verifies(self, tmp_path) -> None:
        self._flash(tmp_path, self._PROGRAMMED)

    def test_skipped_identical_output_verifies(self, tmp_path) -> None:
        # The skip notice carries its own bank address, so the recipe here has
        # to be the AP4-class one that capture came from; pairing it with an AP5
        # recipe address is the mismatch TestFlashBinaryAddressVerification pins.
        self._flash(tmp_path, self._SKIPPED_IDENTICAL, addr="0x00018000")

    def test_connection_only_ok_is_rejected(self, tmp_path) -> None:
        with pytest.raises(CaptureError, match="no recognized flash confirmation"):
            self._flash(tmp_path, self._CONNECTION_ONLY)


class TestFlashBinaryAddressVerification(_FlashRecipeFixtures):
    """The flash must land where it was asked to land, not merely somewhere.

    A flash to a wrong-but-writable address prints a byte-for-byte identical
    ``Total:`` summary, so the marker tripwire above passes it and the device
    then boots stale firmware from its real entry point while hpx publishes
    power numbers attributed to the new build (issue #150).  J-Link names the
    destination on every flash; these pin that hpx reads it.

    Inherits the captured-hardware fixtures and layout helpers above.
    """

    def test_mismatched_address_raises_naming_both(self, tmp_path) -> None:
        """The recipe asks for AP5's address; J-Link reports AP4's."""
        with pytest.raises(CaptureError) as exc_info:
            self._flash(tmp_path, self._programmed_at("0x00018000"), addr="0x00410000")

        message = str(exc_info.value)
        # Both addresses, or the message cannot be acted on.
        assert "0x00410000" in message
        assert "0x00018000" in message
        assert exc_info.value.hint

    def test_the_expected_address_comes_from_the_recipe_not_load_addr(self, tmp_path) -> None:
        """On the recipe path ``load_addr`` is neither consulted nor required.

        NSX bakes the address into the recipe hpx runs verbatim, so the recipe
        is the authority.  Comparing against ``load_addr`` there would reject a
        correct flash whenever the caller's value differs -- and #149 makes
        ``app_flash_load_addr`` resolve to ``None`` more often for custom SoCs,
        which would leave the check with nothing to compare against at all.
        """
        # A wrong caller value must not fail a flash the recipe got right...
        self._flash(
            tmp_path, self._programmed_at("0x00018000"), addr="0x00018000", load_addr=0x00410000
        )

    def test_a_declared_address_that_contradicts_the_recipe_is_warned_about(
        self, tmp_path, caplog
    ) -> None:
        """Recipe still wins -- but the disagreement must not vanish silently.

        hpx holds two authoritative-looking statements about where this part
        boots: the address NSX baked into the recipe from the build's own linker
        configuration, and the one the config declared.  It picks the recipe,
        and that precedence is correct and documented.  What was missing is
        DETECTION: of every record the flash emitted, none named the declared
        address and none was WARNING or above.

        The disagreement is free, high-quality evidence that the build's linker
        configuration is not for the part the user declared.  If the DECLARED
        value is the right one, the image lands at the wrong offset and hpx
        publishes power numbers for stale firmware -- #150's own failure mode,
        arriving through the one door this path deliberately leaves open.  Not
        hypothetical: #153 makes a custom SoC's ``app_flash_load_addr``
        declarable and documents ``based_on: apollo510`` alongside a declared
        address as a working configuration, so this is a shape the guide sends
        users towards rather than one they have to contrive.

        That this does not RAISE is also pinned, by the test above: refusing
        would break the legitimate divergence the recipe path exists to allow.
        """
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(tmp_path, self._PROGRAMMED, addr="0x00410000", load_addr=0x22000000)

        assert "DECLARED FLASH ADDRESS IGNORED" in caplog.text
        # Both addresses, or the reader cannot tell which one to go and fix.
        assert "0x22000000" in caplog.text
        assert "0x00410000" in caplog.text
        # ...and which of the two actually won, said plainly.
        assert "recipe wins" in caplog.text
        # Precedence is unchanged, and this asserts it rather than assuming it:
        # J-Link reported the RECIPE's bank above, so had the declared address
        # displaced it the address check would have raised instead of warning.

    def test_the_divergence_warning_names_the_config_key_to_edit(self, tmp_path, caplog) -> None:
        """Detecting the disagreement is only half of it; say what to go and fix.

        The warning's subject used to be ``target_name`` -- ``hpx_profiler_power``,
        the firmware image being flashed, which declares no address at all.  The
        declaration the reader has to act on lives at
        ``target.custom_socs.<name>.app_flash_load_addr`` (or is inherited from
        the part that entry's ``based_on:`` names, which #153 documents as a
        working shape), and the warning named neither.  It therefore told a user
        that two addresses disagree while pointing them at a file that contains
        neither one.

        The sibling refusal on the fallback branch already names both keys, and
        its own test pins that; a warning about the same setting should not be
        the less actionable of the two.
        """
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(tmp_path, self._PROGRAMMED, addr="0x00410000", load_addr=0x22000000)

        assert "target.custom_socs.<name>.app_flash_load_addr" in caplog.text
        # The address can be inherited rather than typed, so the entry that
        # supplies it is the second place the fix might belong.
        assert "based_on" in caplog.text
        # The firmware target stays named -- it says WHICH flash this is about,
        # which matters once more than one target has been flashed in a run.
        # Asserted against the interpolated phrase, not the bare target name:
        # the recipe path in the same message already contains that name, so a
        # substring check for it alone would pass with the argument dropped.
        assert "flashing hpx_profiler_power" in caplog.text

    def test_a_declared_address_agreeing_with_the_recipe_is_not_warned_about(
        self, tmp_path, caplog
    ) -> None:
        """The warning fires on DISAGREEMENT, not on the value merely existing.

        Warning whenever ``load_addr`` is set would fire on every correct flash
        of every built-in part -- the registry and NSX's recipes agree on all
        seven -- and a warning that cries wolf on the normal case is one nobody
        reads on the abnormal one.  ``None`` must stay silent too: the recipe
        path neither needs nor consults it to decide, and #153 makes ``None``
        the common answer for custom SoCs.
        """
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(tmp_path, self._PROGRAMMED, addr="0x00410000", load_addr=0x00410000)
            self._flash(tmp_path, self._PROGRAMMED, addr="0x00410000", load_addr=None)

        assert "DECLARED FLASH ADDRESS IGNORED" not in caplog.text

    def test_recipe_path_verifies_when_load_addr_is_none(self, tmp_path) -> None:
        """...and an absent caller value must not disable the check either."""
        self._flash(tmp_path, self._programmed_at("0x0000C000"), addr="0x0000C000", load_addr=None)

        with pytest.raises(CaptureError, match="0x0000C000"):
            self._flash(
                tmp_path, self._programmed_at("0x00410000"), addr="0x0000C000", load_addr=None
            )

    def test_a_decimal_recipe_address_is_tolerated(self, tmp_path) -> None:
        """hpx reads a bare decimal address as decimal; J-Link's rule is unknown.

        Deliberately NOT a claim about J-Link.  Its numeric convention is
        per-command -- ``sleep 100`` runs ``Sleep(100)`` while ``sleep 0x100``
        runs ``Sleep(0)`` -- and nobody has checked which one ``LoadFile``
        follows.  Every real NSX recipe uses the ``0x`` form, so this pins only
        that hpx does not read 4259840 as hex and reject a flash over it.
        """
        self._flash(tmp_path, self._PROGRAMMED, addr=str(0x00410000))

    def test_one_matching_bank_of_several_verifies(self, tmp_path) -> None:
        """A multi-range image prints one bank line per range."""
        output = (
            "J-Link: Flash download: Bank 0 @ 0x00410000: 1 range affected (761856 bytes)\n"
            "J-Link: Flash download: Bank 1 @ 0x00800000: 1 range affected (4096 bytes)\n"
            "J-Link: Flash download: Total: 4.088s (Prepare: 0.139s, Compare: 0.000s, "
            "Erase: 0.000s, Program: 3.549s, Verify: 0.308s, Restore: 0.090s)\n"
        )
        self._flash(tmp_path, output, addr="0x00410000")

    def test_only_a_programming_confirmation_counts_as_a_bank(self, tmp_path) -> None:
        """A ``Bank N @ …`` that is not a flash-download line must not satisfy it.

        Four J-Link format strings carry the shape; three are not confirmations
        (``Start of determining flash info``, ``Error while determining flash
        info``, and the sector-to-chip-erase notice).  Unanchored, the error
        line below -- which names the RIGHT bank -- would mask the wrong bank
        actually programmed on the line after it, and the check would pass the
        exact flash it exists to refuse.  Not seen at default verbosity today,
        so this pins a latent hole shut rather than a live one.
        """
        output = (
            "Error while determining flash info (Bank 0 @ 0x00410000)\n"
            "J-Link: Flash download: Bank 1 @ 0x00080000: 1 range affected (761856 bytes)\n"
            "J-Link: Flash download: Total: 4.088s (Prepare: 0.139s)\n"
        )
        with pytest.raises(CaptureError) as exc_info:
            self._flash(tmp_path, output, addr="0x00410000")

        message = str(exc_info.value)
        assert "0x00080000" in message
        assert "0x00410000" in message

    @pytest.mark.parametrize(
        "line",
        [
            "Start of determining flash info (Bank 0 @ 0x00410000)",
            "Error while determining flash info (Bank 0 @ 0x00410000)",
            "Bank 0 @ 0x00410000: Switched from sector erase to chip erase",
        ],
    )
    def test_the_informational_bank_shapes_are_all_ignored(self, tmp_path, caplog, line) -> None:
        """None of the three non-confirmation shapes may stand in for a bank.

        With only the error-line case pinned above, restoring the unanchored
        regex for either of the other two would go unnoticed.  Here the correct
        bank is named by the informational line and by nothing else, so an
        unanchored check would pass where hpx must instead warn that it
        verified nothing.
        """
        output = f"{line}\nJ-Link: Flash download: Total: 4.088s (Prepare: 0.139s)\n"
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(tmp_path, output, addr="0x00410000")

        assert "UNVERIFIED FLASH DESTINATION" in caplog.text

    def test_output_naming_no_bank_is_warned_not_rejected(self, tmp_path, caplog) -> None:
        """Deliberate: no address in the output means no evidence either way.

        The address line is corroboration on top of the exit-status gate and the
        summary tripwire.  If J-Link reworded it, raising here would block every
        correct flash on that part without any sign of a wrong one -- so this
        degrades to a warning naming the address that could not be confirmed.
        """
        with caplog.at_level(logging.WARNING, logger="hpx"):
            self._flash(tmp_path, "J-Link: Flash download: Total: 4.088s\nO.K.\n")

        assert "0x00410000" in caplog.text

    def test_fallback_path_verifies_against_the_resolved_load_addr(self, tmp_path) -> None:
        """With no recipe, the script hpx built is the authority instead."""
        binary = _bin_only(tmp_path)
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script",
            return_value=SimpleNamespace(
                returncode=0, stdout=self._programmed_at("0x00018000"), stderr=""
            ),
        ):
            flash_binary(binary, device="AMAP42KP-KBR", load_addr=0x00018000)

            with pytest.raises(CaptureError) as exc_info:
                flash_binary(binary, device="AP510NFA-CBR", load_addr=0x00410000)

        message = str(exc_info.value)
        assert "0x00410000" in message
        assert "0x00018000" in message


class TestFlashRecipeValidation(_FlashRecipeFixtures):
    """hpx runs NSX's recipe verbatim, so it must vet it the way NSX does.

    ``validate_flash_recipe`` in ``neuralspotx.operations._hardware`` refuses a
    recipe that loads the wrong artifact or omits ``ExitOnError 1``; hpx checked
    neither on this path.  Recipes bake ABSOLUTE paths, so a stale one resolves
    happily and flashes an older image while hpx reports the new build (#150).
    """

    @staticmethod
    def _refuse(binary: Path) -> CaptureError:
        """Flash and expect a refusal that never reached JLinkExe.

        Every refusal on this path shares one invariant, asserted here so it
        holds for all of them rather than only whichever ones a later test
        remembers: the user is TOLD the board was left alone.  "Refused" and
        "failed halfway through programming" call for opposite next steps, and
        ``run.assert_not_called()`` proves it to the test suite but not to the
        person reading the error.
        """
        with patch("helia_profiler.target.probe.flash.run_jlink_script") as run:
            with pytest.raises(CaptureError) as exc_info:
                flash_binary(binary, device="AP510NFA-CBR", load_addr=None)
        run.assert_not_called()
        assert "Nothing was programmed" in str(exc_info.value)
        return exc_info.value

    def test_a_recipe_loading_another_build_is_refused(self, tmp_path) -> None:
        stale = tmp_path / "previous-build"
        stale.mkdir()
        (stale / "hpx_profiler_power.bin").write_bytes(b"\x00")
        binary = self._build(
            tmp_path,
            recipe=(
                "ExitOnError 1\nReset\n"
                f'LoadFile "{stale / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

        message = str(self._refuse(binary))
        assert str(stale / "hpx_profiler_power.bin") in message
        assert str(tmp_path / "hpx_profiler_power.bin") in message

    def test_an_unquoted_relative_loadfile_resolves_against_the_recipe(self, tmp_path) -> None:
        """NSX's regex accepts both forms; relative paths are recipe-relative.

        ``hpx_profiler_power.bin`` next to the recipe is NOT the build dir's
        image, so the same-basename near-miss must still be caught.
        """
        binary = self._build(
            tmp_path,
            recipe="ExitOnError 1\nLoadFile hpx_profiler_power.bin, 0x00410000\nExit\n",
        )
        assert "jlink" in str(self._refuse(binary))

        # ...while the relative form that does point at the build dir passes.
        (tmp_path / "jlink" / "hpx_profiler_power" / "flash_cmds.jlink").write_text(
            "ExitOnError 1\nLoadFile ../../hpx_profiler_power.bin, 0x00410000\nExit\n"
        )
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script",
            return_value=SimpleNamespace(returncode=0, stdout=self._PROGRAMMED, stderr=""),
        ):
            flash_binary(binary, device="AP510NFA-CBR", load_addr=None)

    def test_a_case_only_path_difference_is_not_a_stale_recipe(self, tmp_path) -> None:
        """Same file, different spelling — accepted, and on every platform alike.

        ``Path.resolve()`` compares path TEXT.  It does not case-fold on POSIX,
        but on Windows it canonicalises the case of a path that exists, so a
        resolve-equality check answers this recipe differently depending on the
        host: refused as stale on macOS/Linux, accepted on Windows.  One of
        those is a spurious hard refusal of a correct flash, and a gate that
        disagrees with itself across platforms is the worse half of the bug.

        Run against the real filesystem, because the whole question is what the
        OS considers one file; a mock would only re-assert the answer.  On a
        case-sensitive volume the two names ARE different files and refusing is
        right, so this skips there rather than pinning the wrong expectation.
        """
        self._build(tmp_path)
        recased = tmp_path / "HPX_Profiler_Power.BIN"
        if not recased.exists():
            pytest.skip("case-sensitive volume: the two spellings are genuinely two files")

        self._flash(
            tmp_path,
            self._PROGRAMMED,
            recipe=f'ExitOnError 1\nReset\nLoadFile "{recased}", 0x00410000\nReset\nGo\nExit\n',
        )

    def test_a_recipe_naming_an_absent_image_is_still_refused_as_stale(self, tmp_path) -> None:
        """Widening to stat identity must not cost the stale-recipe refusal.

        ``samefile`` answers the case question correctly but raises when either
        path is missing — which is the commonest stale recipe of all, one left
        behind by a build directory that has since been cleaned.  Swallowing
        that into anything other than "not this build's image" would let a
        recipe pointing at a deleted artifact through to JLinkExe.
        """
        binary = self._build(
            tmp_path,
            recipe=(
                "ExitOnError 1\nReset\n"
                f'LoadFile "{tmp_path / "previous-build" / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

        message = str(self._refuse(binary))
        assert "not this build's image" in message
        assert str(tmp_path / "previous-build" / "hpx_profiler_power.bin") in message

    def test_a_recipe_without_exit_on_error_is_refused(self, tmp_path) -> None:
        binary = self._build(
            tmp_path,
            recipe=(
                "Reset\n"
                f'LoadFile "{tmp_path / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

        assert "ExitOnError 1" in str(self._refuse(binary))

    def test_a_commented_exit_on_error_still_counts(self, tmp_path) -> None:
        """J-Link Commander tolerates trailing `//` comments; so must we."""
        self._flash(
            tmp_path,
            self._PROGRAMMED,
            recipe=(
                "ExitOnError 1  // fail fast\nReset\n"
                f'LoadFile "{tmp_path / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

    def test_exit_on_error_after_the_loadfile_it_protects_is_refused(self, tmp_path) -> None:
        """Presence is not enough; J-Link runs the recipe in order.

        A position-independent search accepts this recipe, yet the ``LoadFile``
        runs before fail-fast is ever armed — so JLinkExe can fail the flash
        and still exit zero, which is the exact outcome the directive is
        demanded for.  The guard would be passing the check meant to enforce
        it.  Hand-edited recipes are in scope for this module, and a
        misordered directive is precisely what a hand-edit produces; NSX's
        generated recipes always lead with it.
        """
        binary = self._build(
            tmp_path,
            recipe=(
                "Reset\n"
                f'LoadFile "{tmp_path / "hpx_profiler_power.bin"}", 0x00410000\n'
                "ExitOnError 1\nReset\nGo\nExit\n"
            ),
        )

        error = self._refuse(binary)
        message = str(error)
        assert "ExitOnError 1" in message
        assert "LoadFile" in message
        # The complaint has to be about ORDER.  The directive IS in the file,
        # so reusing the "missing" wording would send the user hunting for a
        # line already sitting in front of them.
        assert "missing" not in message.lower()
        assert "Move `ExitOnError 1` above the first `LoadFile`" in (error.hint or "")

    def test_exit_on_error_need_not_be_the_recipes_very_first_directive(self, tmp_path) -> None:
        """The rule is "before the first LoadFile", not "before everything".

        Both rules accept every recipe NSX generates, so this is the only case
        that tells them apart — and it is a working recipe.  A ``Reset``
        preamble is legal JLinkExe, and fail-fast is armed before anything
        programs flash, so the flash is fully protected.  Demanding the
        directive be the literal first line would refuse it: a new hard
        refusal of a recipe that flashes correctly, which is the regression
        this module already declined to risk when it widened the accepted
        ``LoadFile`` shapes.
        """
        self._flash(
            tmp_path,
            self._PROGRAMMED,
            recipe=(
                "Reset\nExitOnError 1\n"
                f'LoadFile "{tmp_path / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

    def test_a_recipe_without_a_loadfile_is_refused(self, tmp_path) -> None:
        """A recipe with nothing to load has no address to verify; refuse it.

        Kept distinct from the addressless-``LoadFile`` refusal below, which
        names a ``LoadFile`` the user can see in front of them and so needs a
        different message.  The stated reason here is deliberately the one that
        is true of every recipe reaching this branch -- see the ``LoadBin`` test
        below for why "programs nothing" is not.
        """
        binary = self._build(tmp_path, recipe="ExitOnError 1\nReset\nGo\nExit\n")

        message = str(self._refuse(binary))
        assert "no `LoadFile` command at all" in message
        assert "no address to verify" in message

    def test_a_recipe_programming_by_another_directive_is_not_told_it_programs_nothing(
        self, tmp_path
    ) -> None:
        """``LoadFile`` is not J-Link's only way to write flash.

        This branch fires on the absence of ``LoadFile``, and it used to report
        that absence as "so it programs nothing".  ``LoadBin "<image>",
        0x410000`` programs flash and is not a ``LoadFile``, so a hand-edited
        recipe using it lands here and is told, falsely, that it flashes
        nothing -- then sent hunting for a load command that is right there.  A
        ``w4`` sequence writes flash the same way with even less to recognise.

        The same defect class as the addressless-``LoadFile`` message below, one
        command over: a refusal asserting more than the check actually
        established.  Refusing is still correct -- hpx has no address to verify
        a flash against either way, and guessing is what #150 is about -- so
        only the false clause goes.

        Widening ``_ANY_LOAD_FILE_RE`` to recognise these instead is
        deliberately NOT the fix: it would grow the J-Link grammar this module
        has to track for no gain, since NSX emits neither directive.
        """
        bin_path = tmp_path / "hpx_profiler_power.bin"
        binary = self._build(
            tmp_path, recipe=f'ExitOnError 1\nLoadBin "{bin_path}", 0x00410000\nExit\n'
        )

        message = str(self._refuse(binary))
        # The refusal stays (fail-closed), and keeps naming what it looked for.
        assert "no `LoadFile` command at all" in message
        # ...but must no longer claim to know that nothing gets programmed.
        assert "programs nothing" not in message
        assert "no address to verify" in message

    def test_a_recipe_without_this_builds_image_is_refused(self, tmp_path) -> None:
        """The recipe branch never checked .bin existence; only the fallback did."""
        binary = self._build(tmp_path)
        binary.with_suffix(".bin").unlink()

        assert "hpx_profiler_power.bin" in str(self._refuse(binary))

    def test_the_exit_on_error_hint_acknowledges_a_hand_edited_recipe(self, tmp_path) -> None:
        """Telling the user only to re-run the build discards a deliberate edit.

        Hand-edited recipes are in scope for this whole module (see the
        ``LoadFile`` shapes below), and regenerating from NSX throws those edits
        away.  The hint must offer the one-line fix first and name the cost of
        the rebuild.
        """
        binary = self._build(
            tmp_path,
            recipe=(
                "Reset\n"
                f'LoadFile "{tmp_path / "hpx_profiler_power.bin"}", 0x00410000\n'
                "Reset\nGo\nExit\n"
            ),
        )

        hint = self._refuse(binary).hint or ""
        assert "hand-edited" in hint
        assert "ExitOnError 1" in hint
        assert hint.index("ExitOnError 1") < hint.index("re-running the build")

    @pytest.mark.parametrize(
        "tail",
        [
            "",
            ", noreset",
            ", reset",
            " // the app",
            ", noreset // the app",
        ],
        ids=["bare", "noreset", "reset", "comment", "noreset-and-comment"],
    )
    def test_every_loadfile_form_jlink_accepts_is_accepted(self, tmp_path, tail) -> None:
        """JLinkExe's grammar, not NSX's regex, decides what hpx may refuse.

        Its embedded help reads ``loadfile <filename> [, <Addr>] [, <noreset |
        reset>]`` and the commander strips trailing ``//`` comments from every
        command line, so all of these run correctly on hardware and all of them
        flashed fine before this check existed.  NSX's ``_LOAD_FILE_RE``, which
        this grammar was ported from, anchors the address at end-of-line and
        rejects the last four -- NSX only ever reads recipes it generated
        itself, so it never pays for that.  hpx puts hand-edited recipes in
        scope, which is exactly where these forms turn up, so importing the gap
        along with the grammar would have made this a new hard refusal of
        recipes that work.
        """
        bin_path = tmp_path / "hpx_profiler_power.bin"
        self._flash(
            tmp_path,
            self._PROGRAMMED,
            recipe=f'ExitOnError 1\nReset\nLoadFile "{bin_path}", 0x00410000{tail}\nReset\nGo\nExit\n',
        )

    def test_a_tolerated_tail_still_yields_the_recipes_address(self, tmp_path) -> None:
        """Accepting the new forms must not turn them into unverified ones.

        The shapes above only prove the recipe is no longer refused.  J-Link's
        grammar makes the address itself optional (``loadfile <filename> [,
        <Addr>] [, <noreset | reset>]``), so the obvious next loosening is to
        make the address group optional too -- at which point a tail-bearing
        recipe parses, flashes, and is compared against nothing.  Here the
        recipe says 0x00410000 and J-Link reports a different bank, so anything
        short of "the recipe's address was extracted and used" passes silently.
        """
        bin_path = tmp_path / "hpx_profiler_power.bin"
        recipe = f'ExitOnError 1\nLoadFile "{bin_path}", 0x00410000, noreset\nExit\n'

        with pytest.raises(CaptureError) as exc_info:
            self._flash(tmp_path, self._programmed_at("0x00018000"), recipe=recipe)

        assert "0x00410000" in str(exc_info.value)

    def test_a_loadfile_without_an_address_is_refused_not_guessed(self, tmp_path) -> None:
        """J-Link's grammar makes the address optional; hpx's verification cannot.

        ``loadfile <filename>`` with no address is legal (the destination comes
        from the image format), and it names this build's own ``.bin`` here, so
        nothing about the path is wrong.  hpx still has no address to compare a
        bank against, and the only safe answer is to refuse.  Loosening the
        address group to optional instead -- the natural companion to tolerating
        the reset/comment tails above -- would carry ``None`` into the
        comparison rather than refusing.

        Refusing is right; the stated REASON has to be right too.  This recipe
        DOES program flash -- ``_ANY_LOAD_FILE_RE``'s own comment says exactly
        that about exactly this line, and so does the paragraph above -- so
        telling the user "a recipe that loads nothing programs nothing" asserts
        the opposite of what the module knows, and sends them hunting for a
        missing ``LoadFile`` that is sitting in front of them.  The true reason
        to refuse is that hpx cannot check WHERE it lands, not that nothing does.
        """
        bin_path = tmp_path / "hpx_profiler_power.bin"
        binary = self._build(tmp_path, recipe=f'ExitOnError 1\nLoadFile "{bin_path}"\nExit\n')

        error = self._refuse(binary)
        message = str(error)
        assert "LoadFile" in message
        # The false clause must be gone, and the accurate reason present.
        assert "loads nothing" not in message
        assert "cannot check" in message
        # A deliberate hand-edit is how this recipe comes to exist at all, so a
        # bare "re-run the build" would tell the user to discard it in silence.
        assert "hand-edited" in (error.hint or "")

    def test_a_utf8_recipe_is_read_as_utf8_not_the_locale_codec(self, tmp_path) -> None:
        """``read_text()`` without an encoding is cp1252 on Windows, and CI runs Windows.

        NSX writes the recipe as UTF-8.  Decoded as cp1252 a non-ASCII build
        path becomes mojibake, the baked path stops matching this build's
        ``.bin``, and a correct flash is refused as a stale recipe.  Before this
        module gated the flash a mis-decode only corrupted the text piped to
        JLinkExe; now it decides whether the flash runs at all.
        """
        build_dir = tmp_path / "café-build"
        build_dir.mkdir()
        binary = self._build(build_dir)

        self._flash(build_dir, self._PROGRAMMED)
        assert "café" in str(binary)

    def test_a_mis_encoded_recipe_is_refused_not_raised_as_an_internal_error(
        self, tmp_path
    ) -> None:
        """Demanding UTF-8 turns a mis-encoded recipe into a raise; type it.

        Reading the recipe as UTF-8 is correct -- NSX writes it that way -- but
        it also converts a mis-encoded file from silent mojibake into a raised
        ``UnicodeDecodeError``, and that is not this module's contract with its
        caller.  ``FlashPowerFirmwareStage`` catches ``CaptureError`` only, so
        an untyped escape surfaces to the user as "Unexpected error in stage
        'flash_power_firmware' ... likely a bug in heliaPROFILER. Please file an
        issue" for what is a mis-encoded file on their own disk, and slips past
        that stage's power-cycle retry on the way.  The same commit added
        exactly this conversion for the unresolvable-path case a few lines
        below; this is the same class of problem.

        Written with real cp1252 BYTES because the bug is in the decode: a
        ``str`` fixture would be re-encoded as UTF-8 on write and never fail.
        On POSIX this is pre-existing (``read_text`` with no encoding raised
        here too), but stating the codec newly exposes it on Windows, where
        cp1252 is the default and used to decode this file without complaint.
        """
        binary = self._build(tmp_path)
        recipe = tmp_path / "jlink" / "hpx_profiler_power" / "flash_cmds.jlink"
        # A fixed literal rather than an interpolated ``tmp_path``: the decode
        # fails before any path is looked at, so the baked path is irrelevant to
        # what is under test, and interpolating one would make the fixture
        # depend on whether the runner's temp directory happens to be
        # cp1252-encodable.  "é" is 0xE9 in cp1252 -- a lone continuation byte
        # that UTF-8 must reject.
        recipe.write_bytes(
            'ExitOnError 1\nLoadFile "café.bin", 0x00410000\nExit\n'.encode("cp1252")
        )

        # ``_refuse`` also pins that JLinkExe was never invoked and that the
        # message says the board was left alone -- both of which a
        # ``UnicodeDecodeError`` escaping the function satisfies neither of.
        error = self._refuse(binary)
        assert "not valid UTF-8" in str(error)
        assert "UTF-8" in (error.hint or "")

    def test_the_recipe_encoding_is_stated_rather_than_inherited(self, tmp_path) -> None:
        """Pin the argument, since this host's default already happens to be UTF-8.

        The functional test above only fails where the locale codec differs
        from UTF-8, so on macOS/Linux it would stay green with the encoding
        dropped.  This one fails everywhere.
        """
        binary = self._build(tmp_path)
        recipe = (tmp_path / "jlink" / "hpx_profiler_power" / "flash_cmds.jlink").read_text(
            encoding="utf-8"
        )
        with (
            patch.object(Path, "read_text", autospec=True, return_value=recipe) as read_text,
            patch(
                "helia_profiler.target.probe.flash.run_jlink_script",
                return_value=SimpleNamespace(returncode=0, stdout=self._PROGRAMMED, stderr=""),
            ),
        ):
            flash_binary(binary, device="AP510NFA-CBR", load_addr=None)

        assert read_text.call_args.kwargs.get("encoding") == "utf-8"

    def test_an_unreadable_recipe_is_refused_not_raised_as_an_internal_error(
        self, tmp_path
    ) -> None:
        """``is_file()`` passing does not make the read succeed.

        The mis-encode above is not the only way this ``read_text`` fails.  The
        recipe can be permission-denied, or deleted or renamed in the window
        between the ``is_file()`` check and the read; each raises an ``OSError``
        subclass, which ``except UnicodeDecodeError`` does not catch (it is a
        ``ValueError``).  The consequence is verbatim the one the mis-encode
        conversion exists to prevent: ``FlashPowerFirmwareStage`` catches
        ``CaptureError`` only, so it surfaces as "likely a bug in
        heliaPROFILER ... please file an issue" for a permissions problem on the
        user's own disk, and skips that stage's power-cycle retry on the way.

        Real permission bits rather than a mock, so this pins the actual
        ``chmod 000`` reproduction.  It self-skips wherever they are not
        enforced -- as root, and on Windows, where ``os.chmod`` moves only the
        read-only bit and leaves reads working -- by checking whether the file
        it just made unreadable is in fact unreadable.  The mocked test below
        covers the conversion on those platforms.
        """
        binary = self._build(tmp_path)
        recipe = tmp_path / "jlink" / "hpx_profiler_power" / "flash_cmds.jlink"
        recipe.chmod(0o000)
        try:
            try:
                recipe.read_text(encoding="utf-8")
            except OSError:
                pass
            else:
                pytest.skip("permission bits are not enforced here (root, or Windows)")

            # Still a file as far as the branch's own guard is concerned: that
            # is what makes this reachable rather than the missing-recipe path.
            assert recipe.is_file()

            error = self._refuse(binary)
            message = str(error)
            assert "cannot be read" in message
            # A permission error is not a mis-encode, and must not be diagnosed
            # as one -- "re-save it as UTF-8" is unactionable on a file the user
            # cannot open in the first place.
            assert "is not valid UTF-8" not in message
            assert "readable" in (error.hint or "")
        finally:
            # Restore before teardown so the tmp_path cleanup is unencumbered.
            recipe.chmod(0o644)

    def test_a_recipe_that_vanishes_after_the_is_file_check_is_refused(self, tmp_path) -> None:
        """The same conversion, pinned on every platform including Windows.

        The permission test above self-skips where the bits are unenforced, so
        on the Windows runners it proves nothing.  This one drives the identical
        code path through the other real cause -- the recipe going away between
        ``is_file()`` and ``read_text`` -- and fails everywhere if the ``OSError``
        arm is dropped.
        """
        binary = self._build(tmp_path)
        recipe = tmp_path / "jlink" / "hpx_profiler_power" / "flash_cmds.jlink"
        # Patched rather than raced for real: the window is genuinely there, but
        # hitting it from a test would make this flaky rather than a pin.
        with patch.object(
            Path, "read_text", autospec=True, side_effect=FileNotFoundError(2, "No such file")
        ):
            error = self._refuse(binary)

        assert "cannot be read" in str(error)
        assert str(recipe) in str(error)

    def test_an_unresolvable_loadfile_path_never_escapes_as_an_untyped_error(
        self, tmp_path
    ) -> None:
        """``[^"]+`` captures a NUL, and recipe text is arbitrary.

        On POSIX -- and on Windows through 3.12 -- ``Path.resolve()`` raises
        ``ValueError: embedded null character`` here.  Unconverted that escapes
        a flash helper as an untyped internal error, past every caller that
        catches this module's ``CaptureError``, including
        ``FlashPowerFirmwareStage``'s power-cycle retry.  Windows' illegal
        path characters raise ``OSError`` the same way.

        On Windows 3.13+ ``ntpath.realpath`` swallows it instead and hands the
        path straight back, so the NUL path simply fails to match this build's
        image and the stale-recipe refusal fires.  Both outcomes are refusals
        that programmed nothing, which is what ``_refuse`` checks; asserting
        WHICH of the two messages appears would fail on one platform or the
        other, and the platform split is not the property worth pinning.
        """
        binary = self._build(
            tmp_path,
            recipe='ExitOnError 1\nLoadFile "bad\x00path.bin", 0x00410000\nExit\n',
        )

        # Naming the recipe is common to both refusals -- and is what the user
        # needs either way, since the offending line is in that file.
        assert "flash_cmds.jlink" in str(self._refuse(binary))

    def test_the_fallback_refusals_also_say_nothing_was_programmed(self, tmp_path) -> None:
        """The two refusals that never reach ``_refuse``'s shared assertion.

        Both fire on the no-recipe branch, so a user hitting them has even less
        context for whether the board was touched.
        """
        no_image = tmp_path / "hpx_profiler_power"
        no_image.write_bytes(b"\x00")  # ELF only; no .bin, no recipe
        built = tmp_path / "built"
        built.mkdir()
        with patch("helia_profiler.target.probe.flash.run_jlink_script") as run:
            with pytest.raises(CaptureError) as no_image_exc:
                flash_binary(no_image, device="AP510NFA-CBR", load_addr=0x00410000)
            with pytest.raises(CaptureError) as no_addr_exc:
                flash_binary(
                    _bin_only(built),
                    device="SOME-NEW-PART",
                    load_addr=None,
                )
        run.assert_not_called()

        assert "Nothing was programmed" in str(no_image_exc.value)
        assert "Nothing was programmed" in str(no_addr_exc.value)


def test_the_board_was_left_alone_phrase_is_written_once_not_respelled() -> None:
    """Make the invariant the refusal tests assert visible in the source.

    ``_refuse`` and the test above pin "Nothing was programmed" across every
    refusal, but a substring assertion cannot see WHICH spelling each site
    used.  Eight sites had drifted into three (em-dash, colon, "this" versus
    "the recipe") while every one of those assertions stayed green, and the
    next reword could as easily have pushed one out from under them.

    Counting the literal phrase is what makes the drift impossible rather than
    merely currently-absent: a new refusal that respells it inline instead of
    reusing a constant fails here, at the site of the mistake.  Two constants,
    not one, because the fallback branch is reached precisely BECAUSE the
    recipe is missing -- "the recipe was refused" would be false there.
    """
    from helia_profiler.target.probe import flash as flash_module

    source = Path(flash_module.__file__).read_text(encoding="utf-8")

    assert source.count("Nothing was programmed") == 2
    assert flash_module._NOTHING_PROGRAMMED_RECIPE.startswith("Nothing was programmed")
    assert flash_module._NOTHING_PROGRAMMED.startswith("Nothing was programmed")
    # The two must stay distinguishable; collapsing them into one would put the
    # recipe wording on the branch that has no recipe.
    assert flash_module._NOTHING_PROGRAMMED != flash_module._NOTHING_PROGRAMMED_RECIPE
