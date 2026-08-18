"""Tests for post-GATE terminal collection and reconciliation stage."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from helia_profiler.results import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerObservation,
    PowerRunPlan,
    PowerTerminalEnvelope,
    PowerTerminalRecord,
    OnDevicePowerSummary,
)
from helia_profiler.config import load_config
from helia_profiler.errors import PowerError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import GatedPowerWindow, PowerResult, PowerSummary
from helia_profiler.stages.collect_power_terminal import CollectPowerTerminalStage
from helia_profiler.stages.resolve_platform import ResolvePlatformStage


def _gated_window(duration_s: float) -> GatedPowerWindow:
    """A gated window carrying only the field the clock cross-check reads."""
    return GatedPowerWindow(
        start_s=0.0,
        end_s=duration_s,
        duration_s=duration_s,
        charge_c=0.0,
        energy_j=0.0,
        avg_current_a=0.0,
        avg_power_w=0.0,
        peak_current_a=0.0,
        sample_count=int(duration_s * 1000),
    )


def _make_ctx(
    tmp_path: Path,
    *,
    transport: str = "rtt",
    internal: bool = False,
    inference_count: int = 5,
    reference_inference_us: int = 1000,
    count_source: str = "configured",
    gate_duration_s: float = 0.005,
    capture_duration_s: float | None = None,
    deployed_at: str = "2026-07-18T00:00:00+00:00",
    clean_window_probe: str = "infer",
) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"board": "apollo510_evb", "transport": transport},
            "profiling": {"clean_window_probe": clean_window_probe},
            "power": {
                "enabled": True,
                "firmware": "dedicated",
                "driver": "ondevice" if internal else "joulescope",
                "mode": "internal" if internal else "external",
            },
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    binary = build_dir / "hpx_profiler_power"
    binary.touch()
    firmware = FirmwareArtifact(
        role="power",
        target_name="hpx_profiler_power",
        app_dir=tmp_path,
        build_dir=build_dir,
        binary_path=binary,
    )
    ctx.publish_power_plan(
        PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=inference_count,
            reference_inference_us=reference_inference_us,
            count_source=count_source,
        )
    )
    ctx.publish_power_firmware(firmware)
    ctx.publish_power_deployment(
        DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at=deployed_at,
        )
    )
    if not internal:
        # The gated window must agree with the plan (5 x 1000 us) and with
        # _record()'s elapsed_us, or every run through this fixture trips the
        # window-clock cross-check for reasons unrelated to what it is testing.
        result = PowerResult(
            summary=PowerSummary(
                0.01,
                0.018,
                0.02,
                0.09,
                capture_duration_s if capture_duration_s is not None else gate_duration_s,
                5000,
            ),
            gated_windows=[_gated_window(gate_duration_s)],
            metadata={"measurement_scope": "gpio_gated_clean_window"},
        )
        ctx.publish_power_observation(
            PowerObservation(
                mode="gpio_gated",
                result=result,
                gate_rise_observed=True,
                gate_fall_observed=True,
                deadline_s=20.0,
                integrity="valid",
            )
        )
    return ctx


def _record(**overrides) -> PowerTerminalRecord:
    values = {
        "version": 1,
        "status": "ok",
        "requested_count": 5,
        "completed_count": 5,
        "elapsed_us": 5000,
        "final_phase": "complete",
        "error_code": 0,
        "gate_asserted": True,
        "gate_lowered": True,
        **overrides,
    }
    return PowerTerminalRecord(**values)


class _FakeTerminalTransport:
    def __init__(
        self,
        record: PowerTerminalRecord,
        measurement: OnDevicePowerSummary | None = None,
    ) -> None:
        self.envelope = PowerTerminalEnvelope(
            terminal=record,
            measurement=measurement,
        )

    def collect(self, ctx: PipelineContext, *, timeout_s: float) -> PowerTerminalEnvelope:
        del ctx, timeout_s
        return self.envelope


def test_collect_stage_publishes_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ctx = _make_ctx(tmp_path)
    record = _record()
    monkeypatch.setattr(
        "helia_profiler.power.terminal_transport.get_power_terminal_transport",
        lambda transport: _FakeTerminalTransport(record),
    )

    CollectPowerTerminalStage().run(ctx)

    assert ctx.power_run is not None
    assert ctx.power_run.terminal is record


def test_internal_terminal_measurement_becomes_power_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    ctx = _make_ctx(tmp_path, internal=True)
    record = _record()
    measurement = OnDevicePowerSummary(
        source="ina228",
        scope="fixed_n_inference",
        energy_nj=90_000_000,
        duration_us=5000,
        inference_count=5,
        overflow=False,
        charge_nc=50_000_000,
        bus_voltage_uv=1_800_000,
        sample_count=100,
        calibration_id="board-rev-a",
    )
    monkeypatch.setattr(
        "helia_profiler.power.terminal_transport.get_power_terminal_transport",
        lambda transport: _FakeTerminalTransport(record, measurement),
    )

    CollectPowerTerminalStage().run(ctx)

    assert ctx.power_run is not None
    assert ctx.power_run.on_device_summary is measurement
    assert ctx.power_result is not None
    assert ctx.power_result.summary.energy_j == pytest.approx(0.09)
    assert ctx.power_result.summary.duration_s == pytest.approx(0.005)
    assert ctx.power_result.summary.avg_current_a == pytest.approx(10.0)
    assert ctx.power_result.metadata["source"] == "ina228"


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (_record(requested_count=6, completed_count=6), "does not match the host plan"),
        (
            _record(status="error", completed_count=2, error_code=4, final_phase="inference"),
            "reported error 4",
        ),
        (_record(completed_count=4), "incomplete inference execution"),
        (_record(gate_lowered=False), "did not confirm that GATE was lowered"),
    ],
)
def test_collect_stage_rejects_inconsistent_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: PowerTerminalRecord,
    message: str,
):
    ctx = _make_ctx(tmp_path)
    monkeypatch.setattr(
        "helia_profiler.power.terminal_transport.get_power_terminal_transport",
        lambda transport: _FakeTerminalTransport(record),
    )

    with pytest.raises(PowerError, match=message):
        CollectPowerTerminalStage().run(ctx)


def _measurement(**overrides) -> OnDevicePowerSummary:
    values = {
        "source": "ina228",
        "scope": "fixed_n_inference",
        "energy_nj": 90_000_000,
        "duration_us": 5000,
        "inference_count": 5,
        "overflow": False,
        "charge_nc": 50_000_000,
        "bus_voltage_uv": 1_800_000,
        "sample_count": 100,
        "calibration_id": "board-rev-a",
        **overrides,
    }
    return OnDevicePowerSummary(**values)


class TestInternalMeasurementPlausibility:
    """Both corrupt-measurement signatures pass every register-level health
    check (firmware status ok, no DIAG bit), so the host must refuse to
    publish them as a valid PowerResult."""

    def test_all_zero_measurement_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A window shorter than one accumulator update — or a dead sense
        # path — reads exactly zero while looking perfectly healthy.
        ctx = _make_ctx(tmp_path, internal=True)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(
                _record(), _measurement(energy_nj=0, charge_nc=0)
            ),
        )
        with pytest.raises(PowerError, match="exactly zero energy and charge"):
            CollectPowerTerminalStage().run(ctx)

    def test_tiny_measurement_is_not_mistaken_for_reversed_wiring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """energy_nj and charge_nc round independently on-device, so a charge
        under the 1 nC step legitimately reports 0 alongside a 1-2 nJ energy.
        That must not be diagnosed as miswiring — blocking a valid run with a
        wrong hint is worse than the reading being uselessly small."""
        ctx = _make_ctx(tmp_path, internal=True)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(
                _record(), _measurement(energy_nj=1, charge_nc=0)
            ),
        )
        CollectPowerTerminalStage().run(ctx)
        assert ctx.power_result is not None

    def test_energy_without_charge_flags_reversed_wiring(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # ENERGY integrates |power| while CHARGE is signed: reversed IN+/IN-
        # clamps negative charge to zero — nonzero energy, zero charge.
        ctx = _make_ctx(tmp_path, internal=True)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(
                _record(), _measurement(charge_nc=0)
            ),
        )
        with pytest.raises(PowerError, match="reversed sense wiring"):
            CollectPowerTerminalStage().run(ctx)

    def test_external_mode_ignores_bystander_zero_measurement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # A bystander monitor with disconnected sense inputs legitimately
        # reads zero; the external capture is the measurement of record.
        ctx = _make_ctx(tmp_path)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(
                _record(), _measurement(energy_nj=0, charge_nc=0, bus_voltage_uv=600)
            ),
        )
        CollectPowerTerminalStage().run(ctx)
        assert ctx.power_run is not None and ctx.power_run.terminal is not None


class TestFirmwareWindowClockIntegrity:
    """The firmware times its own measured window and reports it as
    HPX_POWER_ELAPSED_US. Nothing else on the host depends on that clock, so
    when it is wrong every other check still passes -- status ok, counts
    matched, both gate edges seen, energy integrated in hardware -- and the run
    publishes confidently wrong average power and current.

    Numbers below are the real Apollo3 Blue Plus bench pair (2026-08,
    apollo3p_evb, KWS/heliaRT, JS110 external): the pre-fix build reported
    elapsed_us=0 for 24/24 completed inferences against a 4.963 s measured
    gate, and the fixed build reported 4.970184 s against a 4.967 s gate.
    """

    # --- Apollo3 bench pair -------------------------------------------------
    BENCH_COUNT = 24
    BENCH_REFERENCE_US = 208_744  # host plan, from the profile binary
    BENCH_GATE_S = 4.967  # JS110 gated window, fixed build
    BENCH_ELAPSED_US = 4_970_184  # firmware STIMER, fixed build -> 0.064% apart
    BASELINE_GATE_S = 4.963  # JS110 gated window, pre-fix build
    # Apollo4's failure shape: reported 6027 us/inference where the truth was
    # 866.6 us, i.e. the window clock ran ~7x long.
    AP4_INFLATION = 6027 / 866.6

    def _bench_ctx(
        self,
        tmp_path: Path,
        *,
        internal: bool = False,
        gate_s: float = BENCH_GATE_S,
        capture_s: float | None = None,
        host_envelope_s: float | None = None,
    ) -> PipelineContext:
        tmp_path.mkdir(parents=True, exist_ok=True)
        kwargs = {}
        if host_envelope_s is not None:
            # Backdate the power-firmware deployment so the stage computes a
            # controlled host wall-clock envelope. The default fixture uses a
            # fixed 2026-07-18 stamp, i.e. an envelope of months -- no ceiling
            # can trip, which is what every other test wants.
            started = datetime.now(timezone.utc) - timedelta(seconds=host_envelope_s)
            kwargs["deployed_at"] = started.isoformat()
        return _make_ctx(
            tmp_path,
            internal=internal,
            inference_count=self.BENCH_COUNT,
            reference_inference_us=self.BENCH_REFERENCE_US,
            count_source="profile_guided",
            gate_duration_s=gate_s,
            capture_duration_s=capture_s,
            **kwargs,
        )

    def _bench_record(self, **overrides) -> PowerTerminalRecord:
        values = {
            "requested_count": self.BENCH_COUNT,
            "completed_count": self.BENCH_COUNT,
            "elapsed_us": self.BENCH_ELAPSED_US,
            **overrides,
        }
        return _record(**values)

    def _run(self, ctx, record, monkeypatch, measurement=None):
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(record, measurement),
        )
        CollectPowerTerminalStage().run(ctx)

    def _bench_measurement(self, elapsed_us: int) -> OnDevicePowerSummary:
        # Internal mode requires a measurement payload; the parser already
        # enforces duration_us == elapsed_us, so mirror that here.
        return _measurement(
            duration_us=elapsed_us, inference_count=self.BENCH_COUNT
        )

    # --- 1. frozen clock: fatal internally, warning externally ---------------

    def test_zero_elapsed_is_terminal_in_internal_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Internal mode requires MEASUREMENT_DURATION_US == ELAPSED_US, so a
        zero window makes average power and current wrong by the same factor.
        The measurement of record is corrupt -- terminal, for the same reason
        the all-zero INA228 reading is."""
        ctx = self._bench_ctx(tmp_path, internal=True)
        record = self._bench_record(elapsed_us=0)
        with pytest.raises(PowerError, match="zero elapsed time") as excinfo:
            self._run(ctx, record, monkeypatch, self._bench_measurement(1))
        hint = excinfo.value.hint or ""
        assert "CDBGPWRUPREQ" in hint
        assert "32.768 kHz" in hint  # the second cause must be named too

    def test_zero_elapsed_only_warns_in_external_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """The exact pre-fix Apollo3 signature: 24/24 inferences in 0 us. The
        JS110 owns this run's power numbers and they were correct to 0.19%
        despite it, so raising would discard a good capture -- and would do it
        before GenerateReportStage, leaving no artifact at all."""
        ctx = self._bench_ctx(tmp_path, gate_s=self.BASELINE_GATE_S)
        record = self._bench_record(elapsed_us=0)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, record, monkeypatch)
        assert "window clock never advanced" in caplog.text
        # The capture survives and is published.
        assert ctx.power_run is not None
        assert ctx.power_run.terminal is record

    def test_zero_elapsed_with_no_completed_work_is_not_this_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A firmware that failed before running anything reports 0/0 and
        elapsed 0 legitimately; it is rejected earlier, for the real reason,
        not misdiagnosed as a frozen clock."""
        ctx = self._bench_ctx(tmp_path)
        record = _record(
            status="error",
            requested_count=self.BENCH_COUNT,
            completed_count=0,
            elapsed_us=0,
            error_code=4,
            final_phase="allocate",
        )
        with pytest.raises(PowerError, match="reported error 4"):
            self._run(ctx, record, monkeypatch)

    # --- 2. external-mode warning -------------------------------------------

    def test_bench_agreement_logs_no_window_clock_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """4.970184 s firmware vs 4.967 s host = 0.064% apart. The check must
        stay silent well inside its 5% bound on a real passing run."""
        ctx = self._bench_ctx(tmp_path)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, self._bench_record(), monkeypatch)
        assert "window clock" not in caplog.text
        assert ctx.power_run is not None and ctx.power_run.terminal is not None

    def test_external_window_clock_disagreement_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """Apollo4's ~7x inflation against a correctly measured gate."""
        ctx = self._bench_ctx(tmp_path)
        inflated = int(self.BENCH_ELAPSED_US * self.AP4_INFLATION)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, self._bench_record(elapsed_us=inflated), monkeypatch)
        assert "window clock and the reference disagree" in caplog.text
        assert "gated_windows" in caplog.text
        # Warning only -- the run still completes and publishes.
        assert ctx.power_run is not None and ctx.power_run.terminal is not None

    def test_external_check_uses_the_gated_window_not_the_capture_summary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """summary.duration_s holds the WHOLE capture window on the degraded
        path, so comparing against it would measure the wrong interval. The
        gated window is the only accepted reference."""
        # Whole-capture duration was 8.4821 s on the bench run: 41% away from
        # the firmware's window, so picking that source would warn.
        ctx = self._bench_ctx(tmp_path, capture_s=8.4821)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, self._bench_record(), monkeypatch)
        assert "window clock" not in caplog.text

    def test_degraded_capture_is_not_cross_checked_at_all(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """A degraded capture has no gated window, only a whole-capture
        free-form summary. Falling back to it fabricates a disagreement out of
        an unrelated interval -- reproduced on two real Apollo4 artifacts
        (ap4-js110-2, ap4-js110-smoke), where a firmware clock accurate to
        0.16% "disagreed" by 73.9% with a 19.2 s free-form capture. There must
        be no window-clock warning here; power.observation_degraded already
        says what actually went wrong."""
        ctx = self._bench_ctx(tmp_path)
        assert ctx.power_run is not None and ctx.power_run.observation is not None
        ctx.publish_power_observation(
            PowerObservation(
                mode="free_form",
                result=PowerResult(
                    summary=PowerSummary(0.001, 0.002, 0.02, 0.04, 19.2, 19200),
                    gated_windows=[],
                    metadata={"measurement_scope": "free_form_capture"},
                ),
                gate_rise_observed=True,
                gate_fall_observed=False,
                deadline_s=45.0,
                integrity="degraded",
            )
        )
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, self._bench_record(), monkeypatch)
        assert "window clock" not in caplog.text

    # --- 3. internal-mode warning -------------------------------------------

    def test_internal_window_clock_disagreement_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        ctx = self._bench_ctx(tmp_path, internal=True)
        inflated = int(self.BENCH_ELAPSED_US * self.AP4_INFLATION)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                ctx,
                self._bench_record(elapsed_us=inflated),
                monkeypatch,
                self._bench_measurement(inflated),
            )
        assert "window clock and the reference disagree" in caplog.text
        assert "planned_window" in caplog.text

    @pytest.mark.parametrize("skew", [1.14, 0.86])
    def test_internal_cross_binary_skew_does_not_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, skew: float
    ):
        """Internal mode's plan reference is N x a per-inference time measured
        by a DIFFERENT binary, and that comparison legitimately skews: the
        worst legitimate disagreement seen was 14% (Apollo4, profile clean-loop
        757-786 us against a true 866 us window), plus ~4% build-to-build swing
        in the profile metric alone. 14% must not warn in EITHER direction --
        the real disagreement had the power window slower than the profile
        predicted, the opposite of the intuition."""
        ctx = self._bench_ctx(tmp_path, internal=True)
        planned_us = self.BENCH_COUNT * self.BENCH_REFERENCE_US
        skewed = int(planned_us * skew)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                ctx,
                self._bench_record(elapsed_us=skewed),
                monkeypatch,
                self._bench_measurement(skewed),
            )
        assert "window clock" not in caplog.text

    @pytest.mark.parametrize("skew", [1.30, 0.70])
    def test_internal_threshold_is_25_percent_not_50(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog, skew: float
    ):
        """30% away from the plan is past the widest legitimate cross-binary
        disagreement ever observed (14% + build noise) and must warn. Pins the
        band between the old 50% bound and the current 25% one, in both
        directions, so a silent revert is a test failure."""
        ctx = self._bench_ctx(tmp_path, internal=True)
        skewed = int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * skew)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                ctx,
                self._bench_record(elapsed_us=skewed),
                monkeypatch,
                self._bench_measurement(skewed),
            )
        assert "window clock and the reference disagree" in caplog.text
        assert "planned_window" in caplog.text

    # --- 4. internal-mode host wall-clock ceiling ---------------------------

    def test_internal_window_longer_than_host_wall_time_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """The plan-independent, hardware-independent check. A window cannot
        outlast the interval that contains it, so this catches the Apollo4-style
        ~7x inflation for an INA228 user with no external instrument -- even if
        the plan happened to agree."""
        ctx = self._bench_ctx(tmp_path, internal=True, host_envelope_s=10.0)
        inflated = int(self.BENCH_ELAPSED_US * self.AP4_INFLATION)  # ~34.6 s
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                ctx,
                self._bench_record(elapsed_us=inflated),
                monkeypatch,
                self._bench_measurement(inflated),
            )
        assert "cannot outlast the interval that contains it" in caplog.text
        assert ctx.power_result is not None
        assert ctx.power_result.metadata["window_clock_ceiling"]["elapsed_us"] == inflated

    def test_internal_window_inside_host_wall_time_does_not_warn(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """The bench window (4.970184 s) inside a realistic 10 s host envelope:
        recorded for downstream policy, but silent."""
        ctx = self._bench_ctx(tmp_path, internal=True, host_envelope_s=10.0)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                ctx,
                self._bench_record(),
                monkeypatch,
                self._bench_measurement(self.BENCH_ELAPSED_US),
            )
        assert "cannot outlast" not in caplog.text
        assert ctx.power_result is not None
        assert "window_clock_ceiling" in ctx.power_result.metadata

    def test_ceiling_is_internal_mode_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """External mode already compares against the host-timed gate, which is
        strictly better than a start-to-collect envelope; running both would
        only add a second, weaker voice saying the same thing."""
        ctx = self._bench_ctx(tmp_path, host_envelope_s=1.0)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(ctx, self._bench_record(), monkeypatch)
        assert "cannot outlast" not in caplog.text

    # --- 4. mode-awareness ---------------------------------------------------

    def test_the_two_modes_apply_different_tolerances_to_the_same_skew(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """One 14% deviation, two verdicts. Against a host-timed gate (same
        physical window) that is a real fault; against a cross-binary plan it
        is expected noise. The split is the whole point of having two
        thresholds, so pin it directly rather than inferring it from the two
        single-mode tests above."""
        skew = 1.14

        external = self._bench_ctx(tmp_path / "ext")
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                external,
                self._bench_record(elapsed_us=int(self.BENCH_GATE_S * 1e6 * skew)),
                monkeypatch,
            )
        assert "window clock and the reference disagree" in caplog.text

        caplog.clear()
        internal = self._bench_ctx(tmp_path / "int", internal=True)
        planned = int(self.BENCH_COUNT * self.BENCH_REFERENCE_US * skew)
        with caplog.at_level("WARNING", logger="hpx"):
            self._run(
                internal,
                self._bench_record(elapsed_us=planned),
                monkeypatch,
                self._bench_measurement(planned),
            )
        assert "window clock" not in caplog.text


class TestBusyLoopProbeCompletesARun:
    """The busy_loop diagnostic runs ONE spin window, not N inferences.

    Firmware counts terminal work in units the window actually performed, so
    it reports 1 requested / 1 completed for this probe
    (``_power_terminal_success.j2``) and the host expects the same via
    ``power.diagnostics.expected_terminal_requested_count``.

    Before that agreement existed, firmware reported ``clean_iters_n``
    requested against ``clean_count == 1`` completed and this stage raised
    "Power firmware reported incomplete inference execution. Completed 1/5
    inferences." on every busy_loop run -- so the probe could not finish a run
    on any board, and ``elapsed_us``, the number it exists to produce, was
    never consumed.
    """

    def test_busy_loop_terminal_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        ctx = _make_ctx(tmp_path, inference_count=5, clean_window_probe="busy_loop")
        # One spin window, one unit of work, and a real measured duration.
        record = _record(requested_count=1, completed_count=1, elapsed_us=5000)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(record),
        )

        CollectPowerTerminalStage().run(ctx)

        assert ctx.power_run is not None
        assert ctx.power_run.terminal is record
        # elapsed_us reaches the artifact -- the whole point of the probe.
        assert ctx.power_run.terminal.elapsed_us == 5000

    def test_the_old_firmware_shape_is_still_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """N requested against 1 completed stays an error.

        The fix is that firmware no longer EMITS this shape, not that the host
        started tolerating self-contradictory reports.
        """
        ctx = _make_ctx(tmp_path, inference_count=5, clean_window_probe="busy_loop")
        record = _record(requested_count=5, completed_count=1, elapsed_us=5000)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(record),
        )

        with pytest.raises(PowerError, match="does not match the host plan"):
            CollectPowerTerminalStage().run(ctx)

    def test_infer_probe_still_expects_the_planned_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The busy_loop allowance must not leak into the default probe."""
        ctx = _make_ctx(tmp_path, inference_count=5, clean_window_probe="infer")
        record = _record(requested_count=1, completed_count=1, elapsed_us=5000)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(record),
        )

        with pytest.raises(PowerError, match="does not match the host plan"):
            CollectPowerTerminalStage().run(ctx)

    def test_internal_mode_does_not_warn_about_a_plan_derived_reference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
    ):
        """The internal window-clock cross-check has no valid reference here.

        Its reference is ``N x reference_inference_us`` (5 x 1000 us = 5 ms),
        but a busy_loop window is sized from window_target_ms and legitimately
        runs ~1 s. Comparing them would fire a ~200x "disagreement" warning on
        every correct run, so the plan-derived reference is withheld.
        """
        import logging

        ctx = _make_ctx(
            tmp_path,
            internal=True,
            inference_count=5,
            reference_inference_us=1000,
            clean_window_probe="busy_loop",
        )
        record = _record(requested_count=1, completed_count=1, elapsed_us=1_000_000)
        measurement = _measurement(duration_us=1_000_000, inference_count=1)
        monkeypatch.setattr(
            "helia_profiler.power.terminal_transport.get_power_terminal_transport",
            lambda transport: _FakeTerminalTransport(record, measurement),
        )

        with caplog.at_level(logging.WARNING):
            CollectPowerTerminalStage().run(ctx)

        assert "window clock" not in caplog.text


@pytest.mark.parametrize("transport", ["rtt", "uart", "swo", "usb_cdc"])
def test_collect_stage_supports_all_profile_transports(tmp_path: Path, transport: str):
    ctx = _make_ctx(tmp_path, transport=transport)

    assert CollectPowerTerminalStage().should_skip(ctx) is False
