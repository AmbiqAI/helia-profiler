"""Transport truncation diagnostics remain actionable."""

import pytest

from helia_profiler.capture import _TRUNCATION_HINTS, _truncation_hint, capture_pmu
from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.resolve_platform import ResolvePlatformStage
from helia_profiler.vocab import Transport


@pytest.mark.parametrize("transport", list(Transport))
def test_truncation_hint_covers_supported_transports(transport):
    assert transport in _TRUNCATION_HINTS
    assert _truncation_hint(transport.value) == _TRUNCATION_HINTS[transport]


def test_truncation_hint_handles_unknown_transport():
    assert "selected transport" in _truncation_hint("unsupported")


def test_truncation_hint_handles_missing_mapping(monkeypatch):
    monkeypatch.delitem(_TRUNCATION_HINTS, Transport.UART)
    assert "selected transport" in _truncation_hint("uart")


def _uart_ctx(tmp_path, monkeypatch, lines):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"transport": "uart"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    monkeypatch.setattr("helia_profiler.transport.uart.capture_uart_output", lambda **kwargs: lines)
    return ctx


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (["--- HPX_START ---"], "ended before HPX_END"),
        (["--- HPX_START ---", "--- HPX_END ---"], "No layer data"),
    ],
)
def test_uart_without_layers_reports_capture_error(tmp_path, monkeypatch, lines, message):
    ctx = _uart_ctx(tmp_path, monkeypatch, lines)

    with pytest.raises(CaptureError, match=message) as exc:
        capture_pmu(ctx)

    assert exc.value.hint is not None
    assert "UART" in exc.value.hint
    assert "--transport rtt" in exc.value.hint


def test_truncated_capture_with_layers_fails(tmp_path, monkeypatch):
    lines = [
        "--- HPX_START ---",
        "HPX_NUM_PRESETS=2",
        "HPX_PRESETS=cpu_0,memory_0",
        "--- HPX_PRESET cpu_0 ---",
        "--- HPX_ITER 0 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES",
        "0,CONV_2D,100",
    ]
    ctx = _uart_ctx(tmp_path, monkeypatch, lines)

    with pytest.raises(CaptureError, match="ended before HPX_END") as exc:
        capture_pmu(ctx)

    assert exc.value.hint is not None
    assert "UART capture truncated" in exc.value.hint
