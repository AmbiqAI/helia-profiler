"""Tests for J-Link probe enumeration and selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.target.probe.jlink import (
    JLinkProbe,
    JLinkProbeMatch,
    attached_session,
    flash_binary,
    inspect_probe_target,
    find_jlink_exe,
    list_connected_probes,
    resolve_probe_serial,
)
from helia_profiler.platform import CoreArch


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
            "helia_profiler.target.probe.jlink.run_jlink_script",
            return_value=SimpleNamespace(returncode=0, stdout=output, stderr=""),
        ):
            flash_binary(binary, device="AP510NFA-CBR", jlink_serial="1160002204")

    def test_programmed_output_verifies(self, tmp_path) -> None:
        self._flash(tmp_path, self._PROGRAMMED)

    def test_skipped_identical_output_verifies(self, tmp_path) -> None:
        self._flash(tmp_path, self._SKIPPED_IDENTICAL)

    def test_connection_only_ok_is_rejected(self, tmp_path) -> None:
        with pytest.raises(CaptureError, match="no recognized flash confirmation"):
            self._flash(tmp_path, self._CONNECTION_ONLY)
