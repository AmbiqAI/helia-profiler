from __future__ import annotations

import sys
import types
from pathlib import Path

from helia_profiler.capture import capture_pmu
from helia_profiler.capture.rtt_reader import (
    _direct_rtt_read,
    _scan_for_rtt_control_block,
    capture_rtt_output,
)
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage


class _FakeJLink:
    def __init__(self, memory: dict[int, bytes]):
        self._memory = memory

    def memory_read8(self, addr: int, length: int) -> list[int]:
        return list(self._memory.get(addr, b"\x00" * length))


class _FakeDirectRttJLink:
    def __init__(self):
        self.rd_off_writes: list[tuple[int, list[int]]] = []

    def memory_read32(self, addr: int, count: int) -> list[int]:
        if addr == 0x20000010:
            return [3]
        if addr == 0x20000018:
            return [0x1234, 0x20001000, 64, 10, 2, 1]
        raise AssertionError(f"unexpected read32 addr=0x{addr:08X} count={count}")

    def memory_read8(self, addr: int, length: int) -> list[int]:
        if addr == 0x20001002 and length == 8:
            return list(b"HPX_LINE")
        raise AssertionError(f"unexpected read8 addr=0x{addr:08X} length={length}")

    def memory_write32(self, addr: int, data: list[int]) -> None:
        self.rd_off_writes.append((addr, data))


def test_scan_for_rtt_control_block_uses_provided_ranges():
    magic = b"SEGGER RTT"
    chunk = magic + b"\x00" * (0x4000 - len(magic))
    jlink = _FakeJLink({0x30000000: chunk})

    assert _scan_for_rtt_control_block(jlink, ((0x30000000, 0x4000),)) == 0x30000000
    assert _scan_for_rtt_control_block(jlink, ((0x20000000, 0x4000),)) is None


def test_direct_rtt_read_advances_rd_off():
    jlink = _FakeDirectRttJLink()

    data = _direct_rtt_read(jlink, block_address=0x20000000, max_bytes=16)

    assert data == b"HPX_LINE"
    assert jlink.rd_off_writes == [(0x20000028, [10])]


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


def test_capture_rtt_output_restarts_halted_target(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.restart_calls = 0
            self._halted = True

        def open(self, serial_no=None):
            return None

        def disable_dialog_boxes(self):
            return None

        def set_tif(self, tif):
            return None

        def connect(self, device, speed):
            return None

        def halted(self):
            return self._halted

        def restart(self):
            self.restart_calls += 1
            self._halted = False
            return True

        def memory_read8(self, addr: int, length: int) -> list[int]:
            data = b"SEGGER RTT" + b"\x00" * max(0, length - len("SEGGER RTT"))
            return list(data[:length])

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            return []

        def rtt_stop(self):
            return None

        def close(self):
            return None

    fake_jlink = _FakeJLinkHandle()
    fake_pylink = types.SimpleNamespace(
        JLink=lambda: fake_jlink,
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=Exception, JLinkRTTException=Exception),
    )

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.reset_target", lambda **kwargs: None)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.collect_lines", lambda *args, **kwargs: [])

    capture_rtt_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
        rtt_scan_ranges=((0x20000000, 0x4000),),
    )

    assert fake_jlink.restart_calls == 1
