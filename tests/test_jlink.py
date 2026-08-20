"""Tests for J-Link probe enumeration and selection."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helia_profiler.errors import CaptureError, ConfigError
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
    def _bin_only(tmp_path: Path) -> Path:
        """A built target with a .bin sibling but no NSX flash script."""
        binary = tmp_path / "hpx_profiler_power"
        binary.write_bytes(b"\x00")
        binary.with_suffix(".bin").write_bytes(b"\x00")
        return binary

    @staticmethod
    def _flash(binary: Path, *, device: str, load_addr: int | None) -> str:
        """Run the fallback against a stubbed JLinkExe; return the script."""
        ok = SimpleNamespace(returncode=0, stdout="Flash download: Total 1 range", stderr="")
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script", return_value=ok
        ) as run:
            flash_binary(binary, device=device, load_addr=load_addr)
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

        binary = self._bin_only(tmp_path)
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
        binary = self._bin_only(tmp_path)
        script = self._flash(binary, device="AMA3B2KK-KBR", load_addr=0)

        assert '", 0x00000000\n' in script

    def test_probe_settings_are_forwarded_to_the_jlink_run(self, tmp_path: Path) -> None:
        """The script is only half the recipe -- the probe kwargs matter too.

        Asserting script text alone lets ``device`` (or the serial, which
        selects among several attached probes) be hardcoded or dropped while
        every other test stays green, sending the flash to the wrong target.
        """
        ok = SimpleNamespace(returncode=0, stdout="Flash download: Total 1 range", stderr="")
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script", return_value=ok
        ) as run:
            flash_binary(
                self._bin_only(tmp_path),
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

        with pytest.raises(CaptureError) as exc_info:
            self._flash(binary, device="AMA3B2KK-KBR", load_addr=0x00018000)

        message = str(exc_info.value)
        assert "flash_cmds.jlink" in message
        assert "hpx_profiler_power.bin" in message

    def test_unknown_load_address_raises_naming_recipe_and_device(self, tmp_path: Path) -> None:
        with pytest.raises(CaptureError) as exc_info:
            self._flash(self._bin_only(tmp_path), device="SOME-NEW-PART", load_addr=None)

        message = str(exc_info.value)
        assert "flash_cmds.jlink" in message
        assert "SOME-NEW-PART" in message
        assert "app_flash_load_addr" in (exc_info.value.hint or "")

    def test_generated_script_is_used_verbatim_and_ignores_load_addr(self, tmp_path: Path) -> None:
        binary = self._bin_only(tmp_path)
        script_dir = tmp_path / "jlink" / "hpx_profiler_power"
        script_dir.mkdir(parents=True)
        recipe = "ExitOnError 1\nReset\nLoadFile x.bin, 0xDEADBEEF\nReset\nGo\nExit\n"
        (script_dir / "flash_cmds.jlink").write_text(recipe)

        # The recipe wins whatever load_addr is -- including a resolved one, the
        # only case production actually hits.  Hand-rolling instead of running
        # the generated recipe is the regression the docstring warns about.
        assert self._flash(binary, device="AMA3B2KK-KBR", load_addr=0x00018000) == recipe
        # load_addr=None must not raise when the generated recipe exists.
        assert self._flash(binary, device="AMA3B2KK-KBR", load_addr=None) == recipe


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


class TestFlashBinaryVerification:
    """flash_binary must demand explicit flash evidence, not a bare connection O.K.

    Command failures are already gated by exit status (``ExitOnError 1`` in
    every recipe plus ``run_jlink_script``'s CaptureError on nonzero rc);
    these tests pin the output tripwire that catches recipes which connect
    successfully but never issue a program step (issue #101).  All three
    outputs below were captured from real hardware.
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

    def _flash(self, tmp_path, output: str) -> None:
        binary = tmp_path / "hpx_profiler_power"
        script_dir = tmp_path / "jlink" / "hpx_profiler_power"
        script_dir.mkdir(parents=True)
        (script_dir / "flash_cmds.jlink").write_text(
            "ExitOnError 1\nLoadFile hpx_profiler_power.bin, 0x00410000\nExit\n"
        )
        with patch(
            "helia_profiler.target.probe.flash.run_jlink_script",
            return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
        ):
            # These exercise the NSX-recipe path, which needs no load address.
            flash_binary(
                binary, device="AP510NFA-CBR", load_addr=None, jlink_serial="1160002204"
            )

    def test_programmed_output_verifies(self, tmp_path) -> None:
        self._flash(tmp_path, self._PROGRAMMED)

    def test_skipped_identical_output_verifies(self, tmp_path) -> None:
        self._flash(tmp_path, self._SKIPPED_IDENTICAL)

    def test_connection_only_ok_is_rejected(self, tmp_path) -> None:
        with pytest.raises(CaptureError, match="no recognized flash confirmation"):
            self._flash(tmp_path, self._CONNECTION_ONLY)
