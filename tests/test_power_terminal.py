"""Tests for the post-GATE power terminal protocol."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from helia_profiler.artifacts import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerObservation,
    PowerRunPlan,
)
from helia_profiler.capture.power_terminal import (
    collect_power_terminal_rtt,
    parse_power_terminal,
    parse_power_terminal_envelope,
)
from helia_profiler.config import Transport, load_config
from helia_profiler.errors import PowerError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import PowerResult, PowerSummary
from helia_profiler.power.terminal_transport import (
    UsbCdcPowerTerminalTransport,
    _TERMINAL_TRANSPORTS,
    _collect_chunked_terminal,
    _collect_serial_terminal,
    register_power_terminal_transport,
)


def _lines(**overrides: str) -> list[str]:
    fields = {
        "HPX_POWER_TERMINAL_VERSION": "1",
        "HPX_POWER_STATUS": "ok",
        "HPX_POWER_REQUESTED_COUNT": "237",
        "HPX_POWER_COMPLETED_COUNT": "237",
        "HPX_POWER_ELAPSED_US": "5001234",
        "HPX_POWER_FINAL_PHASE": "complete",
        "HPX_POWER_ERROR_CODE": "0",
        "HPX_POWER_GATE_ASSERTED": "1",
        "HPX_POWER_GATE_LOWERED": "1",
        **overrides,
    }
    return [
        "noise before",
        "--- HPX_POWER_TERMINAL_START ---",
        *(f"{key}={value}" for key, value in fields.items()),
        "--- HPX_POWER_TERMINAL_END ---",
        "noise after",
    ]


def test_parse_success_record() -> None:
    record = parse_power_terminal(_lines())

    assert record.version == 1
    assert record.status == "ok"
    assert record.requested_count == 237
    assert record.completed_count == 237
    assert record.elapsed_us == 5001234
    assert record.final_phase == "complete"
    assert record.error_code == 0
    assert record.gate_asserted is True
    assert record.gate_lowered is True


def test_public_power_types_validate_direct_construction() -> None:
    from helia_profiler import OnDevicePowerSummary, PowerTerminalRecord

    with pytest.raises(ValueError, match="Completed count exceeds"):
        PowerTerminalRecord(
            version=1,
            status="ok",
            requested_count=1,
            completed_count=2,
            elapsed_us=100,
            final_phase="complete",
            error_code=0,
            gate_asserted=True,
            gate_lowered=True,
        )
    with pytest.raises(ValueError, match="duration must be positive"):
        OnDevicePowerSummary(
            source="ina228",
            scope="fixed_n_inference",
            energy_nj=100,
            duration_us=0,
            inference_count=1,
            overflow=False,
        )


def test_parse_error_record() -> None:
    record = parse_power_terminal(
        _lines(
            HPX_POWER_STATUS="error",
            HPX_POWER_COMPLETED_COUNT="19",
            HPX_POWER_FINAL_PHASE="inference",
            HPX_POWER_ERROR_CODE="4",
        )
    )

    assert record.status == "error"
    assert record.completed_count == 19
    assert record.final_phase == "inference"
    assert record.error_code == 4


def test_parse_on_device_power_measurement_envelope() -> None:
    envelope = parse_power_terminal_envelope(
        _lines(
            HPX_POWER_MEASUREMENT_SOURCE="ina228",
            HPX_POWER_MEASUREMENT_SCOPE="fixed_n_inference",
            HPX_POWER_ENERGY_NJ="90123456",
            HPX_POWER_MEASUREMENT_DURATION_US="5001234",
            HPX_POWER_MEASUREMENT_COUNT="237",
            HPX_POWER_MEASUREMENT_OVERFLOW="0",
            HPX_POWER_CHARGE_NC="50000000",
            HPX_POWER_BUS_VOLTAGE_UV="1800000",
            HPX_POWER_SAMPLE_COUNT="1000",
            HPX_POWER_CALIBRATION_ID="board-rev-a",
        )
    )

    assert envelope.terminal.status == "ok"
    assert envelope.measurement is not None
    assert envelope.measurement.source == "ina228"
    assert envelope.measurement.scope == "fixed_n_inference"
    assert envelope.measurement.energy_nj == 90123456
    assert envelope.measurement.duration_us == 5001234
    assert envelope.measurement.inference_count == 237
    assert envelope.measurement.overflow is False
    assert envelope.measurement.charge_nc == 50000000
    assert envelope.measurement.bus_voltage_uv == 1800000
    assert envelope.measurement.sample_count == 1000
    assert envelope.measurement.calibration_id == "board-rev-a"


@pytest.mark.parametrize(
    "overrides",
    [
        {"HPX_POWER_MEASUREMENT_SOURCE": "ina228"},
        {"HPX_POWER_ENERGY_NJ": "100"},
        {
            "HPX_POWER_MEASUREMENT_SOURCE": "ina228",
            "HPX_POWER_ENERGY_NJ": "bad",
        },
        {
            "HPX_POWER_MEASUREMENT_SOURCE": "ina228",
            "HPX_POWER_ENERGY_NJ": "-1",
        },
        {
            "HPX_POWER_MEASUREMENT_SOURCE": "ina228",
            "HPX_POWER_MEASUREMENT_SCOPE": "fixed_n_inference",
            "HPX_POWER_ENERGY_NJ": "100",
            "HPX_POWER_MEASUREMENT_DURATION_US": "0",
            "HPX_POWER_MEASUREMENT_COUNT": "237",
            "HPX_POWER_MEASUREMENT_OVERFLOW": "0",
        },
    ],
)
def test_parse_rejects_partial_or_invalid_measurement(overrides: dict[str, str]) -> None:
    with pytest.raises(PowerError):
        parse_power_terminal_envelope(_lines(**overrides))


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (_lines()[:-2], "incomplete or missing"),
        (_lines(HPX_POWER_TERMINAL_VERSION="2"), "Unsupported power terminal version"),
        (_lines(HPX_POWER_GATE_LOWERED="yes"), "Malformed power terminal boolean"),
        (_lines(HPX_POWER_ELAPSED_US="abc"), "Malformed power terminal field"),
        (_lines(HPX_POWER_COMPLETED_COUNT="238"), "exceeds requested count"),
        (_lines(HPX_POWER_ERROR_CODE="1"), "Successful power terminal status"),
        (
            _lines(HPX_POWER_STATUS="error", HPX_POWER_ERROR_CODE="0"),
            "Error power terminal status",
        ),
        (_lines(HPX_POWER_FINAL_PHASE=""), "final phase must not be empty"),
        (
            [
                "--- HPX_POWER_TERMINAL_START ---",
                "HPX_POWER_STATUS=ok",
                "HPX_POWER_STATUS=error",
                "--- HPX_POWER_TERMINAL_END ---",
            ],
            "Duplicate power terminal field",
        ),
    ],
)
def test_parse_rejects_invalid_records(lines: list[str], message: str) -> None:
    with pytest.raises(PowerError, match=message):
        parse_power_terminal(lines)


def test_parse_requires_elapsed_time_for_v1() -> None:
    lines = [
        line
        for line in _lines()
        if not line.startswith("HPX_POWER_ELAPSED_US=")
    ]

    with pytest.raises(PowerError, match="missing fields: HPX_POWER_ELAPSED_US"):
        parse_power_terminal(lines)


def test_publish_terminal_into_grouped_power_run(tmp_path: Path) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "power": {"enabled": True},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.publish_power_plan(
        PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=237,
            count_source="configured",
        )
    )
    binary = tmp_path / "hpx_profiler_power"
    binary.touch()
    firmware = FirmwareArtifact(
        role="power",
        target_name="hpx_profiler_power",
        app_dir=tmp_path,
        build_dir=tmp_path,
        binary_path=binary,
    )
    ctx.publish_power_firmware(firmware)
    ctx.publish_power_deployment(
        DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
    )
    result = PowerResult(summary=PowerSummary(0.01, 0.018, 0.02, 0.09, 5.0, 5000))
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
    record = parse_power_terminal(_lines())

    ctx.publish_power_terminal(record)

    assert ctx.power_run is not None
    assert ctx.power_run.terminal is record

    with pytest.raises(ValueError, match="already been published"):
        ctx.publish_power_terminal(record)


class _FakeRttSession:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.started_at: int | None = None
        self.stopped = False

    def rtt_start(self, block_address: int | None = None) -> None:
        self.started_at = block_address

    def rtt_read(self, buffer_index: int, num_bytes: int):
        del buffer_index, num_bytes
        return self.chunks.pop(0) if self.chunks else b""

    def rtt_stop(self) -> None:
        self.stopped = True


def test_collect_power_terminal_rtt_without_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "\n".join(_lines()) + "\n"
    session = _FakeRttSession([text[:50].encode(), text[50:].encode()])

    monkeypatch.setattr(
        "helia_profiler.capture.rtt_symbol.resolve_rtt_control_block_address",
        lambda *args, **kwargs: 0x20008000,
    )

    @contextmanager
    def fake_attached_session(**kwargs):
        assert kwargs["device"] == "AP510NFA-CBR"
        yield session

    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.attached_session",
        fake_attached_session,
    )

    record = collect_power_terminal_rtt(
        build_dir=tmp_path,
        toolchain="arm-none-eabi-gcc",
        device="AP510NFA-CBR",
        jlink_serial="1160002255",
        timeout_s=1.0,
    )

    assert record.status == "ok"
    assert session.started_at == 0x20008000
    assert session.stopped is True


def test_collect_power_terminal_rtt_skips_stale_malformed_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    malformed = "\n".join(_lines(HPX_POWER_GATE_LOWERED="bad")) + "\n"
    valid = "\n".join(_lines()) + "\n"
    session = _FakeRttSession([(malformed + valid).encode()])
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_symbol.resolve_rtt_control_block_address",
        lambda *args, **kwargs: 0x20008000,
    )

    @contextmanager
    def fake_attached_session(**kwargs):
        del kwargs
        yield session

    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.attached_session",
        fake_attached_session,
    )

    record = collect_power_terminal_rtt(
        build_dir=tmp_path,
        toolchain="arm-none-eabi-gcc",
        device="AP510NFA-CBR",
        jlink_serial=None,
        timeout_s=1.0,
    )

    assert record.status == "ok"
    assert record.completed_count == 237


def test_collect_power_terminal_rtt_times_out_without_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _FakeRttSession([])
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_symbol.resolve_rtt_control_block_address",
        lambda *args, **kwargs: 0x20008000,
    )

    @contextmanager
    def fake_attached_session(**kwargs):
        del kwargs
        yield session

    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.attached_session",
        fake_attached_session,
    )

    with pytest.raises(PowerError, match="No power terminal record"):
        collect_power_terminal_rtt(
            build_dir=tmp_path,
            toolchain="arm-none-eabi-gcc",
            device="AP510NFA-CBR",
            jlink_serial=None,
            timeout_s=0.01,
        )


class _FakeSerial:
    def __init__(self, lines: list[bytes]) -> None:
        self.lines = list(lines)

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


def test_collect_serial_terminal_ignores_noise_and_parses_fresh_frame() -> None:
    stream = _FakeSerial(
        [b"stale noise\n", *(f"{line}\n".encode() for line in _lines())]
    )

    envelope = _collect_serial_terminal(stream, timeout_s=1.0)

    assert envelope.terminal.status == "ok"
    assert envelope.terminal.completed_count == 237


def test_collect_serial_terminal_recovers_after_malformed_repeated_frame() -> None:
    malformed = _lines(HPX_POWER_GATE_LOWERED="bad")
    stream = _FakeSerial(
        [
            *(f"{line}\n".encode() for line in malformed),
            *(f"{line}\n".encode() for line in _lines()),
        ]
    )

    envelope = _collect_serial_terminal(stream, timeout_s=1.0)

    assert envelope.terminal.status == "ok"


def test_collect_chunked_terminal_handles_split_swo_frames() -> None:
    text = "noise\n" + "\n".join(_lines()) + "\n"
    chunks = [text[:33].encode(), text[33:111].encode(), text[111:].encode()]

    envelope = _collect_chunked_terminal(
        lambda: chunks.pop(0) if chunks else b"",
        timeout_s=1.0,
    )

    assert envelope.terminal.status == "ok"
    assert envelope.terminal.completed_count == 237


def test_collect_chunked_terminal_recovers_after_malformed_frame() -> None:
    malformed = "\n".join(_lines(HPX_POWER_GATE_LOWERED="bad")) + "\n"
    valid = "\n".join(_lines()) + "\n"
    chunks = [malformed.encode(), valid.encode()]

    envelope = _collect_chunked_terminal(
        lambda: chunks.pop(0) if chunks else b"",
        timeout_s=1.0,
    )

    assert envelope.terminal.status == "ok"


def test_terminal_registry_rejects_mismatch_and_duplicate() -> None:
    class WrongAdapter:
        transport = Transport.UART

        def collect(self, ctx, *, timeout_s):  # pragma: no cover
            raise AssertionError

    with pytest.raises(ValueError, match="cannot register"):
        register_power_terminal_transport(Transport.RTT, WrongAdapter)

    original = _TERMINAL_TRANSPORTS[Transport.RTT]
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_power_terminal_transport(Transport.RTT, original)
    finally:
        _TERMINAL_TRANSPORTS[Transport.RTT] = original


def test_usb_adapter_preserves_buffer_and_asserts_dtr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from helia_profiler.config import load_config

    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"transport": "usb_cdc", "usb_port": "/dev/fake"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    lines = [f"{line}\n".encode() for line in _lines()]

    class FakeUsbStream(_FakeSerial):
        def __init__(self):
            super().__init__(lines)
            self.dtr = False
            self.flush_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def reset_input_buffer(self):
            self.flush_calls += 1

    stream = FakeUsbStream()
    monkeypatch.setattr("serial.Serial", lambda **kwargs: stream)

    envelope = UsbCdcPowerTerminalTransport().collect(ctx, timeout_s=1.0)

    assert envelope.terminal.status == "ok"
    assert stream.dtr is True
    assert stream.flush_calls == 0
