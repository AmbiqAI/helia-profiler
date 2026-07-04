from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from helia_profiler.capture import capture_pmu
from helia_profiler.capture.rtt_reader import (
    _direct_rtt_write,
    _direct_rtt_read,
    _scan_for_rtt_control_block,
    _write_rtt_command_api,
    _wipe_rtt_control_blocks,
    capture_rtt_output,
)
from helia_profiler.errors import CaptureError
from helia_profiler.capture.serial_reader import capture_swo_output
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.resolve_platform import ResolvePlatformStage


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
            return [0x30000100, 0x30000200, 4096, 0, 0, 0][:count]
        return [0] * count


class _FakeDirectRttJLink:
    def __init__(self):
        self.rd_off_writes: list[tuple[int, list[int]]] = []

    def memory_read32(self, addr: int, count: int) -> list[int]:
        if addr == 0x20000010:
            return [3]
        if addr == 0x20000018:
            return [0x1234, 0x20001000, 64, 10, 2, 1][:count]
        raise AssertionError(f"unexpected read32 addr=0x{addr:08X} count={count}")

    def memory_read8(self, addr: int, length: int) -> list[int]:
        if addr == 0x20001002 and length == 8:
            return list(b"HPX_LINE")
        raise AssertionError(f"unexpected read8 addr=0x{addr:08X} length={length}")

    def memory_write32(self, addr: int, data: list[int]) -> None:
        self.rd_off_writes.append((addr, data))


class _FakeDirectRttWriteJLink:
    def __init__(self):
        self.byte_writes: list[tuple[int, list[int]]] = []
        self.word_writes: list[tuple[int, list[int]]] = []

    def memory_read32(self, addr: int, count: int) -> list[int]:
        if addr == 0x20000010:
            return [1]
        if addr == 0x20000014:
            return [1]
        if addr == 0x20000030:
            return [0x1234, 0x20002000, 16, 2, 0, 0][:count]
        raise AssertionError(f"unexpected read32 addr=0x{addr:08X} count={count}")

    def memory_write8(self, addr: int, data: list[int]) -> None:
        self.byte_writes.append((addr, data))

    def memory_write32(self, addr: int, data: list[int]) -> None:
        self.word_writes.append((addr, data))


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


def test_direct_rtt_write_advances_wr_off():
    jlink = _FakeDirectRttWriteJLink()

    written = _direct_rtt_write(jlink, block_address=0x20000000, data=b"READY")

    assert written == 5
    assert jlink.byte_writes == [(0x20002002, [82, 69, 65, 68, 89])]
    assert jlink.word_writes == [(0x2000003C, [7])]


def test_api_rtt_write_retries_until_full_command_sent(monkeypatch):
    class _FakeApiRttWriteJLink:
        def __init__(self):
            self.calls: list[list[int]] = []
            self._returns = iter([0, 3, 2])

        def rtt_write(self, channel: int, data: list[int]) -> int:
            assert channel == 0
            self.calls.append(data)
            return next(self._returns)

    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.sleep", lambda _s: None)
    jlink = _FakeApiRttWriteJLink()

    _write_rtt_command_api(jlink, command=b"READY", timeout_s=0.1)

    assert jlink.calls == [
        [82, 69, 65, 68, 89],
        [82, 69, 65, 68, 89],
        [68, 89],
    ]


def test_api_rtt_write_times_out_when_down_buffer_never_accepts_bytes(monkeypatch):
    class _FakeApiRttWriteJLink:
        def rtt_write(self, channel: int, data: list[int]) -> int:
            assert channel == 0
            return 0

    sleeps = {"count": 0}

    def fake_sleep(_s: float) -> None:
        sleeps["count"] += 1

    times = iter([0.0, 0.0, 0.02, 0.04, 0.06])
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.sleep", fake_sleep)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.monotonic", lambda: next(times))

    with pytest.raises(CaptureError, match="Timed out sending RTT host-ready command"):
        _write_rtt_command_api(_FakeApiRttWriteJLink(), command=b"READY", timeout_s=0.05)

    assert sleeps["count"] >= 1


def test_wipe_rtt_control_blocks_validates_candidates_and_honors_range_end():
    class _FakeWipeJLink:
        def __init__(self):
            self.read_lengths: list[int] = []
            self.writes: list[tuple[int, list[int]]] = []

        def memory_read8(self, addr: int, length: int) -> list[int]:
            self.read_lengths.append(length)
            if addr == 0x30000000:
                chunk = bytearray(length)
                chunk[0:10] = b"SEGGER RTT"
                if length >= 42:
                    chunk[32:42] = b"SEGGER RTT"
                return list(chunk)
            if addr == 0x30000100:
                return list((b"HPX\x00" + b"\x00" * length)[:length])
            return [0] * length

        def memory_read32(self, addr: int, count: int) -> list[int]:
            if addr == 0x30000010:
                return [1]
            if addr == 0x30000018:
                return [0x30000100, 0x30000200, 4096, 1, 0, 0][:count]
            if addr == 0x30000030:
                return [0, 0, 0, 0, 0, 0][:count]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            self.writes.append((addr, data))

    jlink = _FakeWipeJLink()

    wiped = _wipe_rtt_control_blocks(jlink, ((0x30000000, 42),))

    assert wiped == 1
    assert jlink.read_lengths[0] == 42
    assert jlink.writes == [(0x30000000, [0] * 16)]


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
            timing_out["rtt_phase_reset_s"] = 0.05
            timing_out["rtt_phase_sbl_settle_s"] = 0.2
            timing_out["rtt_phase_attach_s"] = 0.1
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
    assert ctx.run_metadata.timing.phases == {
        "reset": 0.05,
        "sbl_settle": 0.2,
        "attach": 0.1,
    }


def test_capture_pmu_passes_known_block_address_from_map(tmp_path: Path, monkeypatch):
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
    (ctx.build_dir / "hpx_profiler.map").write_text(
        "                0x20088010                _SEGGER_RTT\n"
    )
    ctx.resolved_jlink_serial = "1160002204"
    ctx.weights_region = "mram"

    captured: dict[str, object] = {}

    def fake_capture_rtt_output(**kwargs):
        captured.update(kwargs)
        return ["--- HPX_START ---", "--- HPX_PRESET basic_cpu ---", "--- HPX_ITER 0 ---", "Layer,Op,ARM_PMU_CPU_CYCLES", "0,CONV_2D,1", "--- HPX_END ---"]

    monkeypatch.setattr("helia_profiler.capture.rtt_reader.capture_rtt_output", fake_capture_rtt_output)

    capture_pmu(ctx)

    assert captured["known_block_address"] == 0x20088010



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


def test_capture_pmu_swo_requires_resolved_cpu_clock(tmp_path: Path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"transport": "swo"},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir()
    ctx.resolved_jlink_serial = "1160002204"
    # Simulate a platform whose clock failed to resolve — the host must refuse
    # to guess a SWO baud rather than silently assume a default frequency.
    ctx.run_metadata.platform.cpu_clock_mhz = 0

    with pytest.raises(CaptureError, match="resolved trace clock"):
        capture_pmu(ctx)


def test_capture_pmu_swo_uses_fixed_trace_clock_on_apollo3(tmp_path: Path, monkeypatch):
    """Apollo3 SWO baud is fixed (CPU-independent), so burst must not change it."""
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {
                "board": "apollo3p_evb",
                "transport": "swo",
                "clock": {"cpu": "hp"},  # 96 MHz burst
            },
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir()
    ctx.resolved_jlink_serial = "1160000174"

    # CPU runs at 96 MHz under burst, but the TPIU trace clock stays at 48 MHz.
    assert ctx.run_metadata.platform.cpu_clock_mhz == 96
    assert ctx.soc.swo_trace_clock_mhz == 48

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

    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.capture_swo_output", fake_capture_swo_output
    )

    capture_pmu(ctx)

    # Host must program J-Link's SWO prescaler against 48 MHz, not the 96 MHz
    # burst core clock, or the ITM stream is undecodable.
    assert captured["cpu_freq"] == 48_000_000



def test_capture_pmu_warns_when_device_clock_disagrees(tmp_path: Path, monkeypatch, caplog):
    import logging

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

    def fake_capture_swo_output(**kwargs):
        # Registry assumed 250 MHz (hp); device actually ran at 96 MHz.
        return [
            "--- HPX_START ---",
            "HPX_SYSTEM_CLOCK_HZ=96000000",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1",
            "--- HPX_END ---",
        ]

    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.capture_swo_output", fake_capture_swo_output
    )

    with caplog.at_level(logging.WARNING, logger="hpx"):
        capture_pmu(ctx)

    assert any("Device reports CPU clock" in r.message for r in caplog.records)


def test_capture_pmu_no_clock_warning_when_device_clock_matches(tmp_path: Path, monkeypatch, caplog):
    import logging

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

    def fake_capture_swo_output(**kwargs):
        # Device reports the same 250 MHz the registry assumed (hp).
        return [
            "--- HPX_START ---",
            "HPX_SYSTEM_CLOCK_HZ=250000000",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1",
            "--- HPX_END ---",
        ]

    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.capture_swo_output", fake_capture_swo_output
    )

    with caplog.at_level(logging.WARNING, logger="hpx"):
        capture_pmu(ctx)

    assert not any("Device reports CPU clock" in r.message for r in caplog.records)


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


def test_capture_pmu_rejects_rtt_protocol_without_hpx_start(tmp_path: Path, monkeypatch):
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

    def fake_capture_rtt_output(**kwargs):
        return [
            "HPX_HEARTBEAT phase=infer pass=1 iter=4 layer=24",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1",
            "--- HPX_END ---",
        ]

    monkeypatch.setattr("helia_profiler.capture.rtt_reader.capture_rtt_output", fake_capture_rtt_output)

    from helia_profiler.errors import CaptureError

    try:
        capture_pmu(ctx)
    except CaptureError as exc:
        assert "does not contain HPX_START sentinel" in str(exc)
    else:
        raise AssertionError("expected CaptureError for missing HPX_START")


def test_capture_rtt_output_does_not_send_down_channel_command(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.commands: list[tuple[int, list[int]]] = []
            self.reads = [b"HPX_READY\n"]

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
            return False

        def memory_read8(self, addr: int, length: int) -> list[int]:
            data = b"HPX\x00" if addr == 0x20000100 else b"SEGGER RTT"
            return list((data + b"\x00" * length)[:length])

        def memory_read32(self, addr: int, count: int) -> list[int]:
            if addr == 0x20000010:
                return [1]
            if addr == 0x20000018:
                return [0x20000100, 0x20000200, 4096, 16, 0, 0][:count]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            if self.reads:
                return list(self.reads.pop(0))
            return []

        def rtt_write(self, buffer_index, data):
            self.commands.append((buffer_index, data))

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
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_reader.collect_lines",
        lambda *args, **kwargs: ["--- HPX_START ---", "--- HPX_END ---"],
    )

    capture_rtt_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
        rtt_scan_ranges=((0x20000000, 0x4000),),
    )

    # The firmware now streams the HPX_START header losslessly on the up-channel
    # and never waits on a host->target command, so the host must not write to
    # the RTT down-channel during the ready handshake.
    assert fake_jlink.commands == []


def test_capture_rtt_output_preserves_bytes_after_hpx_ready(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.reads = [b"HPX_READY\n--- HPX_START ---\nHPX_VERSION=1\n--- HPX_END ---\n"]

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
            return False

        def memory_read8(self, addr: int, length: int) -> list[int]:
            data = b"HPX\x00" if addr == 0x20000100 else b"SEGGER RTT"
            return list((data + b"\x00" * length)[:length])

        def memory_read32(self, addr: int, count: int) -> list[int]:
            if addr == 0x20000010:
                return [1]
            if addr == 0x20000018:
                return [0x20000100, 0x20000200, 4096, 16, 0, 0][:count]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

        def memory_write32(self, addr: int, data: list[int]) -> None:
            return None

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            if self.reads:
                return list(self.reads.pop(0))
            return []

        def rtt_write(self, buffer_index, data):
            return None

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

    lines = capture_rtt_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
        rtt_scan_ranges=((0x20000000, 0x4000),),
        heartbeat_timeout_s=1.0,
    )

    assert lines == ["HPX_READY", "--- HPX_START ---", "HPX_VERSION=1", "--- HPX_END ---"]


def test_capture_rtt_output_restarts_halted_target(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.restart_calls = 0
            self._halted = True
            self.reads = [b"HPX_READY\n"]

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
                return [0x20000100, 0x20000200, 4096, 16, 0, 0][:count]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

        def memory_write32(self, addr: int, data: list[int]) -> None:
            return None

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            if self.reads:
                return list(self.reads.pop(0))
            return []

        def rtt_write(self, buffer_index, data):
            return None

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
            self.reads = [b"HPX_READY\n"]

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
                return [0x20000100, 0x20000200, 4096, 16, 0, 0][:count]
            return [0] * count

        def memory_write8(self, addr: int, data: list[int]) -> None:
            return None

        def memory_write32(self, addr: int, data: list[int]) -> None:
            return None

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            if self.reads:
                return list(self.reads.pop(0))
            return []

        def rtt_write(self, buffer_index, data):
            return None

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


def test_capture_rtt_output_tolerates_preclean_attach_failure(monkeypatch):
    # When the pre-clean attach fails the run must still complete, discovering
    # the live block through the settle window rather than early-breaking.
    from helia_profiler.errors import CaptureError

    class _FakeStatus:
        NumUpBuffers = 1

    class _FakeJLinkHandle:
        def __init__(self):
            self.reads = [b"HPX_READY\n"]

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

        def memory_write32(self, addr: int, data: list[int]) -> None:
            return None

        def rtt_start(self, block_address=None):
            return None

        def rtt_get_status(self):
            return _FakeStatus()

        def rtt_read(self, buffer_index, length):
            if self.reads:
                return list(self.reads.pop(0))
            return []

        def rtt_write(self, buffer_index, data):
            return None

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

    attach_calls = {"n": 0}

    def fake_open(jlink, **kwargs):
        attach_calls["n"] += 1
        if attach_calls["n"] == 1:
            raise CaptureError("pre-clean attach failed")
        return None

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.open_jlink_with_retry", fake_open)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.reset_target", lambda **kwargs: None)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader._RTT_DISCOVERY_SETTLE_S", 0.0)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.collect_lines", lambda *args, **kwargs: [])

    capture_rtt_output(
        jlink_serial="1160002204",
        jlink_device="AP510BFA-CBR",
        rtt_scan_ranges=((0x20000000, 0x4000),),
    )

    # Pre-clean attach raised, capture phase attach succeeded: two attempts.
    assert attach_calls["n"] == 2


def test_capture_rtt_output_direct_fallback_rescans_only_after_idle_backoff(monkeypatch):
    class _FakeStatus:
        NumUpBuffers = 0

    class _FakeJLinkHandle:
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
            return False

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

    allow_rescan_calls: list[bool] = []

    def fake_direct_rtt_read_any(*_args, **kwargs):
        allow_rescan_calls.append(kwargs["allow_rescan"])
        call_index = len(allow_rescan_calls)
        if call_index == 1:
            return b"HPX_READY\n", kwargs["preferred_block_address"]
        if call_index <= 21:
            return b"", kwargs["preferred_block_address"]
        return b"--- HPX_START ---\n--- HPX_END ---\n", kwargs["preferred_block_address"]

    def fake_collect_lines(read_chunk, **_kwargs):
        for _ in range(22):
            read_chunk()
        return ["--- HPX_START ---", "--- HPX_END ---"]

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.reset_target", lambda **kwargs: None)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.time.sleep", lambda _: None)
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_reader._scan_for_rtt_control_block",
        lambda *_args, **_kwargs: (0x20000000, 123),
    )
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_reader._direct_rtt_read_any",
        fake_direct_rtt_read_any,
    )
    monkeypatch.setattr("helia_profiler.capture.rtt_reader._RTT_CB_TIMEOUT_S", 0.1)
    monkeypatch.setattr("helia_profiler.capture.rtt_reader._RTT_DISCOVERY_SETTLE_S", 0.0)
    monkeypatch.setattr(
        "helia_profiler.capture.rtt_reader._write_rtt_command_direct",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("helia_profiler.capture.rtt_reader.collect_lines", fake_collect_lines)

    capture_rtt_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
        rtt_scan_ranges=((0x20000000, 0x4000),),
    )

    assert allow_rescan_calls[0] is False
    assert all(flag is False for flag in allow_rescan_calls[1:21])
    assert allow_rescan_calls[21] is True


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


def test_capture_swo_output_retries_once_after_empty_capture(monkeypatch):
    class _FakeJLinkHandle:
        def open(self, serial_no=None):
            return None

        def disable_dialog_boxes(self):
            return None

        def set_tif(self, tif):
            return None

        def connect(self, device, speed):
            return None

        def halted(self):
            return False

        def swo_enable(self, cpu_speed, swo_speed, port_mask):
            return None

        def swo_read_stimulus(self, port, length):
            return []

        def swo_stop(self):
            return None

        def close(self):
            return None

    fake_pylink = types.SimpleNamespace(
        JLink=lambda: _FakeJLinkHandle(),
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=Exception),
    )

    reset_calls: list[tuple[str, str | None]] = []
    collect_call_count = {"count": 0}

    def fake_collect_lines(*args, **kwargs):
        collect_call_count["count"] += 1
        if collect_call_count["count"] == 1:
            return []
        return ["--- HPX_START ---", "--- HPX_END ---"]

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.reset_target",
        lambda **kwargs: reset_calls.append((kwargs["device"], kwargs.get("jlink_serial"))),
    )
    monkeypatch.setattr("helia_profiler.capture.serial_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.collect_lines", fake_collect_lines)

    lines = capture_swo_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
    )

    assert lines == ["--- HPX_START ---", "--- HPX_END ---"]
    assert collect_call_count["count"] == 2
    assert reset_calls == [
        ("AP510NFA-CBR", "1160002204"),
        ("AP510NFA-CBR", "1160002204"),
    ]


def test_capture_swo_output_retries_when_start_sentinel_missing(monkeypatch):
    """A partial capture missing HPX_START is the SWO startup race — retry."""

    class _FakeJLinkHandle:
        def open(self, serial_no=None):
            return None

        def disable_dialog_boxes(self):
            return None

        def set_tif(self, tif):
            return None

        def connect(self, device, speed):
            return None

        def halted(self):
            return False

        def swo_enable(self, cpu_speed, swo_speed, port_mask):
            return None

        def swo_read_stimulus(self, port, length):
            return []

        def swo_stop(self):
            return None

        def close(self):
            return None

    fake_pylink = types.SimpleNamespace(
        JLink=lambda: _FakeJLinkHandle(),
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=Exception),
    )

    reset_calls: list[tuple[str, str | None]] = []
    collect_call_count = {"count": 0}

    def fake_collect_lines(*args, **kwargs):
        collect_call_count["count"] += 1
        if collect_call_count["count"] == 1:
            # Head lost to the startup race: data but no start sentinel.
            return ["0,CONV_2D,1", "--- HPX_END ---"]
        return ["--- HPX_START ---", "0,CONV_2D,1", "--- HPX_END ---"]

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.reset_target",
        lambda **kwargs: reset_calls.append((kwargs["device"], kwargs.get("jlink_serial"))),
    )
    monkeypatch.setattr("helia_profiler.capture.serial_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.collect_lines", fake_collect_lines)

    lines = capture_swo_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
    )

    assert lines == ["--- HPX_START ---", "0,CONV_2D,1", "--- HPX_END ---"]
    assert collect_call_count["count"] == 2


def test_capture_swo_output_returns_partial_after_final_attempt(monkeypatch):
    """If every attempt loses the start sentinel, return the last capture.

    Downstream validation raises the precise "no HPX_START" error; the reader
    must not loop forever on a target that genuinely never emits the sentinel.
    """

    class _FakeJLinkHandle:
        def open(self, serial_no=None):
            return None

        def disable_dialog_boxes(self):
            return None

        def set_tif(self, tif):
            return None

        def connect(self, device, speed):
            return None

        def halted(self):
            return False

        def swo_enable(self, cpu_speed, swo_speed, port_mask):
            return None

        def swo_read_stimulus(self, port, length):
            return []

        def swo_stop(self):
            return None

        def close(self):
            return None

    fake_pylink = types.SimpleNamespace(
        JLink=lambda: _FakeJLinkHandle(),
        JLinkInterfaces=types.SimpleNamespace(SWD=1),
        errors=types.SimpleNamespace(JLinkException=Exception),
    )

    collect_call_count = {"count": 0}

    def fake_collect_lines(*args, **kwargs):
        collect_call_count["count"] += 1
        return ["0,CONV_2D,1", "--- HPX_END ---"]

    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setattr(
        "helia_profiler.capture.serial_reader.reset_target", lambda **kwargs: None
    )
    monkeypatch.setattr("helia_profiler.capture.serial_reader.time.sleep", lambda _: None)
    monkeypatch.setattr("helia_profiler.capture.serial_reader.collect_lines", fake_collect_lines)

    from helia_profiler.capture.serial_reader import _MAX_CAPTURE_ATTEMPTS

    lines = capture_swo_output(
        jlink_serial="1160002204",
        jlink_device="AP510NFA-CBR",
    )

    assert lines == ["0,CONV_2D,1", "--- HPX_END ---"]
    assert collect_call_count["count"] == _MAX_CAPTURE_ATTEMPTS
