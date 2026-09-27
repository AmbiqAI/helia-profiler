"""Mocked raw capture checks preserve evidence and reject unsafe images."""

from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
import json
import struct
from types import SimpleNamespace

import pytest

from helia_profiler import fixture_capture as capture
from helia_profiler.fixture import FixtureTimingScope
from helia_profiler.fixture_image import inspect_elf
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
        "Cortex-M55",
        "123",
        tmp_path / "evidence",
        0.01,
        FixtureTimingScope.INVOKE_ONLY,
    )
    memory = {0x410000: image.read(), 0xE000ED00: struct.pack("<I", 0xD22 << 4)}
    writes = []

    class Session:
        def halt(self):
            pass

        def halted(self):
            return True

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
    monkeypatch.setattr(capture, "reset_target", lambda **kwargs: memory.update(terminal))
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

    class Denied:
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
