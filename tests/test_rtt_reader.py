from __future__ import annotations

from pathlib import Path

from helia_profiler.capture import capture_pmu
from helia_profiler.capture.rtt_reader import _scan_for_rtt_control_block
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage


class _FakeJLink:
    def __init__(self, memory: dict[int, bytes]):
        self._memory = memory

    def memory_read8(self, addr: int, length: int) -> list[int]:
        return list(self._memory.get(addr, b"\x00" * length))


def test_scan_for_rtt_control_block_uses_provided_ranges():
    magic = b"SEGGER RTT"
    chunk = magic + b"\x00" * (0x4000 - len(magic))
    jlink = _FakeJLink({0x30000000: chunk})

    assert _scan_for_rtt_control_block(jlink, ((0x30000000, 0x4000),)) == 0x30000000
    assert _scan_for_rtt_control_block(jlink, ((0x20000000, 0x4000),)) is None


def test_capture_pmu_passes_soc_rtt_scan_ranges(tmp_path: Path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "tflm"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir()
    ctx.resolved_jlink_serial = "1160002204"
    ctx.weights_region = "mram"

    captured: dict[str, object] = {}

    def fake_capture_rtt_output(**kwargs):
        captured.update(kwargs)
        return ["--- HPX_START ---", "--- HPX_PRESET basic_cpu ---", "--- HPX_ITER 0 ---", "Layer,Op,ARM_PMU_CPU_CYCLES", "0,CONV_2D,1", "--- HPX_END ---"]

    monkeypatch.setattr("helia_profiler.capture.rtt_reader.capture_rtt_output", fake_capture_rtt_output)

    result = capture_pmu(ctx)

    assert result.layers[0].cycles == 1
    assert captured["rtt_scan_ranges"] == ctx.soc.rtt_scan_ranges
