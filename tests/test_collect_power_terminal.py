"""Tests for post-GATE terminal collection and reconciliation stage."""

from __future__ import annotations

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
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.stages.collect_power_terminal import CollectPowerTerminalStage
from helia_profiler.stages.resolve_platform import ResolvePlatformStage


def _make_ctx(
    tmp_path: Path,
    *,
    transport: str = "rtt",
    internal: bool = False,
) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"board": "apollo510_evb", "transport": transport},
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
            inference_count=5,
            reference_inference_us=1000,
            count_source="configured",
        )
    )
    ctx.publish_power_firmware(firmware)
    ctx.publish_power_deployment(
        DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
    )
    if not internal:
        result = PowerResult(
            summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000),
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


@pytest.mark.parametrize("transport", ["rtt", "uart", "swo", "usb_cdc"])
def test_collect_stage_supports_all_profile_transports(tmp_path: Path, transport: str):
    ctx = _make_ctx(tmp_path, transport=transport)

    assert CollectPowerTerminalStage().should_skip(ctx) is False
