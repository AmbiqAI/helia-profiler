"""Mocked raw capture checks preserve evidence and reject unsafe images."""

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import re
import struct
from types import SimpleNamespace

import pytest

from helia_profiler import fixture_capture as capture
from helia_profiler.fixture import FixtureTimingScope
from helia_profiler.fixture_stage import FixtureStage
from helia_profiler.fixture_image import Sink, inspect_elf
from helia_profiler.fixture_target import supported_fixture_target
from helia_profiler.fixture_runtime import FixtureFile


def image_files(tmp_path):
    names = ["deployment_status", "deployment_output", "deployment_checksum", "deployment_timing"]
    sizes = [4, 3, 4, 28]
    addresses = [0x20000000, 0x20000004, 0x20000008, 0x2000000C]
    strings = b"\0" + b"".join(n.encode() + b"\0" for n in names)
    symbols = b"\0" * 16
    for name, size, address in zip(names, sizes, addresses):
        symbols += struct.pack("<IIIBBH", strings.index(name.encode()), address, size, 0x11, 0, 1)
    binary = b"\x01\x02\x03\x04"
    data = bytearray(512)
    data[:16] = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(
        "<HHIIIIIHHHHHH", data, 16, 2, 40, 1, 0x410001, 52, 128, 0, 52, 32, 2, 40, 4, 2
    )
    struct.pack_into("<8I", data, 52, 1, 400, 0x410000, 0x410000, 4, 4, 5, 1)
    struct.pack_into("<8I", data, 84, 1, 0, 0x20000000, 0, 0, 64, 6, 1)
    struct.pack_into("<10I", data, 168, 0, 8, 3, 0x20000000, 0, 64, 0, 0, 4, 0)
    struct.pack_into("<10I", data, 208, 0, 3, 0, 0, 288, len(strings), 0, 0, 1, 0)
    struct.pack_into("<10I", data, 248, 0, 2, 0, 0, 408, len(symbols), 2, 0, 4, 16)
    data[288 : 288 + len(strings)] = strings
    data[400:404] = binary
    data[408 : 408 + len(symbols)] = symbols

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
            return memory[address][:count]

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
    monkeypatch.setattr(capture.time, "sleep", lambda _: None)
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
    assert sleeps[0] == capture._FIRST_POLL_S
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


@pytest.mark.parametrize("engine", ["tflm", "helia-aot"])
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
        replace(request, settle_seconds=10, expected_duration_s=2.0), guard=guard
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
    assert sleeps[0] == 5


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

    monkeypatch.setattr(capture, "attached_session", attach)
    monkeypatch.setattr(capture, "reset_target", lambda **kwargs: memory.update(_RUNNING))
    result = capture.capture_fixture(
        replace(request, settle_seconds=5, expected_duration_s=0.1), guard=guard
    )
    assert result.state == "success", result.error
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
