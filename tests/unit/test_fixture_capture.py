"""Mocked raw capture checks preserve evidence and reject unsafe images."""

import json
import re
import struct
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from helia_profiler import fixture_capture as capture
from helia_profiler.fixture import FixtureTimingScope
from helia_profiler.fixture_image import Sink, inspect_elf
from helia_profiler.fixture_runtime import FixtureFile
from helia_profiler.fixture_stage import FixtureStage
from helia_profiler.fixture_target import supported_fixture_target

_SINKS = {
    "deployment_status": 4,
    "deployment_output": 3,
    "deployment_checksum": 4,
    "deployment_timing": 28,
}


def image_files(tmp_path, sinks=_SINKS, *, align=4, binary=b"\x01\x02\x03\x04"):
    names, sizes = list(sinks), list(sinks.values())
    addresses = [0x20000000]
    for size in sizes[:-1]:
        addresses.append(addresses[-1] + (size + align - 1) // align * align)
    extent = max(64, addresses[-1] + sizes[-1] - 0x20000000)
    strings = b"\0" + b"".join(n.encode() + b"\0" for n in names)
    symbols = b"\0" * 16
    for name, size, address in zip(names, sizes, addresses):
        symbols += struct.pack(
            "<IIIBBH", strings.index(name.encode() + b"\0"), address, size, 0x11, 0, 1
        )
    data = bytearray(1024)
    data[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(
        "<HHIIIIIHHHHHH", data, 16, 2, 40, 1, 0x410001, 52, 128, 0, 52, 32, 2, 40, 4, 2
    )
    struct.pack_into("<8I", data, 52, 1, 1024, 0x410000, 0x410000, len(binary), len(binary), 5, 1)
    struct.pack_into("<8I", data, 84, 1, 0, 0x20000000, 0, 0, extent, 6, 1)
    struct.pack_into("<10I", data, 168, 0, 8, 3, 0x20000000, 0, extent, 0, 0, 4, 0)
    struct.pack_into("<10I", data, 208, 0, 3, 0, 0, 288, len(strings), 0, 0, 1, 0)
    struct.pack_into("<10I", data, 248, 0, 2, 0, 0, 648, len(symbols), 2, 0, 4, 16)
    data[288 : 288 + len(strings)] = strings
    data[648 : 648 + len(symbols)] = symbols
    data += binary

    def pin(name, raw):
        path = tmp_path / name
        path.write_bytes(raw)
        return FixtureFile(path, sha256(raw).hexdigest())

    return pin("image.elf", data), pin("source.bin", binary), dict(zip(names, sizes))


class Guard:
    def __init__(self):
        self.calls = []

    def verify_target(self, *, target, jlink_serial):
        assert target == supported_fixture_target() and jlink_serial == "123"

    def check(self, *, require_free, remaining_s):
        self.calls.append((require_free, remaining_s))


class _Clock:
    """Capture's clock in tests: each reading advances one tick and a sleep advances
    by its argument, so a slow runner's real elapsed time never decides a
    completion window."""

    TICK_S = 1e-4

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        self.now += self.TICK_S
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    elf, image, sizes = image_files(tmp_path)
    request = capture.FixtureCaptureRequest(
        elf,
        image,
        0x410000,
        3,
        "AP510NFA-CBR",
        "123",
        tmp_path / "evidence",
        0.01,
        FixtureTimingScope.INVOKE_ONLY,
        supported_fixture_target(),
    )
    memory = {0x410000: image.read(), 0xE000ED00: struct.pack("<I", 0xD22 << 4)}
    writes = []

    core = {"halted": False}

    class Session:
        def halt(self):
            core["halted"] = True

        def halted(self):
            return core["halted"]

        def restart(self):
            core["halted"] = False

        def memory_read8(self, address, count):
            if address in memory:
                return memory[address][:count]
            base = next(b for b in memory if b <= address < b + len(memory[b]))
            return memory[base][address - base : address - base + count]

        def memory_write8(self, address, values):
            writes.append((address, bytes(values)))
            memory[address] = bytes(values)

    @contextmanager
    def attach(**kwargs):
        yield Session()

    output = bytes([1, 2, 3])
    terminal = {
        0x20000000: struct.pack("<i", 0),
        0x20000004: output,
        0x20000008: struct.pack("<I", 1026),
        0x2000000C: struct.pack("<7I", 50, 10, 2, 32768, 300, 96000000, 1),
    }
    flashes = []
    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "flash_binary", lambda **kwargs: flashes.append(kwargs))

    def reset(**kwargs):
        memory.update(terminal)
        core["halted"] = False

    monkeypatch.setattr(capture, "reset_target", reset)
    monkeypatch.setattr(capture, "list_connected_probes", lambda: [SimpleNamespace(serial="123")])
    monkeypatch.setattr(capture, "time", _Clock())
    return request, memory, terminal, writes, flashes, Guard()


def test_capture_raw_success(rig):
    request, memory, terminal, writes, flashes, guard = rig
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
    assert result.output is not None
    assert result.output.read() == bytes([1, 2, 3])
    assert result.timing is not None
    assert result.timing.iterations == 10
    assert [len(v) for _, v in writes] == [4, 3, 4, 28] or sorted(len(v) for _, v in writes) == [
        3,
        4,
        4,
        28,
    ]
    assert all(v == b"\xa5" * len(v) for _, v in writes)
    assert flashes[0]["binary_path"] == request.evidence_dir / "image.bin"
    identity = json.loads((request.evidence_dir / "identity.json").read_text())
    assert identity["elf_sha256"] == request.elf.sha256
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["resumed_after_attach"] is False
    assert len(identity["sinks"]) == 4
    assert any(not free for free, _ in guard.calls)


@pytest.mark.parametrize("fault", ["status", "checksum", "timing", "readback", "hash"])
def test_failure_preserves_receipt_and_no_retry(rig, fault):
    request, memory, terminal, writes, flashes, guard = rig
    if fault == "status":
        terminal[0x20000000] = struct.pack("<i", -3)
    if fault == "checksum":
        terminal[0x20000008] = b"\0" * 4
    if fault == "timing":
        terminal[0x2000000C] = b"\xa5" * 28
    if fault == "readback":
        memory[0x410000] = b"bad!"
    if fault == "hash":
        request.elf.path.write_bytes(b"x" * 512)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert len(flashes) <= 1
    assert (request.evidence_dir / "started.json").exists()
    assert json.loads((request.evidence_dir / "receipt.json").read_text())["error"]
    if fault in ("status", "checksum", "timing"):
        assert result.output is not None
        assert result.output.read() == bytes([1, 2, 3])


def test_guard_denial_stops_before_probe(rig, monkeypatch):
    request, _, _, _, flashes, _ = rig

    class Denied(Guard):
        def check(self, **kwargs):
            raise PermissionError("denied by caller")

    monkeypatch.setattr(capture, "list_connected_probes", lambda: pytest.fail("probe reached"))
    result = capture.capture_fixture(request, guard=Denied())
    assert result.error == "denied by caller"
    assert not flashes


def test_elf_extent_and_binary_identity(tmp_path):
    elf, image, sizes = image_files(tmp_path)
    assert len(inspect_elf(elf.read(), image.read(), 0x410000, sizes).sinks) == 4
    with pytest.raises(ValueError, match="differ"):
        inspect_elf(elf.read(), b"bad!", 0x410000, sizes)
    with pytest.raises(ValueError, match="sink size"):
        inspect_elf(elf.read(), image.read(), 0x410000, dict(sizes, deployment_output=4))


def test_unstable_snapshot_preserves_first_raw_snapshot(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    calls = 0
    original_attach = capture.attached_session

    @contextmanager
    def attach(**kwargs):
        nonlocal calls
        calls += 1
        with original_attach(**kwargs) as session:
            if calls == 3:
                original_read = session.memory_read8
                output_reads = 0

                def read(address, count):
                    nonlocal output_reads
                    if address == 0x20000004:
                        output_reads += 1
                        if output_reads == 2:
                            return b"bad"
                    return original_read(address, count)

                monkeypatch.setattr(session, "memory_read8", read)
            yield session

    monkeypatch.setattr(capture, "attached_session", attach)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error == "Unstable completed snapshot"
    assert (request.evidence_dir / "deployment_output.bin").read_bytes() == bytes([1, 2, 3])


def test_memory_terminal_phase_contract():
    words = [0x4D454D31, 1, 1024, 100, 120, 140, 1]
    check = 0
    for word in words:
        check = (check * 31 + word) & 0xFFFFFFFF
    raw = struct.pack("<8I", *words, check)
    result = capture._memory(raw, 1024)
    assert (result.after_io_access, result.after_warmup, result.after_invoke) == (100, 120, 140)
    with pytest.raises(ValueError, match="versioned memory"):
        capture._memory(raw, 2048)
    with pytest.raises(ValueError, match="versioned memory"):
        capture._memory(raw[:-4] + b"\0" * 4, 1024)


def test_existing_evidence_is_not_overwritten(rig):
    request, _, _, _, flashes, guard = rig
    request.evidence_dir.mkdir()
    with pytest.raises(FileExistsError):
        capture.capture_fixture(request, guard=guard)
    assert not flashes


def test_timing_scope_is_proven_by_firmware(rig):
    request, _, _, _, _, guard = rig
    request = replace(request, timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error is not None and "Timing scope differs" in result.error
    assert result.timing is not None
    assert result.timing.timing_scope is FixtureTimingScope.INVOKE_ONLY


@pytest.mark.parametrize("index,value", [(3, 1000), (4, 1), (4, 411), (5, 192000000), (6, 0)])
def test_capture_rejects_unsupported_timing_terminal(rig, index, value):
    request, _, terminal, _, _, guard = rig
    words = list(struct.unpack("<7I", terminal[0x2000000C]))
    words[index] = value
    terminal[0x2000000C] = struct.pack("<7I", *words)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.output is not None


@pytest.mark.parametrize("pass_number", [1, 2])
@pytest.mark.parametrize("short", [False, True])
def test_partial_snapshot_preserves_received_evidence(rig, monkeypatch, pass_number, short):
    request, _, terminal, _, flashes, guard = rig
    original_attach = capture.attached_session
    attachments = 0

    @contextmanager
    def attach(**kwargs):
        nonlocal attachments
        attachments += 1
        with original_attach(**kwargs) as session:
            if attachments == 3:
                original_read = session.memory_read8
                timing_reads = 0

                def read(address, count):
                    nonlocal timing_reads
                    if address == 0x2000000C:
                        timing_reads += 1
                        if timing_reads == pass_number:
                            if short:
                                return terminal[address][:5]
                            raise OSError("timing read failed")
                    return original_read(address, count)

                monkeypatch.setattr(session, "memory_read8", read)
            yield session

    monkeypatch.setattr(capture, "attached_session", attach)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error == ("Short target read" if short else "timing read failed")
    assert result.output is not None and result.output.read() == bytes([1, 2, 3])
    assert len(flashes) == 1
    suffix = "-verification" if pass_number == 2 else ""
    for name, address in (("checksum", 0x20000008), ("output", 0x20000004), ("status", 0x20000000)):
        path = request.evidence_dir / f"deployment_{name}{suffix}.bin"
        assert path.read_bytes() == terminal[address]
        assert any(ref.path == path and ref.read() == terminal[address] for ref in result.artifacts)
    timing_path = request.evidence_dir / f"deployment_timing{suffix}.bin"
    if short:
        assert timing_path.read_bytes() == terminal[0x2000000C][:5]
    else:
        assert not timing_path.exists()
    assert json.loads((request.evidence_dir / "receipt.json").read_text())["state"] == "failure"


def test_consistent_relocated_image_rejected_before_device(rig, monkeypatch):
    request, memory, terminal, writes, flashes, guard = rig
    raw = bytearray(request.elf.read())
    struct.pack_into("<I", raw, 24, 0x420001)
    struct.pack_into("<II", raw, 60, 0x420000, 0x420000)
    request.elf.path.write_bytes(raw)
    moved = replace(
        request, elf=FixtureFile(request.elf.path, sha256(raw).hexdigest()), load_address=0x420000
    )
    memory[0x420000] = request.image.read()
    result = capture.capture_fixture(moved, guard=guard)
    assert result.state == "failure"
    assert result.error is not None and "boot origin" in result.error
    assert not guard.calls and not flashes and not writes


def test_unsupported_device_rejected_before_device(rig):
    request, memory, terminal, writes, flashes, guard = rig
    with pytest.raises(ValueError, match="target"):
        capture.capture_fixture(replace(request, device="AP510BFA-CBR"), guard=guard)
    assert not guard.calls and not flashes and not writes


def test_missing_physical_target_verification_stops_before_device(rig, monkeypatch):
    request, memory, terminal, writes, flashes, guard = rig

    class UnverifiedGuard(Guard):
        def verify_target(self, *, target, jlink_serial):
            raise ValueError("No independent physical board/serial verification")

    monkeypatch.setattr(
        capture, "list_connected_probes", lambda: pytest.fail("probe enumeration reached")
    )
    result = capture.capture_fixture(request, guard=UnverifiedGuard())
    assert result.state == "failure"
    assert result.error is not None and "physical board" in result.error
    assert not flashes and not writes


def test_inconsistent_typed_target_stops_before_device(rig):
    request, memory, terminal, writes, flashes, guard = rig
    bad = replace(request.target, board="apollo510b_evb")
    with pytest.raises(ValueError, match="target"):
        capture.capture_fixture(replace(request, target=bad), guard=guard)
    assert not guard.calls and not flashes and not writes


_RUNNING = {
    0x20000000: struct.pack("<i", -1),
    0x20000004: bytes(3),
    0x20000008: bytes(4),
    0x2000000C: bytes(28),
}


def test_mid_loop_read_waits_for_completion(rig, monkeypatch):
    """Sinks read while the timed loop still runs must not end the capture."""
    request, memory, terminal, _, _, guard = rig
    status_reads = 0
    original_attach = capture.attached_session

    @contextmanager
    def attach(**kwargs):
        with original_attach(**kwargs) as session:
            original_read = session.memory_read8

            def read(address, count):
                nonlocal status_reads
                if address == 0x20000000 and memory[address] == _RUNNING[address]:
                    status_reads += 1
                    if status_reads > 3:
                        memory.update(terminal)
                return original_read(address, count)

            monkeypatch.setattr(session, "memory_read8", read)
            yield session

    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "reset_target", lambda **kwargs: memory.update(_RUNNING))
    result = capture.capture_fixture(replace(request, settle_seconds=5), guard=guard)
    assert result.state == "success", result.error
    assert result.timing is not None and result.timing.iterations == 10
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["status"] == 0 and completion["polls"] == 4


def test_completion_timeout_names_running_stage(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    monkeypatch.setattr(
        capture, "reset_target", lambda **kwargs: memory.update({0x20000000: struct.pack("<i", -1)})
    )
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error is not None
    assert "did not complete within 0.01 s" in result.error and "running" in result.error
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["status"] == -1 and completion["completed"] is False


def test_completion_timeout_names_unstarted_firmware(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    monkeypatch.setattr(capture, "reset_target", lambda **kwargs: None)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error is not None and "not started" in result.error


def test_nonzero_status_names_failing_stage(rig):
    request, memory, terminal, _, _, guard = rig
    terminal[0x20000000] = struct.pack("<i", -7)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error is not None and "invoke" in result.error and "-7" in result.error


def _live_memory(*words):
    return struct.pack("<8I", *words, *([0] * (8 - len(words))))


@pytest.mark.parametrize(
    "status,memory,stage",
    [
        (capture._STATUS_POISON, None, "not started"),
        (-1, None, "no intermediate stage"),
        (-1, _live_memory(), "before tensor allocation"),
        (-1, _live_memory(0x4D454D31, 1, 1024), "before tensor allocation"),
        (-1, _live_memory(0x4D454D31, 1, 1024, 100), "running warmups"),
        (-1, _live_memory(0x4D454D31, 1, 1024, 100, 100), "timed loop"),
        (7, None, "unrecognised status 7"),
    ],
)
def test_running_stage_names_progress(status, memory, stage):
    assert stage in capture._running_stage(status, memory)


def test_failed_stage_names_unmapped_status():
    assert capture._failed_stage(-9) == "Firmware failed at timing bound (status -9)"
    assert "unknown stage" in capture._failed_stage(-42)


def test_early_firmware_failure_names_stage_before_timing(rig):
    """A failing fixture returns before writing timing; the stage must still be named."""
    request, memory, terminal, _, _, guard = rig
    terminal[0x20000000] = struct.pack("<i", -5)
    terminal[0x2000000C] = bytes(28)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert result.error == "Firmware failed at arena allocation or model init (status -5)"


def test_target_is_not_halted_until_status_is_terminal(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    events: list[tuple] = []
    after_reset = False
    running_reads = 0
    original_attach = capture.attached_session

    @contextmanager
    def attach(**kwargs):
        with original_attach(**kwargs) as session:
            original_read, original_halt = session.memory_read8, session.halt

            def read(address, count):
                nonlocal running_reads
                if after_reset and address == 0x20000000:
                    if struct.unpack("<i", memory[address][:4])[0] == -1:
                        running_reads += 1
                        if running_reads >= 3:
                            memory.update(terminal)
                    events.append(("read_status", struct.unpack("<i", memory[address][:4])[0]))
                return original_read(address, count)

            def halt():
                if after_reset:
                    events.append(("halt",))
                return original_halt()

            monkeypatch.setattr(session, "memory_read8", read)
            monkeypatch.setattr(session, "halt", halt)
            yield session

    def reset(**kwargs):
        nonlocal after_reset
        after_reset = True
        memory.update(_RUNNING)

    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", sleeps.append)
    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "reset_target", reset)
    result = capture.capture_fixture(replace(request, settle_seconds=5), guard=guard)
    assert result.state == "success", result.error
    first_halt = events.index(("halt",))
    assert first_halt >= 1
    assert all(kind == "read_status" for kind, *_ in events[:first_halt])
    assert events[first_halt - 1] == ("read_status", 0)
    assert sleeps[0] == 0  # Short budgets reserve the attachment window.
    assert all(0 < s <= capture._POLL_INTERVAL_S for s in sleeps[1:])


def test_memory_sink_names_stage_on_timeout(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    real_inspect = capture.inspect_elf

    def inspect(elf, image, load_address, sizes):
        sizes = dict(sizes)
        assert sizes.pop("deployment_memory") == 32
        found = real_inspect(elf, image, load_address, sizes)
        return replace(found, sinks=found.sinks + (Sink("deployment_memory", 0x20000030, 32),))

    live = struct.pack("<8I", 0x4D454D31, 1, 1024, 100, 0, 0, 0, 0)
    monkeypatch.setattr(capture, "inspect_elf", inspect)
    monkeypatch.setattr(
        capture,
        "reset_target",
        lambda **kwargs: memory.update({0x20000000: struct.pack("<i", -1), 0x20000030: live}),
    )
    result = capture.capture_fixture(replace(request, arena_capacity=1024), guard=guard)
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert result.state == "failure"
    assert result.error is not None and result.error.endswith("running warmups")
    assert completion["completed"] is False and completion["status"] == -1


@pytest.mark.parametrize("engine", ["litert", "helia-aot"])
def test_rendered_fixture_returns_only_named_stages(engine):
    from tests.contracts.fixture_compile_cases import render_fixture

    text, _ = render_fixture("kws", engine)
    returned = {int(code) for code in re.findall(r"return (-\d+);", text)}
    returned |= {int(code) for code in re.findall(r"\? infer_fixture\(\) : (-\d+)", text)}
    assert returned and returned <= set(capture._FAILED_STAGES)
    assert set(capture._FAILED_STAGES) == {int(stage) for stage in FixtureStage}


def test_attach_that_halts_the_core_is_resumed_before_polling(rig, monkeypatch):
    """A debug connect that leaves the core halted must not stall the fixture."""
    request, memory, terminal, _, _, guard = rig
    after_reset = False
    original_attach = capture.attached_session

    @contextmanager
    def attach(**kwargs):
        with original_attach(**kwargs) as session:
            if after_reset:
                session.halt()
                original_read = session.memory_read8

                def read(address, count):
                    if address == 0x20000000 and not session.halted():
                        memory.update(terminal)
                    return original_read(address, count)

                monkeypatch.setattr(session, "memory_read8", read)
            yield session

    def reset(**kwargs):
        nonlocal after_reset
        after_reset = True
        memory.update(_RUNNING)

    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "reset_target", reset)
    result = capture.capture_fixture(replace(request, settle_seconds=0.5), guard=guard)
    assert result.state == "success", result.error
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["resumed_after_attach"] is True


def test_predicted_run_stays_detached_and_completes_on_first_poll(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", sleeps.append)
    result = capture.capture_fixture(
        replace(request, settle_seconds=30, expected_duration_s=2.0), guard=guard
    )
    assert result.state == "success", result.error
    assert sleeps[0] == 2.0 * capture._EXPECTED_MARGIN
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["detached_s"] == 2.5 and completion["complete_on_first_poll"] is True
    assert completion["polls"] == 1


def test_predicted_detach_is_bounded_by_settle_seconds(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", sleeps.append)
    result = capture.capture_fixture(
        replace(request, settle_seconds=5, expected_duration_s=4.5), guard=guard
    )
    assert result.state == "success", result.error
    assert sleeps[0] == 0


def test_short_prediction_falls_back_to_polling(rig, monkeypatch):
    request, memory, terminal, _, _, guard = rig
    status_reads = 0
    original_attach = capture.attached_session

    @contextmanager
    def attach(**kwargs):
        with original_attach(**kwargs) as session:
            original_read = session.memory_read8

            def read(address, count):
                nonlocal status_reads
                if address == 0x20000000 and memory[address] == _RUNNING[address]:
                    status_reads += 1
                    if status_reads > 2:
                        memory.update(terminal)
                return original_read(address, count)

            monkeypatch.setattr(session, "memory_read8", read)
            yield session

    sleeps: list[float] = []
    monkeypatch.setattr(capture.time, "sleep", sleeps.append)
    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "reset_target", lambda **kwargs: memory.update(_RUNNING))
    result = capture.capture_fixture(
        replace(request, settle_seconds=5, expected_duration_s=0.1), guard=guard
    )
    assert result.state == "success", result.error
    assert sleeps[0] == 0  # Short budgets reserve the attachment window.
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["complete_on_first_poll"] is False and completion["polls"] == 3


@pytest.mark.parametrize("expected", [0, -1.0, float("nan"), 11.0, "2"])
def test_invalid_prediction_is_rejected_before_device(rig, expected):
    request, memory, terminal, writes, flashes, guard = rig
    with pytest.raises(ValueError, match="expected duration"):
        capture.capture_fixture(
            replace(request, settle_seconds=10, expected_duration_s=expected), guard=guard
        )
    assert not guard.calls and not flashes and not writes


_TYPED_SINKS = {
    **_SINKS,
    "deployment_output_1": 5,
    "deployment_output_2": 2,
    "deployment_arena_scan": 16,
}
_TYPED_OUTPUTS = {
    0x20000004: bytes([1, 2, 3]),
    0x20000028: bytes([4, 5, 6, 7, 8]),
    0x20000030: b"\x09\x0a",
}


def _checksum(data):
    value = 0
    for byte in data:
        value = (value * 31 + byte) & 0xFFFFFFFF
    return value


@pytest.fixture
def typed_rig(rig, tmp_path):
    request, memory, terminal, writes, flashes, guard = rig
    (tmp_path / "typed").mkdir()
    elf, _, _ = image_files(tmp_path / "typed", _TYPED_SINKS)
    request = replace(request, elf=elf, extra_output_sizes=(5, 2), arena_scan_sizes=(64, 32))
    terminal.update(_TYPED_OUTPUTS)
    terminal[0x20000008] = struct.pack("<I", _checksum(b"".join(_TYPED_OUTPUTS.values())))
    terminal[0x20000034] = struct.pack("<4I", 10, 40, 0, 0)
    return request, memory, terminal, writes, flashes, guard


def test_typed_capture_reads_every_output_and_the_arena_scan(typed_rig):
    request, memory, terminal, writes, flashes, guard = typed_rig
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
    assert [ref.read() for ref in result.outputs] == list(_TYPED_OUTPUTS.values())
    assert [ref.path.name for ref in result.outputs] == [
        "deployment_output.bin",
        "deployment_output_1.bin",
        "deployment_output_2.bin",
    ]
    assert result.output == result.outputs[0]
    assert result.arena_scan == ((10, 40), (0, 0))
    identity = json.loads((request.evidence_dir / "identity.json").read_text())
    assert len(identity["sinks"]) == 7
    receipt = json.loads((request.evidence_dir / "receipt.json").read_text())
    assert receipt["arena_scan"] == [[10, 40], [0, 0]] and len(receipt["outputs"]) == 3


@pytest.mark.parametrize("covered", [1, 2])
def test_typed_checksum_must_cover_every_output(typed_rig, covered):
    request, memory, terminal, writes, flashes, guard = typed_rig
    partial = b"".join(list(_TYPED_OUTPUTS.values())[:covered])
    terminal[0x20000008] = struct.pack("<I", _checksum(partial))
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure" and "checksum" in (result.error or "")
    assert len(result.outputs) == 3


@pytest.mark.parametrize("words", [(41, 40, 0, 0), (0, 0, 1, 33), (0, 65, 0, 0)])
def test_arena_scan_terminal_must_fit_its_arena(typed_rig, words):
    request, memory, terminal, writes, flashes, guard = typed_rig
    terminal[0x20000034] = struct.pack("<4I", *words)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure" and "arena scan" in (result.error or "")


@pytest.mark.parametrize(
    "change",
    [
        {"extra_output_sizes": (5, 2, 1)},
        {"extra_output_sizes": (5, 3)},
        {"arena_scan_sizes": ()},
        {"arena_scan_sizes": (64,)},
    ],
)
def test_declared_sinks_must_match_the_image(typed_rig, change):
    request, memory, terminal, writes, flashes, guard = typed_rig
    result = capture.capture_fixture(replace(request, **change), guard=guard)
    assert result.state == "failure"
    assert not flashes and not writes


@pytest.mark.parametrize(
    "change,match",
    [
        ({"extra_output_sizes": (0,)}, "output extent"),
        ({"extra_output_sizes": [5]}, "output extent"),
        ({"extra_output_sizes": 5}, "output extent"),
        ({"extra_output_sizes": (0x7C000,)}, "output extent"),
        ({"arena_scan_sizes": (0,)}, "arena scan extents"),
        ({"arena_scan_sizes": [64]}, "arena scan extents"),
        ({"arena_scan_sizes": (1,) * 65}, "arena scan extents"),
    ],
)
def test_invalid_typed_extents_are_rejected_before_device(rig, change, match):
    request, memory, terminal, writes, flashes, guard = rig
    with pytest.raises(ValueError, match=match):
        capture.capture_fixture(replace(request, **change), guard=guard)
    assert not guard.calls and not flashes and not writes


def test_output_sinks_may_be_byte_aligned_but_word_sinks_may_not(tmp_path):
    sinks = {
        "deployment_output": 3,
        "deployment_output_1": 4,
        "deployment_status": 4,
        "deployment_checksum": 4,
        "deployment_timing": 28,
    }
    elf, image, sizes = image_files(tmp_path, sinks, align=1)
    with pytest.raises(ValueError, match="unaligned word sink"):
        inspect_elf(elf.read(), image.read(), 0x410000, sizes)
    (tmp_path / "bytes").mkdir()
    only_bytes = {"deployment_status": 4, "deployment_output": 3, "deployment_output_1": 5}
    elf, image, sizes = image_files(tmp_path / "bytes", only_bytes, align=1)
    parsed = inspect_elf(elf.read(), image.read(), 0x410000, sizes)
    assert {s.name: s.address for s in parsed.sinks}["deployment_output_1"] == 0x20000007


@pytest.mark.parametrize(
    "extra", ["deployment_output_1", "deployment_output_12", "deployment_arena_scan"]
)
def test_undeclared_typed_sink_is_refused(tmp_path, extra):
    elf, image, sizes = image_files(tmp_path, {**_SINKS, extra: 8})
    del sizes[extra]
    with pytest.raises(ValueError, match=f"undeclared fixture sink {extra}"):
        inspect_elf(elf.read(), image.read(), 0x410000, sizes)


def _eleven_output_rig(rig, tmp_path, *, checksum_ok):
    request, memory, terminal, writes, flashes, guard = rig
    extra = {f"deployment_output_{k}": 1 for k in range(1, 12)}
    sinks = {**_SINKS, **extra}
    (tmp_path / "eleven").mkdir()
    elf, _, _ = image_files(tmp_path / "eleven", sinks)
    address, addresses = 0x20000000, {}
    for name, size in sinks.items():
        addresses[name] = address
        address += (size + 3) // 4 * 4
    values = {f"deployment_output_{k}": bytes([k]) for k in range(1, 12)}
    for name, value in values.items():
        terminal[addresses[name]] = value
    model_order = bytes([1, 2, 3]) + b"".join(
        values[f"deployment_output_{k}"] for k in range(1, 12)
    )
    terminal[addresses["deployment_checksum"]] = struct.pack(
        "<I", _checksum(model_order) if checksum_ok else 0
    )
    return replace(request, elf=elf, extra_output_sizes=(1,) * 11), guard


@pytest.mark.parametrize("checksum_ok", [True, False])
def test_outputs_are_returned_in_model_order_past_ten(rig, tmp_path, checksum_ok):
    request, guard = _eleven_output_rig(rig, tmp_path, checksum_ok=checksum_ok)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == ("success" if checksum_ok else "failure"), result.error
    assert [ref.path.name for ref in result.outputs] == ["deployment_output.bin"] + [
        f"deployment_output_{k}.bin" for k in range(1, 12)
    ]
    assert [ref.read() for ref in result.outputs[1:]] == [bytes([k]) for k in range(1, 12)]
    receipt = json.loads((request.evidence_dir / "receipt.json").read_text())
    assert [Path(o["path"]).name for o in receipt["outputs"]] == [
        ref.path.name for ref in result.outputs
    ]


_MIB = 1024 * 1024


@pytest.mark.parametrize("size", [_MIB + 1, 2 * _MIB, 2297828, 0x003F0000])
def test_images_within_application_extent_are_flashed_verified_and_captured(rig, tmp_path, size):
    request, memory, terminal, writes, flashes, guard = rig
    binary = bytes((i * 7 + 1) & 0xFF for i in range(size))
    (tmp_path / "large").mkdir()
    elf, image, _ = image_files(tmp_path / "large", binary=binary)
    memory[0x410000] = binary
    result = capture.capture_fixture(replace(request, elf=elf, image=image), guard=guard)
    assert result.state == "success", result.error
    assert len(flashes) == 1
    assert (request.evidence_dir / "image.bin").read_bytes() == binary


def test_image_over_application_extent_is_refused_before_device(rig, tmp_path):
    request, memory, terminal, writes, flashes, guard = rig
    (tmp_path / "large").mkdir()
    elf, image, _ = image_files(tmp_path / "large", binary=b"\x01" * (0x003F0000 + 1))
    result = capture.capture_fixture(replace(request, elf=elf, image=image), guard=guard)
    assert result.state == "failure" and result.error == "Image size"
    assert not flashes and not writes and not guard.calls


def test_elf_inspection_admits_application_extent_and_keeps_the_mram_bound(tmp_path):
    from helia_profiler.fixture_image import MAX_IMAGE, MRAM, bounded

    assert MAX_IMAGE == MRAM[1] - MRAM[0]
    assert MRAM == (0x00410000, 0x00800000)
    assert bounded(0x00410000, 0x003F0000, MRAM) and not bounded(0x00410000, 0x003F0001, MRAM)
    elf, image, sizes = image_files(tmp_path, binary=b"\x02" * 0x003F0000)
    assert len(inspect_elf(elf.read(), image.read(), 0x410000, sizes).binary) == 0x003F0000
    (tmp_path / "over").mkdir()
    elf, image, sizes = image_files(tmp_path / "over", binary=b"\x02" * (0x003F0000 + 1))
    with pytest.raises(ValueError, match="image outside bounded application MRAM"):
        inspect_elf(elf.read(), image.read(), 0x410000, sizes)


def test_fixture_target_facts_come_from_the_platform():
    from helia_profiler.fixture_image import DTCM, MRAM
    from helia_profiler.fixture_target import FIXTURE_CLOCK_PROFILE
    from helia_profiler.platform import get_soc_for_board

    target = supported_fixture_target()
    soc = get_soc_for_board(target.board)
    speed = soc.cpu_clock.speed(FIXTURE_CLOCK_PROFILE)
    assert speed is not None and capture.FIXTURE_CPU_HZ == speed.mhz * 1_000_000 == 96_000_000
    assert MRAM == (0x00410000, 0x00800000)
    assert DTCM == (0x20000000, 0x2007C000)
    assert target.load_address == MRAM[0]


@pytest.mark.parametrize("fault", ["none", "truncated", "invoke"])
def test_complete_64k_output_and_invoke_status(rig, tmp_path, fault):
    request, memory, terminal, _, _, guard = rig
    sizes = {**_SINKS, "deployment_output": 65536}
    elf, image, _ = image_files(tmp_path, sizes)
    request = replace(request, elf=elf, image=image, output_size=65536)
    inspected = inspect_elf(elf.read(), image.read(), 0x410000, sizes)
    sinks = {s.name: s for s in inspected.sinks}
    raw = bytes(range(256)) * 256
    checksum = 0
    for byte in raw:
        checksum = (checksum * 31 + byte) & 0xFFFFFFFF
    terminal.clear()
    terminal.update(
        {
            sinks["deployment_status"].address: struct.pack("<i", -7 if fault == "invoke" else 0),
            sinks["deployment_output"].address: raw[:-1] if fault == "truncated" else raw,
            sinks["deployment_checksum"].address: struct.pack("<I", checksum),
            sinks["deployment_timing"].address: struct.pack(
                "<7I", 50, 10, 2, 32768, 300, 96000000, 1
            ),
        }
    )
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == ("success" if fault == "none" else "failure")
    if fault != "truncated":
        assert result.output is not None and result.output.read() == raw
    if fault == "truncated":
        assert result.error == "Short target read"
    if fault == "invoke":
        assert result.error is not None and "invoke" in result.error


def test_power_capture_preserves_full_terminal_and_instrument_evidence(rig, tmp_path, monkeypatch):
    from test_fixture_power import measured
    from test_fixture_power import request as power_request

    request, memory, terminal, writes, flashes, guard = rig
    elf, image, _ = image_files(tmp_path, {**_SINKS, "deployment_power": 20})
    request = replace(
        request,
        elf=elf,
        image=image,
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
        power_sync=power_request().firmware_wiring(),
        rail_power=power_request(),
    )
    terminal[0x2000000C] = struct.pack("<7I", 50, 10, 2, 32768, 300, 96000000, 2)
    terminal[0x20000028] = struct.pack("<5I", 0x50575231, 2, 29, 36, 14)
    ownership = []
    monkeypatch.setattr(
        guard, "verify_power_device", lambda **kw: ownership.append(kw), raising=False
    )

    def observe(req, same_guard, reset):
        assert same_guard is guard and ownership
        reset()
        return measured()

    from helia_profiler import power

    monkeypatch.setattr(
        power,
        "get_driver",
        lambda name, *, serial: SimpleNamespace(ensure_target_powered=lambda *, required: True),
    )
    monkeypatch.setattr(capture, "observe_fixture_power", observe)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
    assert result.output is not None
    assert result.rail_energy is not None
    assert result.output.read() == bytes([1, 2, 3])
    assert result.rail_energy.energy_j == 0.02
    assert result.rail_energy.iterations == 10
    assert result.rail_energy.raw in result.artifacts
    assert len(flashes) == 1
    raw = json.loads(result.rail_energy.raw.read())
    assert raw["image_sha256"] == image.sha256
    assert raw["observation"]["gated_windows"][0]["energy_j"] == 0.02


def test_power_authority_is_required_before_any_device_operation(rig):
    from test_fixture_power import request as power_request

    request, memory, terminal, writes, flashes, guard = rig
    request = replace(
        request,
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
        power_sync=power_request().firmware_wiring(),
        rail_power=power_request(),
    )
    with pytest.raises(ValueError, match="power authority"):
        capture.capture_fixture(request, guard=guard)
    assert not flashes and not writes


def test_legacy_power_image_refuses_before_device(rig):
    from test_fixture_power import request as power_request

    request, _, _, writes, flashes, guard = rig
    request = replace(
        request,
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
        power_sync=replace(power_request().firmware_wiring(), protocol_version=1),
        rail_power=power_request(),
    )
    with pytest.raises(ValueError, match="protocol 2"):
        capture.capture_fixture(request, guard=guard)
    assert not writes and not flashes and not guard.calls


@pytest.mark.parametrize("unfinished", [capture._STATUS_RUNNING, capture._STATUS_POISON])
@pytest.mark.parametrize("halted", [False, True])
def test_power_capture_rejects_completion_after_observation(
    rig, tmp_path, monkeypatch, unfinished, halted
):
    from test_fixture_power import measured
    from test_fixture_power import request as power_request

    request, memory, terminal, _, flashes, guard = rig
    elf, image, _ = image_files(tmp_path, {**_SINKS, "deployment_power": 20})
    request = replace(
        request,
        elf=elf,
        image=image,
        settle_seconds=60,
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
        power_sync=power_request().firmware_wiring(),
        rail_power=power_request(),
    )
    terminal[0x2000000C] = struct.pack("<7I", 50, 10, 2, 32768, 300, 96000000, 2)
    terminal[0x20000028] = struct.pack("<5I", 0x50575231, 2, 29, 36, 14)
    monkeypatch.setattr(guard, "verify_power_device", lambda **kw: None, raising=False)

    def observe(req, same_guard, reset):
        reset()
        memory[0x20000000] = struct.pack("<i", unfinished)
        return measured()

    # Later completion cannot extend samples that have already stopped.
    monkeypatch.setattr(capture.time, "sleep", lambda _: memory.update(terminal))
    from helia_profiler import power

    monkeypatch.setattr(
        power,
        "get_driver",
        lambda name, *, serial: SimpleNamespace(ensure_target_powered=lambda *, required: True),
    )
    monkeypatch.setattr(capture, "observe_fixture_power", observe)
    restarts = []
    if halted:
        original_attach = capture.attached_session
        attaches = 0

        @contextmanager
        def attach(**kwargs):
            nonlocal attaches
            attaches += 1
            with original_attach(**kwargs) as session:
                if attaches == 3:
                    monkeypatch.setattr(session, "halted", lambda: True)
                    monkeypatch.setattr(session, "restart", lambda: restarts.append(True))
                yield session

        monkeypatch.setattr(capture, "attached_session", attach)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure"
    assert (
        result.error is not None
        and "Power observation ended before firmware completion" in result.error
    )
    assert result.rail_energy is None
    assert not restarts
    assert len(flashes) == 1
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["polls"] == 1
    assert completion["completed"] is False
    assert completion["status"] == unfinished
    assert (request.evidence_dir / "rail-power.json").is_file()


@pytest.mark.parametrize("outcome", [True, False, "error"])
def test_configured_board_power_before_attach(rig, monkeypatch, outcome):
    from helia_profiler import power
    from helia_profiler.errors import PowerError
    from helia_profiler.fixture import FixtureBoardPowerRequest

    request, memory, terminal, writes, flashes, guard = rig
    request = replace(
        request, board_power=FixtureBoardPowerRequest("supply", "supply-serial", "MCU")
    )
    events = []

    def authority(*, serial, rail):
        assert (serial, rail) == ("supply-serial", "MCU")
        events.append("authority")

    guard.verify_power_device = authority

    def make_driver(name, *, serial):
        assert events == ["authority"]
        assert (name, serial) == ("supply", "supply-serial")
        assert not writes and not flashes
        events.append("driver")

        def ensure(*, required):
            assert required is True
            events.append("powered")
            if outcome == "error":
                raise PowerError("supply unavailable")
            return outcome

        return SimpleNamespace(ensure_target_powered=ensure)

    monkeypatch.setattr(power, "get_driver", make_driver)
    result = capture.capture_fixture(request, guard=guard)
    assert events == ["authority", "driver", "powered"]
    if outcome is True:
        assert result.state == "success", result.error
        assert len(flashes) == 1
        assert result.rail_energy is None
    else:
        assert result.state != "success"
        assert not writes and not flashes


def test_timing_without_supply_never_resolves_driver(rig, monkeypatch):
    from helia_profiler import power

    request, memory, terminal, writes, flashes, guard = rig
    monkeypatch.setattr(power, "get_driver", lambda *a, **k: pytest.fail("unexpected instrument"))
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
    assert result.rail_energy is None


def test_board_power_requires_station_authority_before_hardware(rig, monkeypatch):
    from helia_profiler import power
    from helia_profiler.fixture import FixtureBoardPowerRequest

    request, memory, terminal, writes, flashes, guard = rig
    request = replace(request, board_power=FixtureBoardPowerRequest("supply", "serial", "MCU"))
    monkeypatch.setattr(power, "get_driver", lambda *a, **k: pytest.fail("unexpected instrument"))
    with pytest.raises(ValueError, match="power authority"):
        capture.capture_fixture(request, guard=guard)
    assert not request.evidence_dir.exists()
    assert not writes and not flashes


@pytest.mark.parametrize(
    "field,value", [("driver", "other"), ("serial", "other"), ("rail", "other")]
)
def test_supply_and_measurement_must_select_same_device(rig, field, value):
    from test_fixture_power import request as power_request

    from helia_profiler.fixture import FixtureBoardPowerRequest

    request, memory, terminal, writes, flashes, guard = rig
    guard.verify_power_device = lambda **kw: None
    supply = replace(FixtureBoardPowerRequest("joulescope", "25QG", "MCU"), **{field: value})
    request = replace(
        request,
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
        power_sync=power_request().firmware_wiring(),
        rail_power=power_request(),
        board_power=supply,
    )
    with pytest.raises(ValueError, match="Supply path differs"):
        capture.capture_fixture(request, guard=guard)
    assert not request.evidence_dir.exists() and not writes and not flashes


@pytest.mark.parametrize("when", ["authority", "after_supply"])
def test_supply_authority_failure_stops_before_attachment(rig, monkeypatch, when):
    from helia_profiler import power
    from helia_profiler.fixture import FixtureBoardPowerRequest

    request, memory, terminal, writes, flashes, guard = rig
    request = replace(request, board_power=FixtureBoardPowerRequest("supply", "serial", "MCU"))
    powered = []

    def verify(**kwargs):
        if when == "authority":
            raise ValueError("station refused")

    def check(**kwargs):
        if powered:
            raise ValueError("station expired")

    def ensure(**kwargs):
        powered.append(True)
        return True

    guard.verify_power_device = verify
    guard.check = check
    monkeypatch.setattr(
        power, "get_driver", lambda *a, **k: SimpleNamespace(ensure_target_powered=ensure)
    )
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "failure" and "station" in (result.error or "")
    assert bool(powered) is (when == "after_supply")
    assert not writes and not flashes


@pytest.mark.parametrize(
    "completion_s, tick_seconds, accepted",
    [(120, 110, True), (299, 290, True), (301, 290, False), (120, 301, False)],
)
def test_explicit_long_timing_budget_binds_wait_and_terminal(
    rig, monkeypatch, completion_s, tick_seconds, accepted
):
    request, memory, terminal, _, flashes, guard = rig
    request = replace(request, settle_seconds=300)
    terminal[0x2000000C] = struct.pack("<7I", tick_seconds * 32768, 10, 2, 32768, 300, 96000000, 1)
    clock = [0.0]
    reset = capture.reset_target

    def complete(**kwargs):
        reset(**kwargs)
        clock[0] = completion_s

    monkeypatch.setattr(capture, "reset_target", complete)
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    result = capture.capture_fixture(request, guard=guard)
    assert (result.state == "success") is accepted, result.error
    assert len(flashes) == 1
    assert (True, 600) in guard.calls
    assert (True, 420) in guard.calls
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["elapsed_s"] == completion_s
    assert completion["max_wait_s"] == 300


@pytest.mark.parametrize("seconds", [True, 0, -1, 301, float("inf"), float("nan")])
def test_invalid_timing_budget_refuses_before_device(rig, seconds):
    request, _, _, writes, flashes, guard = rig
    with pytest.raises(ValueError, match="Unbounded settle time"):
        capture.capture_fixture(replace(request, settle_seconds=seconds), guard=guard)
    assert not flashes and not writes and not guard.calls


def test_long_timing_requires_full_permit_before_probe(rig, monkeypatch):
    request, _, _, writes, flashes, _ = rig

    class ShortPermit(Guard):
        def check(self, *, require_free, remaining_s):
            if remaining_s > 360:
                raise PermissionError("permit cannot cover requested run")

    monkeypatch.setattr(capture, "list_connected_probes", lambda: pytest.fail("probe reached"))
    result = capture.capture_fixture(replace(request, settle_seconds=300), guard=ShortPermit())
    assert result.error == "permit cannot cover requested run"
    assert not flashes and not writes


@pytest.mark.parametrize("seconds", [60.01, 300])
def test_rail_budget_stays_bounded_before_device(rig, seconds):
    request, _, _, writes, flashes, guard = rig
    rail = capture.FixtureRailPowerRequest("serial", "MCU", 1.8, 29, 36, 14)
    guard.verify_power_device = lambda **kwargs: None
    request = replace(
        request,
        settle_seconds=seconds,
        rail_power=rail,
        power_sync=rail.firmware_wiring(),
        timing_scope=FixtureTimingScope.RESTORE_AND_INVOKE,
    )
    with pytest.raises(ValueError, match="Unbounded settle time"):
        capture.capture_fixture(request, guard=guard)
    assert not flashes and not writes and not guard.calls


@pytest.mark.parametrize("budget, prediction", [(300, 250), (60, 55), (5, 4.5), (0.5, None)])
def test_predicted_detach_reserves_attachment_after_reset(rig, monkeypatch, budget, prediction):
    request, _, _, _, _, guard = rig
    clock = [0.0]
    original_reset = capture.reset_target
    original_attach = capture.attached_session

    def reset(**kwargs):
        clock[0] += 0.1
        original_reset(**kwargs)

    @contextmanager
    def attach(**kwargs):
        clock[0] += 0.1
        with original_attach(**kwargs) as session:
            yield session

    def sleep(seconds):
        clock[0] += seconds

    monkeypatch.setattr(capture, "reset_target", reset)
    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture.time, "sleep", sleep)
    monkeypatch.setattr(capture.time, "monotonic", lambda: clock[0])
    result = capture.capture_fixture(
        replace(request, settle_seconds=budget, expected_duration_s=prediction), guard=guard
    )
    assert result.state == "success", result.error
    completion = json.loads((request.evidence_dir / "completion.json").read_text())
    assert completion["elapsed_s"] < budget
    assert completion["complete_on_first_poll"] is True


def test_a_slow_host_does_not_decide_the_completion_window(rig, monkeypatch):
    import time as wall

    request, _, _, _, _, guard = rig
    reset = capture.reset_target

    def slow_reset(**kwargs):
        reset(**kwargs)
        end = wall.perf_counter() + 2 * request.settle_seconds
        while wall.perf_counter() < end:
            pass

    monkeypatch.setattr(capture, "reset_target", slow_reset)
    result = capture.capture_fixture(request, guard=guard)
    assert result.state == "success", result.error
