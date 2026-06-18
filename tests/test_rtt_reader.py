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
from helia_profiler.capture.serial_reader import capture_swo_output
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage


class _FakeJLink:
    def __init__(self, memory: dict[int, bytes]):
        self._memory = memory

    def memory_read8(self, addr: int, length: int) -> list[int]:
        return list(self._memory.get(addr, b"\x00" * length))

    def memory_read32(self, addr: int, count: int) -> list[int]:
        # Model a single valid control block at 0x30000000 so scoring accepts it.
        if addr == 0x30000010:
            return [1]
        if addr == 0x30000018:
            return [0x30000100, 0x30000200, 4096, 0, 0, 0]
        return [0] * count


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

    result = _scan_for_rtt_control_block(jlink, ((0x30000000, 0x4000),))
    assert result is not None and result[0] == 0x30000000
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
            "engine": {"type": "helia-rt"},
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
        timing_out = kwargs.get("timing_out")
        if timing_out is not None:
            timing_out["capture_duration_s"] = 1.25
            timing_out["hpx_start_latency_s"] = 0.2
            timing_out["protocol_duration_s"] = 0.75
        return ["--- HPX_START ---", "--- HPX_PRESET basic_cpu ---", "--- HPX_ITER 0 ---", "Layer,Op,ARM_PMU_CPU_CYCLES", "0,CONV_2D,1", "--- HPX_END ---"]

    monkeypatch.setattr("helia_profiler.capture.rtt_reader.capture_rtt_output", fake_capture_rtt_output)

    result = capture_pmu(ctx)

    assert result.layers[0].cycles == 1
    assert captured["jlink_device"] == ctx.soc.jlink_device
    assert captured["rtt_scan_ranges"] == ctx.soc.rtt_scan_ranges
    assert ctx.run_metadata.timing is not None
    assert ctx.run_metadata.timing.capture_duration_s == 1.25
    assert ctx.run_metadata.timing.hpx_start_latency_s == 0.2
    assert ctx.run_metadata.timing.protocol_duration_s == 0.75


def test_capture_pmu_passes_resolved_cpu_clock_to_swo(tmp_path: Path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"transport": "swo", "clock": {"cpu": "hp"}},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir()
    ctx.resolved_jlink_serial = "1160002204"

    captured: dict[str, object] = {}

    def fake_capture_swo_output(**kwargs):
        captured.update(kwargs)
        return [
            "--- HPX_START ---",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1",
            "--- HPX_END ---",
        ]

    monkeypatch.setattr("helia_profiler.capture.serial_reader.capture_swo_output", fake_capture_swo_output)

    result = capture_pmu(ctx)

    assert result.layers[0].cycles == 1
    assert captured["jlink_device"] == ctx.soc.jlink_device
    assert captured["cpu_freq"] == 250_000_000


def test_capture_pmu_passes_resolved_jlink_device_to_usb(tmp_path: Path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"transport": "usb_cdc"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir()
    ctx.resolved_jlink_serial = "1160002204"

    captured: dict[str, object] = {}

    def fake_capture_usb_output(**kwargs):
        captured.update(kwargs)
        return [
            "--- HPX_START ---",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1",
            "--- HPX_END ---",
        ]

    monkeypatch.setattr("helia_profiler.capture.usb_reader.capture_usb_output", fake_capture_usb_output)

    result = capture_pmu(ctx)

    assert result.layers[0].cycles == 1
    assert captured["jlink_device"] == ctx.soc.jlink_device


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

        def halt(self):
            return None

        def halted(self):
            return self._halted

        def restart(self):
            self.restart_calls += 1
            self._halted = False
            return True

        def memory_read8(self, addr: int, length: int) -> list[int]:
            # The channel-0 name pointer resolves to "HPX"; everything else
            # carries the control-block magic so the scan can locate it.
            data = b"HPX\x00" if addr == 0x20000100 else b"SEGGER RTT"
            return list((data + b"\x00" * length)[:length])

        def memory_read32(self, addr: int, count: int) -> list[int]:
            if addr == 0x20000010:
                return [1]
            if addr == 0x20000018:
                return [0x20000100, 0x20000200, 4096, 16, 0, 0]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

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


def test_capture_rtt_output_retries_attach_until_target_ready(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.open_calls = 0

        def open(self, serial_no=None):
            self.open_calls += 1
            return None

        def disable_dialog_boxes(self):
            return None

        def set_tif(self, tif):
            return None

        def connect(self, device, speed):
            if self.open_calls == 1:
                raise Exception("target not ready")
            return None

        def halt(self):
            return None

        def halted(self):
            return False

        def memory_read8(self, addr: int, length: int) -> list[int]:
            data = b"HPX\x00" if addr == 0x20000100 else b"SEGGER RTT"
            return list((data + b"\x00" * length)[:length])

        def memory_read32(self, addr: int, count: int) -> list[int]:
            if addr == 0x20000010:
                return [1]
            if addr == 0x20000018:
                return [0x20000100, 0x20000200, 4096, 16, 0, 0]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

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

    # Pre-clean attaches once (absorbing the first not-ready failure via retry)
    # and the capture phase attaches again, so open() is called three times.
    assert fake_jlink.open_calls == 3


def test_capture_swo_output_restarts_halted_target(monkeypatch):
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

        def swo_enable(self, cpu_speed, swo_speed, port_mask):
            return None

        def swo_read_stimulus(self, port, length):
            return []

        def swo_stop(self):
            return None

        def close(self):
            return None

    fake_jlink = _FakeJLinkHandle()
    fake_pylink = types.SimpleNamespace(
        JLink=lambda: fake_jlink,
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=Exception),
    )

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.reset_target", lambda **kwargs: None)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.collect_lines", lambda *args, **kwargs: [])

    capture_swo_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
    )

    assert fake_jlink.restart_calls == 1
