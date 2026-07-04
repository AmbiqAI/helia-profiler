"""Tests for :class:`EnsureBoardPoweredStage` (driver-agnostic shell) and
the Joulescope-specific decision matrix that lives on the driver.

The stage is intentionally thin: it instantiates the configured driver and
delegates the whole \"power the board on\" decision to
:meth:`PowerDriver.ensure_target_powered`. The vendor-specific behavior
(enumeration, multi-device handling, serial matching) is exercised
directly on :class:`JoulescopeDriver`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import PowerError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.joulescope_driver import JoulescopeDriver
from helia_profiler.stages.ensure_powered import EnsureBoardPoweredStage


def _ctx(tmp_path: Path, power: dict | None = None) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    overrides: dict = {
        "model": {"path": str(model)},
        "engine": {"type": "helia-rt"},
    }
    if power is not None:
        overrides["power"] = power
    config = load_config(None, overrides)
    return PipelineContext(config=config, work_dir=tmp_path)


# ---------------------------------------------------------------------------
# Stage-level: it should be a thin shell that just delegates.
# ---------------------------------------------------------------------------


class TestShouldSkip:
    def test_skipped_by_default_without_power_capture(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        assert EnsureBoardPoweredStage().should_skip(ctx) is True

    def test_not_skipped_when_power_enabled(self, tmp_path: Path):
        ctx = _ctx(tmp_path, power={"enabled": True})
        assert EnsureBoardPoweredStage().should_skip(ctx) is False

    def test_not_skipped_when_ensure_board_powered_opted_in(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        ctx.config = load_config(
            None,
            {
                "model": {"path": str(ctx.config.model.path)},
                "engine": {"type": "helia-rt"},
                "target": {"ensure_board_powered": True},
            },
        )
        assert EnsureBoardPoweredStage().should_skip(ctx) is False


class TestStageDelegation:
    def test_delegates_to_driver_and_records_success(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        driver = MagicMock()
        driver.ensure_target_powered.return_value = True
        with patch("helia_profiler.power.get_driver", return_value=driver):
            EnsureBoardPoweredStage().run(ctx)
        driver.ensure_target_powered.assert_called_once_with(required=False)
        assert ctx.passthrough_skipped is False

    def test_delegates_to_driver_and_records_skip(self, tmp_path: Path):
        ctx = _ctx(tmp_path)
        driver = MagicMock()
        driver.ensure_target_powered.return_value = False
        with patch("helia_profiler.power.get_driver", return_value=driver):
            EnsureBoardPoweredStage().run(ctx)
        assert ctx.passthrough_skipped is True

    def test_strict_mode_passes_required_true(self, tmp_path: Path):
        ctx = _ctx(tmp_path, power={"enabled": True})
        driver = MagicMock()
        driver.ensure_target_powered.return_value = True
        with patch("helia_profiler.power.get_driver", return_value=driver):
            EnsureBoardPoweredStage().run(ctx)
        driver.ensure_target_powered.assert_called_once_with(required=True)

    def test_strict_mode_propagates_driver_error(self, tmp_path: Path):
        ctx = _ctx(tmp_path, power={"enabled": True})
        driver = MagicMock()
        driver.ensure_target_powered.side_effect = PowerError("no device")
        with patch("helia_profiler.power.get_driver", return_value=driver):
            with pytest.raises(PowerError, match="no device"):
                EnsureBoardPoweredStage().run(ctx)


# ---------------------------------------------------------------------------
# Driver-level: full Joulescope decision matrix.
# ---------------------------------------------------------------------------


def _patch_devices(devices):
    return patch(
        "helia_profiler.power.joulescope_driver.enumerate_devices",
        return_value=devices,
    )


class TestJoulescopeEnsureTargetPowered_BestEffort:
    """``required=False`` — skip + return False on any ambiguity."""

    def test_driver_missing_returns_false(self):
        driver = JoulescopeDriver()
        with patch.object(driver, "check_available", side_effect=PowerError("not installed")):
            assert driver.ensure_target_powered(required=False) is False

    def test_zero_devices_returns_false(self):
        driver = JoulescopeDriver()
        with patch.object(driver, "check_available"), _patch_devices([]):
            assert driver.ensure_target_powered(required=False) is False

    def test_one_device_enables_and_returns_true(self):
        driver = JoulescopeDriver()
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000123", "js220")]),
            patch.object(driver, "enable_passthrough") as enable,
            patch.object(driver, "disable_passthrough") as disable,
        ):
            assert driver.ensure_target_powered(required=False) is True
        enable.assert_called_once()
        disable.assert_called_once()

    def test_multi_devices_no_serial_returns_false(self):
        driver = JoulescopeDriver()
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000111", "js220"), ("u/js110/000222", "js110")]),
            patch.object(driver, "enable_passthrough") as enable,
        ):
            assert driver.ensure_target_powered(required=False) is False
        enable.assert_not_called()

    def test_multi_devices_with_matching_serial_succeeds(self):
        driver = JoulescopeDriver(serial="000222")
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000111", "js220"), ("u/js110/000222", "js110")]),
            patch.object(driver, "enable_passthrough"),
            patch.object(driver, "disable_passthrough"),
        ):
            assert driver.ensure_target_powered(required=False) is True

    def test_serial_no_match_returns_false(self):
        driver = JoulescopeDriver(serial="999999")
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000111", "js220")]),
            patch.object(driver, "enable_passthrough") as enable,
        ):
            assert driver.ensure_target_powered(required=False) is False
        enable.assert_not_called()

    def test_passthrough_failure_returns_false(self):
        driver = JoulescopeDriver()
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000123", "js220")]),
            patch.object(driver, "enable_passthrough", side_effect=PowerError("relay")),
        ):
            assert driver.ensure_target_powered(required=False) is False


class TestJoulescopeEnsureTargetPowered_Strict:
    """``required=True`` — raise on any failure."""

    def test_driver_missing_raises(self):
        driver = JoulescopeDriver()
        with patch.object(driver, "check_available", side_effect=PowerError("not installed")):
            with pytest.raises(PowerError, match="not installed"):
                driver.ensure_target_powered(required=True)

    def test_zero_devices_raises(self):
        driver = JoulescopeDriver()
        with patch.object(driver, "check_available"), _patch_devices([]):
            with pytest.raises(PowerError, match="No Joulescope"):
                driver.ensure_target_powered(required=True)

    def test_multi_devices_no_serial_raises(self):
        driver = JoulescopeDriver()
        with (
            patch.object(driver, "check_available"),
            _patch_devices([("u/js220/000111", "js220"), ("u/js110/000222", "js110")]),
        ):
            with pytest.raises(PowerError, match="disambiguate"):
                driver.ensure_target_powered(required=True)

    def test_serial_no_match_raises(self):
        driver = JoulescopeDriver(serial="999")
        with patch.object(driver, "check_available"), _patch_devices([("u/js220/000111", "js220")]):
            with pytest.raises(PowerError, match="not found"):
                driver.ensure_target_powered(required=True)
