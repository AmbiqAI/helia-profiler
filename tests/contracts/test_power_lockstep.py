"""Contract: power capture lock-step ordering, defaulting, and rail discipline.

Three invariants:

1. **Arm before reset.** When lock-step sync is enabled, the host arms the
   sync controller (holds GO low) *before* the lifecycle reset that starts the
   measured run.  Reversing this races a fast-booting firmware past the READY
   barrier before the host is observing, which manifests later as a missing
   power gate.

2. **Lock-step defaults ON for every wired board doing gated external
   capture** (issue #114).  The rule used to be SoC-family-gated
   (Apollo5-only), which silently degraded every Apollo3/Apollo4 gated capture
   to ``no_gate_rise`` unless the user hand-set ``power.lockstep: true``.  An
   explicit setting still wins in both directions.

3. **``auto`` never cycles the rail.** The default/``auto`` reset policy uses
   debug/SWPOI reset primitives only.  Instrument rail power-cycling happens
   *exclusively* through explicit paths: the ``power_cycle`` reset strategy and
   the flash-recovery bring-up in stage 5.
"""

from __future__ import annotations

from tests.pipeline_context_helpers import set_profile_result

import pytest

from helia_profiler.results import DeploymentRecord, FirmwareArtifact, PowerRunPlan
from helia_profiler.capture import capture_power
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.power.sync import DeviceState
from helia_profiler.results import FirmwareMeta, PmuResult
from helia_profiler.target.lifecycle import (
    CapturePhase,
    prepare_target_for_phase,
    resolve_power_lockstep,
)

from .conftest import BOARD_FOR_FAMILY, make_pmu_ctx


def _mark_deployed(ctx, tmp_path) -> None:
    binary = tmp_path / "hpx_profiler_power"
    binary.touch()
    artifact = FirmwareArtifact(
        role="power",
        target_name="hpx_profiler_power",
        app_dir=tmp_path,
        build_dir=tmp_path,
        binary_path=binary,
    )
    ctx.publish_power_plan(
        PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=5,
            count_source="configured",
        )
    )
    ctx.publish_power_firmware(artifact)
    ctx.publish_power_deployment(
        DeploymentRecord(
            firmware=artifact,
            target_id=ctx.config.target.board,
            deployed_at="2026-07-18T00:00:00+00:00",
        )
    )


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
            tmp_path,
            board="apollo510_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=True,
        )
        set_profile_result(ctx, PmuResult(meta=FirmwareMeta(clean_infer_count=5)))
        _mark_deployed(ctx, tmp_path)

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
            tmp_path,
            board="apollo510_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=True,
        )
        set_profile_result(ctx, PmuResult(meta=FirmwareMeta(clean_infer_count=5)))
        _mark_deployed(ctx, tmp_path)
        # Recorder stub in place of a real lifecycle plan; its None return is
        # tolerated by this path and irrelevant to the ordering under test.
        capture_power(
            ctx,
            prepare_target=lambda *_: events.append("lifecycle_reset"),  # ty: ignore[invalid-argument-type]
        )
        assert events.index("wait_ready") < events.index("signal_go")


def _all_board_names() -> list[str]:
    from helia_profiler.platform import list_boards

    return [board.name for board in list_boards()]


def _board_is_wired_for_lockstep(board_name: str) -> bool:
    from helia_profiler.platform import get_board

    board = get_board(board_name)
    return board.default_state_gpio_pin > 0 and board.default_go_gpio_pin > 0


class TestLockstepDefaultsOnWhenWired:
    """Issue #114: the lock-step default keys on wiring + mode, not SoC family.

    Before this contract, ``resolve_power_lockstep`` auto-enabled only for
    families flagged ``requires_lockstep_for_gated_power`` (Apollo5 only), so
    a wired Apollo4 or Apollo3 board silently free-ran its measured window and
    every gated capture came back ``integrity: degraded (no_gate_rise)``.
    """

    def test_apollo4_blue_plus_auto_enables_lockstep(self, tmp_path):
        """The exact board the issue was reproduced on.

        Apollo4 Blue Plus KBR, gate/state/GO = 22/23/24, no ``power.lockstep``
        in the config. Without lock-step this run degrades to ``no_gate_rise``
        on the bench; with it, ``integrity: valid``.
        """
        ctx = make_pmu_ctx(
            tmp_path,
            board="apollo4p_blue_kbr_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=None,  # left unset -> auto-resolution
        )
        assert ctx.config.power.lockstep is None
        assert _board_is_wired_for_lockstep("apollo4p_blue_kbr_evb") is True
        assert resolve_power_lockstep(ctx) is True

    @pytest.mark.parametrize("board", _all_board_names())
    def test_wiring_alone_decides_the_default(self, tmp_path, board):
        """For a gated external capture with ``power.lockstep`` unset, the
        answer is the wiring and nothing else.

        Written as an equality against the board's own wiring rather than a
        list of expected-true boards, so it stays honest for boards added
        later and pins the *absence* of any family conditional: an AP5-only
        (or AP4-only) rule fails here on every board of the other families.
        """
        ctx = make_pmu_ctx(
            tmp_path, board=board, transport="rtt", power_enabled=True, lockstep=None
        )
        assert resolve_power_lockstep(ctx) is _board_is_wired_for_lockstep(board)

    @pytest.mark.parametrize("board", ["apollo3p_evb", "apollo4p_evb", "apollo510_evb"])
    def test_explicit_false_still_wins(self, tmp_path, board):
        """Auto-enable is a default, never an override.

        ``power.lockstep: false`` is the documented escape hatch for bringing
        up incomplete wiring, and it must keep working on a fully wired board
        -- which is precisely where the new default would otherwise stomp it.

        This one cannot fail against the pre-#114 code, which returned False
        here for the *wrong* reason (the family gate) and so agreed by
        accident. What it does catch is the obvious wrong fix: an auto-enable
        that forgets to check ``power.lockstep is not None`` first.
        """
        ctx = make_pmu_ctx(
            tmp_path, board=board, transport="rtt", power_enabled=True, lockstep=False
        )
        assert _board_is_wired_for_lockstep(board) is True
        assert resolve_power_lockstep(ctx) is False

    def test_internal_mode_never_auto_enables(self, tmp_path):
        """Internal (on-device monitor) mode has no host poller to race.

        The measurement happens inside the firmware, so there is no gate for
        reset latency to outrun and no reason to add a handshake the host
        would have to drive. Like the test above, this guards the wrong fix
        (auto-enable keyed on wiring alone), not the pre-#114 code.
        """
        ctx = make_pmu_ctx(
            tmp_path,
            board="apollo4p_blue_kbr_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=None,
            extra={"power": {"mode": "internal"}},
        )
        assert _board_is_wired_for_lockstep("apollo4p_blue_kbr_evb") is True
        assert resolve_power_lockstep(ctx) is False

    def test_power_disabled_never_auto_enables(self, tmp_path):
        """Also a wrong-fix guard, not a pre-#114 regression test."""
        ctx = make_pmu_ctx(
            tmp_path,
            board="apollo4p_blue_kbr_evb",
            transport="rtt",
            power_enabled=False,
            lockstep=None,
        )
        assert resolve_power_lockstep(ctx) is False

    def test_auto_enabled_lockstep_reaches_the_baked_firmware_constant(self, tmp_path):
        """The host decision and the firmware constant come from one source.

        ``hpx_sync_wait_go()`` compiles to a no-op unless ``kSyncLockstep`` is
        baked true, so a host that thinks lock-step is on while the binary
        free-runs is the same bug with extra steps. This renders the real
        ``_gpio_sync.j2`` with the values the render context actually feeds it
        (``PowerConfig.gated_external_capture`` and
        ``resolve_power_lockstep``) and reads the emitted C constant back.
        """
        from helia_profiler.firmware import _jinja_env

        ctx = make_pmu_ctx(
            tmp_path,
            board="apollo4p_blue_kbr_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=None,
        )
        power = ctx.config.power
        rendered = _jinja_env.get_template("_gpio_sync.j2").render(
            power_sync_enabled=True,  # external power capture is requested
            lockstep=resolve_power_lockstep(ctx),
            sync_gpio_pin=power.sync_gpio_pin,
            state_gpio_pin=power.state_gpio_pin,
            go_gpio_pin=power.go_gpio_pin,
        )
        assert "static constexpr bool     kSyncLockstep     = true;" in rendered
        assert "static constexpr bool     kPowerSyncEnabled = true;" in rendered

    # Three scenarios chosen so that power_sync_enabled and lockstep DISAGREE
    # in at least one, and so that the expected answer is False in two. A
    # single all-True fixture (the first version of this test) was shown by
    # adversarial review to leave three realistic mutations of context.py
    # completely green: reading `lockstep_wiring_available` instead of the
    # resolved decision, hardcoding `True` in to_template_vars, and swapping
    # the power_sync_enabled/lockstep arguments -- because in that one fixture
    # all three sources happened to be True at once.
    @pytest.mark.parametrize(
        "scenario,extra,expect_sync,expect_lockstep",
        [
            ("wired external, auto", None, True, True),
            (
                "wired internal — wiring present but not a gated external capture",
                {"power": {"mode": "internal", "driver": "ina228", "ina228": {"shunt_ohms": 0.1}}},
                False,
                False,
            ),
            (
                "wired external, explicitly opted out",
                {"power": {"lockstep": False}},
                True,
                False,
            ),
        ],
    )
    def test_render_context_feeds_the_resolved_decision_to_the_template(
        self, tmp_path, scenario, extra, expect_sync, expect_lockstep
    ):
        """The hand-off that ``test_auto_enabled_lockstep...`` does NOT cover.

        That test calls ``resolve_power_lockstep`` itself and hands the result
        straight to the template, re-implementing the very hand-off it claims
        to verify. Adversarial review proved the gap twice over: first that
        replacing ``FirmwareRenderContext``'s
        ``lockstep=resolve_power_lockstep(ctx)`` with a bare ``False`` left the
        whole suite green, then that a single all-True fixture here still let
        three further mutations through.

        A divergence between host and baked constant is #114 with the polarity
        reversed, and worse than the bug this PR fixes: the host arms lock-step
        and holds GO low while the binary free-runs, so the run blocks for the
        full ``power.duration_s`` and dies with "Target did not signal READY",
        pointing the user at wiring that is fine.

        So take the REAL context through the REAL template and read the emitted
        C back -- which also covers the rendered ``false`` case, previously
        pinned nowhere (the render snapshots hardcode both values to False and
        their marker is a substring test that is true regardless).
        """
        from helia_profiler.engines import TFLM_ENGINE_HEADER
        from helia_profiler.engines.base import TflmArtifacts
        from helia_profiler.firmware import _jinja_env
        from helia_profiler.firmware.context import FirmwareRenderContext

        ctx = make_pmu_ctx(
            tmp_path,
            board="apollo4p_blue_kbr_evb",
            transport="rtt",
            power_enabled=True,
            lockstep=None,
            extra=extra,
        )
        # from_pipeline_context asserts the engine stage has run; nothing about
        # the lock-step hand-off depends on which engine.
        ctx.engine_artifacts = TflmArtifacts(engine_header=TFLM_ENGINE_HEADER)

        template_vars = FirmwareRenderContext.from_pipeline_context(ctx).to_template_vars()

        assert template_vars["lockstep"] == resolve_power_lockstep(ctx), scenario
        assert template_vars["lockstep"] is expect_lockstep, scenario
        assert template_vars["power_sync_enabled"] is expect_sync, scenario

        rendered = _jinja_env.get_template("_gpio_sync.j2").render(**template_vars)
        assert (
            f"static constexpr bool     kSyncLockstep     = {str(expect_lockstep).lower()};"
        ) in rendered, scenario
        assert (
            f"static constexpr bool     kPowerSyncEnabled = {str(expect_sync).lower()};"
        ) in rendered, scenario


class TestAutoStrategyNeverCyclesRail:
    @pytest.mark.parametrize("strategy", ["auto", "none", "debug_reset", "swpoi_reset"])
    def test_non_power_cycle_strategies_leave_rail_untouched(self, tmp_path, monkeypatch, strategy):
        # J-Link resets are stubbed so only rail cycling is observable.
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", lambda **_k: None)
        monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target_poi", lambda **_k: None)
        driver = _FakeRailDriver()
        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", power_enabled=True, reset_strategy=strategy
        )
        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=driver,  # ty: ignore[invalid-argument-type]  # duck-typed fake: only the rail surface
            power_driver_name="joulescope",
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
            ctx,
            phase=CapturePhase.POWER,
            power_driver=driver,  # ty: ignore[invalid-argument-type]  # duck-typed fake: only the rail surface
            power_driver_name="joulescope",
        )
        assert driver.power_cycle_calls == 1
        assert plan.power_cycle_attempted is True
        assert plan.power_cycle_succeeded is True
        assert "power_cycle" in plan.actions


class TestExplicitFlashRecoveryPath:
    def test_flash_recovery_is_the_other_rail_cycle_entry(self, tmp_path, monkeypatch):
        """Flash recovery is the only *other* place the rail cycles.

        This is the explicit bring-up/recovery path referenced by the auto-vs-
        rail-cycle invariant: a locked debug domain after a failed flash is
        recovered by power-cycling the Joulescope rail, never by the auto reset
        policy.
        """
        from helia_profiler.target.lifecycle import try_power_cycle_for_context

        driver = _FakeRailDriver()
        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: driver)
        ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=True)

        assert try_power_cycle_for_context(ctx) is True
        assert driver.power_cycle_calls == 1

    def test_flash_recovery_skipped_when_power_disabled(self, tmp_path):
        from helia_profiler.target.lifecycle import try_power_cycle_for_context

        ctx = make_pmu_ctx(tmp_path, board="apollo510_evb", power_enabled=False)
        assert try_power_cycle_for_context(ctx) is False
