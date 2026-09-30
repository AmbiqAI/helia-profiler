"""Typed raw fixture capture through the existing profiler probe APIs."""

from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
import struct
import math
import time
from dataclasses import asdict
from typing import Protocol

from ._fixture_build import FixtureTimingScope
from .fixture_stage import FixtureStage
from .fixture_runtime import FixtureFile
from .fixture_target import FixtureTarget
from .fixture_image import MAX_ELF, MAX_IMAGE, DTCM, digest, require, inspect_elf
from .target.probe.flash import flash_binary
from .target.probe.jlink import (
    attached_session,
    list_connected_probes,
    reset_target,
    resume_if_halted,
)


class FixtureCaptureGuard(Protocol):
    """Caller-owned authorization and remaining-window checks."""

    def check(self, *, require_free: bool, remaining_s: float) -> None: ...

    def verify_target(self, *, target: FixtureTarget, jlink_serial: str) -> None:
        """Require caller-verified physical board/serial identity and exclusive ownership.

        Core CPUID and probe enumeration cannot establish the physical board.
        Reject if the caller has no current independent board/serial evidence.
        """
        ...


_STATUS_POISON = struct.unpack("<i", bytes([0xA5]) * 4)[0]
_STATUS_RUNNING = -1
_FIRST_POLL_S = 1.0
_POLL_INTERVAL_S = 0.25
_EXPECTED_MARGIN = 1.25
_MEMORY_MAGIC = (0x4D454D31, 1)
_FAILED_STAGES = {int(stage): stage.description for stage in FixtureStage}


@dataclass(frozen=True)
class FixtureCaptureRequest:
    """One bounded capture; ``settle_seconds`` is the maximum wait for completion."""

    elf: FixtureFile
    image: FixtureFile
    load_address: int
    output_size: int
    device: str
    jlink_serial: str
    evidence_dir: Path
    settle_seconds: float
    timing_scope: FixtureTimingScope
    target: FixtureTarget
    arena_capacity: int | None = None
    #: Caller-predicted firmware run time after reset; the host stays detached for
    #: ``max(1 s, expected_duration_s * 1.25)``, bounded by ``settle_seconds``, before polling.
    expected_duration_s: float | None = None
    #: Sizes of outputs after the first, in model order (typed fixtures).
    extra_output_sizes: tuple[int, ...] = ()
    #: Byte size of each heliaAOT scratch arena the build scans, in ``FixtureBuild.aot_arena_scan`` order.
    arena_scan_sizes: tuple[int, ...] = ()


@dataclass(frozen=True)
class FixtureTiming:
    ticks: int
    iterations: int
    warmups: int
    timer_hz: int
    settle_ticks: int
    cpu_hz: int
    timing_scope: FixtureTimingScope


@dataclass(frozen=True)
class FixtureMemory:
    capacity: int
    after_io_access: int
    after_warmup: int
    after_invoke: int


@dataclass(frozen=True)
class FixtureCaptureResult:
    state: str
    timing_scope: FixtureTimingScope
    output: FixtureFile | None = None
    status: int | None = None
    checksum: int | None = None
    timing: FixtureTiming | None = None
    memory: FixtureMemory | None = None
    artifacts: tuple[FixtureFile, ...] = ()
    error: str | None = None
    #: Every output in model order; ``output`` is the first.
    outputs: tuple[FixtureFile, ...] = ()
    #: Per scanned scratch arena: (bytes no longer holding the paint, highest such offset + 1).
    #: Both are lower bounds: a kernel may write the paint value itself.
    arena_scan: tuple[tuple[int, int], ...] = ()


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, default=str, sort_keys=True) + "\n")
    temporary.replace(path)


def _memory(data: bytes, capacity: int) -> FixtureMemory:
    words = struct.unpack("<8I", data)
    check = 0
    for word in words[:7]:
        check = (check * 31 + word) & 0xFFFFFFFF
    require(
        words[:2] == _MEMORY_MAGIC and words[2] == capacity and words[6] == 1 and words[7] == check,
        "Invalid versioned memory terminal",
    )
    require(all(0 < used <= capacity for used in words[3:6]), "Invalid allocator-use snapshot")
    return FixtureMemory(capacity, *words[3:6])


def _running_stage(status: int, memory: bytes | None) -> str:
    """Name the stage an unfinished fixture reached from its live sinks."""
    if status == _STATUS_POISON:
        return "firmware not started (status sink still poisoned)"
    if status != _STATUS_RUNNING:
        return f"unrecognised status {status}"
    if memory is None or len(memory) != 32:
        return "running (no intermediate stage reported)"
    words = struct.unpack("<8I", memory)
    if words[:2] != _MEMORY_MAGIC or not words[3]:
        return "running before tensor allocation"
    if not words[4]:
        return "running warmups"
    return "running the timed loop"


def _failed_stage(status: int) -> str:
    stage = _FAILED_STAGES.get(status, "system initialisation or unknown stage")
    return f"Firmware failed at {stage} (status {status})"


def capture_fixture(
    request: FixtureCaptureRequest, *, guard: FixtureCaptureGuard
) -> FixtureCaptureResult:
    """Capture one bounded raw terminal snapshot and persist every attempted operation."""
    require(isinstance(request.extra_output_sizes, tuple), "Invalid output extent")
    output_sizes = (request.output_size, *request.extra_output_sizes)
    require(
        all(type(size) is int and size > 0 for size in output_sizes)
        and sum(output_sizes) <= DTCM[1] - DTCM[0],
        "Invalid output extent",
    )
    require(
        isinstance(request.arena_scan_sizes, tuple)
        and len(request.arena_scan_sizes) <= 64
        and all(type(size) is int and size > 0 for size in request.arena_scan_sizes),
        "Invalid arena scan extents",
    )
    output_names = ["deployment_output"] + [
        f"deployment_output_{k}" for k in range(1, len(output_sizes))
    ]
    require(
        type(request.settle_seconds) in (int, float)
        and math.isfinite(request.settle_seconds)
        and 0 < request.settle_seconds <= 60,
        "Unbounded settle time",
    )
    require(
        isinstance(request.device, str)
        and bool(request.device)
        and isinstance(request.jlink_serial, str)
        and bool(request.jlink_serial),
        "Explicit device and probe required",
    )
    require(
        request.arena_capacity is None
        or type(request.arena_capacity) is int
        and 0 < request.arena_capacity <= 3 * 1024 * 1024,
        "Invalid arena capacity",
    )
    require(
        request.expected_duration_s is None
        or type(request.expected_duration_s) in (int, float)
        and math.isfinite(request.expected_duration_s)
        and 0 < request.expected_duration_s <= request.settle_seconds,
        "Invalid expected duration",
    )
    require(isinstance(request.timing_scope, FixtureTimingScope), "Explicit timing scope required")
    require(isinstance(request.target, FixtureTarget), "Explicit typed fixture target required")
    request.target.verify()
    require(request.device == request.target.device, "Device differs from fixture target")
    directory = request.evidence_dir
    directory.mkdir(parents=True, exist_ok=False)
    _atomic_json(directory / "started.json", {"state": "started", "request": asdict(request)})
    artifacts: list[FixtureFile] = []
    output = None
    outputs: tuple[FixtureFile, ...] = ()
    status = crc = None
    timing = memory = None

    def pin(name: str, value: bytes) -> FixtureFile:
        path = directory / name
        path.write_bytes(value)
        artifact = FixtureFile(path, digest(value))
        artifacts.append(artifact)
        return artifact

    def attach():
        guard.check(require_free=True, remaining_s=90)
        return attached_session(
            device=request.device, jlink_serial=request.jlink_serial, attach_timeout_s=15
        )

    def read(session, address: int, count: int) -> bytes:
        guard.check(require_free=False, remaining_s=10)
        data = bytes(session.memory_read8(address, count))
        require(len(data) == count, "Short target read")
        return data

    def halt(session) -> None:
        guard.check(require_free=False, remaining_s=10)
        session.halt()
        require(session.halted(), "Halt failed")

    try:
        require(52 <= request.elf.path.stat().st_size <= MAX_ELF, "ELF size")
        require(0 < request.image.path.stat().st_size <= MAX_IMAGE, "Image size")
        sizes = {
            "deployment_status": 4,
            **dict(zip(output_names, output_sizes)),
            "deployment_checksum": 4,
            "deployment_timing": 28,
        }
        if request.arena_capacity is not None:
            sizes["deployment_memory"] = 32
        if request.arena_scan_sizes:
            sizes["deployment_arena_scan"] = 8 * len(request.arena_scan_sizes)
        image = inspect_elf(request.elf.read(), request.image.read(), request.load_address, sizes)
        pin(
            "identity.json",
            (
                json.dumps(
                    {
                        "elf_sha256": image.elf_sha256,
                        "image_sha256": digest(image.binary),
                        "load_address": image.load_address,
                        "sinks": [asdict(s) for s in image.sinks],
                        "request_sha256": digest((directory / "started.json").read_bytes()),
                        "timing_scope": request.timing_scope.value,
                    },
                    sort_keys=True,
                )
                + "\n"
            ).encode(),
        )
        # Flash the verified private copy so source mutation cannot select another image.
        flash_image = pin("image.bin", image.binary)
        guard.verify_target(target=request.target, jlink_serial=request.jlink_serial)
        guard.check(require_free=True, remaining_s=360)
        require(
            sum(str(p.serial) == request.jlink_serial for p in list_connected_probes()) == 1,
            "Probe enumeration mismatch",
        )

        def verify(session) -> None:
            for offset in range(0, len(image.binary), 1024):
                block = image.binary[offset : offset + 1024]
                require(
                    read(session, image.load_address + offset, len(block)) == block,
                    "Full image readback mismatch",
                )

        def snapshot(session, *, suffix: str) -> dict[str, bytes]:
            nonlocal output, outputs
            values = {}
            for sink in image.sinks:
                guard.check(require_free=False, remaining_s=10)
                value = bytes(session.memory_read8(sink.address, sink.size))
                ref = pin(sink.name + suffix + ".bin", value)
                if sink.name in output_names and not suffix:
                    outputs = tuple(
                        sorted(
                            (*outputs, ref),
                            key=lambda r: output_names.index(r.path.stem),
                        )
                    )
                    if sink.name == "deployment_output":
                        output = ref
                require(len(value) == sink.size, "Short target read")
                values[sink.name] = value
            return values

        with attach() as session:
            halt(session)
            require(
                (int.from_bytes(read(session, 0xE000ED00, 4), "little") >> 4) & 0xFFF == 0xD22,
                "Unexpected core",
            )
        guard.check(require_free=True, remaining_s=240)
        flash_image.read()
        flash_binary(
            binary_path=flash_image.path,
            device=request.device,
            load_addr=image.load_address,
            jlink_serial=request.jlink_serial,
            timeout_s=60,
        )
        with attach() as session:
            halt(session)
            verify(session)
            for sink in image.sinks:
                poison = bytes([0xA5]) * sink.size
                guard.check(require_free=False, remaining_s=10)
                session.memory_write8(sink.address, list(poison))
                require(
                    read(session, sink.address, sink.size) == poison, "Poison readback mismatch"
                )
        sinks = {sink.name: sink for sink in image.sinks}

        def await_completion(session, started: float, resumed: bool, detached: float) -> None:
            """Poll the running target's status sink until it leaves both sentinels."""
            polls = 0
            while True:
                polls += 1
                raw = read(session, sinks["deployment_status"].address, 4)
                live = struct.unpack("<i", raw)[0]
                elapsed = time.monotonic() - started
                completed = live not in (_STATUS_POISON, _STATUS_RUNNING)
                if completed or elapsed >= request.settle_seconds:
                    break
                time.sleep(min(_POLL_INTERVAL_S, request.settle_seconds - elapsed))
            live_memory = None
            if not completed and "deployment_memory" in sinks:
                live_memory = read(session, sinks["deployment_memory"].address, 32)
            pin(
                "completion.json",
                (
                    json.dumps(
                        {
                            "completed": completed,
                            "status": live,
                            "polls": polls,
                            "elapsed_s": elapsed,
                            "max_wait_s": request.settle_seconds,
                            "resumed_after_attach": resumed,
                            "detached_s": detached,
                            "complete_on_first_poll": completed and polls == 1,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                ).encode(),
            )
            require(
                completed,
                f"Firmware did not complete within {request.settle_seconds} s: "
                + _running_stage(live, live_memory),
            )

        guard.check(require_free=True, remaining_s=request.settle_seconds + 120)
        reset_target(device=request.device, jlink_serial=request.jlink_serial)
        started = time.monotonic()
        # Stay detached through the secure bootloader and, when predicted, the whole run.
        detached = min(
            max(_FIRST_POLL_S, (request.expected_duration_s or 0.0) * _EXPECTED_MARGIN),
            request.settle_seconds,
        )
        time.sleep(detached)
        with attach() as session:
            guard.check(require_free=False, remaining_s=10)
            await_completion(session, started, resume_if_halted(session), detached)
            halt(session)
            verify(session)
            values = snapshot(session, suffix="")
            require(
                values == snapshot(session, suffix="-verification"), "Unstable completed snapshot"
            )
            require(session.halted(), "Target resumed during snapshot")
        status = struct.unpack("<i", values["deployment_status"])[0]
        crc = struct.unpack("<I", values["deployment_checksum"])[0]
        require(status == 0, _failed_stage(status))
        timing_words = struct.unpack("<7I", values["deployment_timing"])
        observed_scope = {
            1: FixtureTimingScope.INVOKE_ONLY,
            2: FixtureTimingScope.RESTORE_AND_INVOKE,
        }.get(timing_words[6])
        require(observed_scope is not None, "Invalid timing scope terminal")
        if observed_scope is None:
            raise ValueError("Invalid timing scope terminal")
        timing = FixtureTiming(*timing_words[:6], observed_scope)
        require(
            observed_scope is request.timing_scope,
            "Timing scope differs from verified firmware terminal",
        )
        computed = 0
        for byte in b"".join(values[name] for name in output_names):
            computed = (computed * 31 + byte) & 0xFFFFFFFF
        require(crc == computed, "Output checksum mismatch")
        require(
            timing.iterations > 0
            and timing.timer_hz == 32768
            and timing.cpu_hz == 96000000
            and 0 < timing.ticks < 60 * timing.timer_hz
            and 245 <= timing.settle_ticks <= 410,
            "Invalid timing completion terminal",
        )
        if request.arena_capacity is not None:
            memory = _memory(values["deployment_memory"], request.arena_capacity)
        arena_scan = ()
        if request.arena_scan_sizes:
            words = struct.unpack(
                f"<{2 * len(request.arena_scan_sizes)}I", values["deployment_arena_scan"]
            )
            arena_scan = tuple(zip(words[0::2], words[1::2]))
            require(
                all(
                    touched <= high <= size
                    for (touched, high), size in zip(arena_scan, request.arena_scan_sizes)
                ),
                "Invalid arena scan terminal",
            )
        result = FixtureCaptureResult(
            "success",
            request.timing_scope,
            output,
            status,
            crc,
            timing,
            memory,
            tuple(artifacts),
            outputs=outputs,
            arena_scan=arena_scan,
        )
    except Exception as exc:
        result = FixtureCaptureResult(
            "failure",
            request.timing_scope,
            output,
            status,
            crc,
            timing,
            memory,
            tuple(artifacts),
            str(exc),
            outputs=outputs,
        )
    _atomic_json(directory / "receipt.json", asdict(result))
    return result
