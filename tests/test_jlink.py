"""Tests for J-Link probe enumeration and selection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from helia_profiler.errors import ConfigError
from helia_profiler.jlink import (
    JLinkProbe,
    JLinkProbeMatch,
    inspect_probe_target,
    resolve_probe_serial,
)
from helia_profiler.platform import CoreArch


def _probe(serial: str, product: str = "J-Link") -> JLinkProbe:
    return JLinkProbe(serial=serial, product=product)


def _match(serial: str, core: CoreArch | None, product: str = "J-Link") -> JLinkProbeMatch:
    return JLinkProbeMatch(probe=_probe(serial, product), detected_core=core)


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
