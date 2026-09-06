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


@pytest.mark.parametrize("include_end", [False, True])
def test_uart_without_layers_reports_capture_error(tmp_path, monkeypatch, caplog, include_end):
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
    lines = ["--- HPX_START ---"]
    if include_end:
        lines.append("--- HPX_END ---")
    monkeypatch.setattr("helia_profiler.transport.uart.capture_uart_output", lambda **kwargs: lines)

    with pytest.raises(CaptureError, match="No layer data") as exc:
        capture_pmu(ctx)

    assert exc.value.hint is not None
    assert "UART" in exc.value.hint
    assert "--transport rtt" in exc.value.hint
    if not include_end:
        assert "HPX_END sentinel not found" in caplog.text
        assert "UART capture truncated" in caplog.text
