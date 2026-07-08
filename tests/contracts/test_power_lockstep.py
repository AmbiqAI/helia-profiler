"""Contract: power capture lock-step ordering and rail-cycle discipline.

Two invariants from the transport-hardening baseline:

1. **Arm before reset.** When lock-step sync is enabled, the host arms the
   sync controller (holds GO low) *before* the lifecycle reset that starts the
   measured run.  Reversing this races a fast-booting firmware past the READY
   barrier before the host is observing, which manifests later as a missing
   power gate.

2. **``auto`` never cycles the rail.** The default/``auto`` reset policy uses
   debug/SWPOI reset primitives only.  Instrument rail power-cycling happens
   *exclusively* through explicit paths: the ``power_cycle`` reset strategy and
   the flash-recovery bring-up in stage 5.
"""

from __future__ import annotations

import pytest

from helia_profiler.capture import capture_power
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.power.sync import DeviceState
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.target.lifecycle import CapturePhase, prepare_target_for_phase

from .conftest import make_pmu_ctx


def _power_result() -> PowerResult:
    return PowerResult(
        summary=PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.018,
            peak_current_a=0.05,
            energy_j=0.54,
            duration_s=1.0,
            sample_count=10,
        )
    )


class _RecordingSyncController:
    """Sync controller that appends each host action to a shared event log."""

    def __init__(self, events: list[str]) -> None:
        self._events = events

    @property
    def lockstep(self) -> bool:
        return True

    def arm(self) -> None:
        self._events.append("arm")

    def wait_ready(self, *, timeout_s: float) -> bool:
        self._events.append("wait_ready")
        return True

    def signal_go(self) -> None:
        self._events.append("signal_go")

    def release_go(self) -> None:
        self._events.append("release_go")

    def read_state(self) -> DeviceState:  # pragma: no cover - not hit on success
        return DeviceState.READY

    def release(self) -> None:
        self._events.append("release")


class _FakeGatedDriver:
    """Joulescope-like driver whose gated capture invokes the release hook."""

    supports_gated_capture = True

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.power_cycle_calls = 0

    def check_available(self) -> None:
        pass

    def make_sync_controller(self, wiring):
        return _RecordingSyncController(self._events)

    def capture_gated(self, *, on_started=None, **_kwargs) -> PowerResult:
        self._events.append("capture_gated")
        if on_started is not None:
            on_started()
        return _power_result()

    def capture(self, **_kwargs) -> PowerResult:  # pragma: no cover - gated path used
        return _power_result()

    def power_cycle(self, **_kwargs) -> None:
        self.power_cycle_calls += 1


class _FakeRailDriver:
    """Minimal driver used to observe whether the rail is cycled."""

    def __init__(self) -> None:
        self.power_cycle_calls = 0

    def check_available(self) -> None:
        pass

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        self.power_cycle_calls += 1


class TestLockstepArmBeforeReset:
    def test_arm_precedes_lifecycle_reset(self, tmp_path, monkeypatch):
        events: list[str] = []
        driver = _FakeGatedDriver(events)
        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: driver)

        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", transport="rtt",
            power_enabled=True, lockstep=True,
        )
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=5))

        def _prepare_target(_driver, _name):
            events.append("lifecycle_reset")
            return None

        result = capture_power(ctx, prepare_target=_prepare_target)

        assert result is not None
        # The arm must happen before the reset that starts the measured run.
        assert events.index("arm") < events.index("lifecycle_reset")
        # Revised ordering (AP510 combo-reset gate-race fix, 2026-07-05): the
        # GPI poller must be live BEFORE the lifecycle reset, so the reset +
        # READY handshake now happen inside capture_gated's on_started hook:
        #   arm -> capture_gated(poller live) -> reset -> wait_ready -> go.
        # Previously the reset preceded capture_gated, which let a slow
        # multi-step reset strategy race the firmware's gated window past the
        # not-yet-started poller ("gate rose but did not fall").
        assert events == [
            "arm",
            "capture_gated",
            "lifecycle_reset",
            "wait_ready",
            "signal_go",
            "release",
        ]

    def test_go_is_released_only_after_ready(self, tmp_path, monkeypatch):
        events: list[str] = []
        driver = _FakeGatedDriver(events)
        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: driver)
        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", transport="rtt",
            power_enabled=True, lockstep=True,
        )
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=5))
        capture_power(ctx, prepare_target=lambda *_: events.append("lifecycle_reset"))
        assert events.index("wait_ready") < events.index("signal_go")


class TestAutoStrategyNeverCyclesRail:
    @pytest.mark.parametrize("strategy", ["auto", "none", "debug_reset", "swpoi_reset"])
    def test_non_power_cycle_strategies_leave_rail_untouched(
        self, tmp_path, monkeypatch, strategy
    ):
        # J-Link resets are stubbed so only rail cycling is observable.
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", lambda **_k: None)
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target_poi", lambda **_k: None)
        driver = _FakeRailDriver()
        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", power_enabled=True, reset_strategy=strategy
        )
        plan = prepare_target_for_phase(
            ctx, phase=CapturePhase.POWER, power_driver=driver, power_driver_name="joulescope"
        )
        assert driver.power_cycle_calls == 0
        assert plan.power_cycle_attempted is False
        assert plan.power_cycle_succeeded is False
        assert "power_cycle" not in plan.actions

    def test_explicit_power_cycle_strategy_cycles_rail(self, tmp_path, monkeypatch):
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", lambda **_k: None)
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target_poi", lambda **_k: None)
        driver = _FakeRailDriver()
        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", power_enabled=True, reset_strategy="power_cycle"
        )
        plan = prepare_target_for_phase(
            ctx, phase=CapturePhase.POWER, power_driver=driver, power_driver_name="joulescope"
        )
        assert driver.power_cycle_calls == 1
        assert plan.power_cycle_attempted is True
        assert plan.power_cycle_succeeded is True
        assert "power_cycle" in plan.actions


class TestExplicitFlashRecoveryPath:
    def test_flash_recovery_is_the_other_rail_cycle_entry(self, tmp_path, monkeypatch):
        """Stage-5 flash recovery is the only *other* place the rail cycles.

        This is the explicit bring-up/recovery path referenced by the auto-vs-
        rail-cycle invariant: a locked debug domain after a failed flash is
        recovered by power-cycling the Joulescope rail, never by the auto reset
        policy.
        """
        from helia_profiler.stages.flash import _try_power_cycle

        driver = _FakeRailDriver()
        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: driver)
        ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=True)

        assert _try_power_cycle(ctx) is True
        assert driver.power_cycle_calls == 1

    def test_flash_recovery_skipped_when_power_disabled(self, tmp_path):
        from helia_profiler.stages.flash import _try_power_cycle

        ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=False)
        assert _try_power_cycle(ctx) is False
