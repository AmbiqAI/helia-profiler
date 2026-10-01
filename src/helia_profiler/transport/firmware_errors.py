"""Host hints for ``HPX_ERROR=<code>`` lines and the raiser every transport shares.

It lives under ``transport`` rather than ``capture`` so the RTT handshake and
PSRAM upload, which see firmware errors before the stream is collected, can
raise the same hinted :class:`CaptureError` without importing ``capture``
(which imports ``transport``).
"""

from __future__ import annotations

import logging
import re

from ..errors import CaptureError
from ..wire import HPX_ERROR_PREFIX, FirmwareErrorCode

log = logging.getLogger("hpx")

# Maps a registered ``HPX_ERROR=<code>`` to a human-readable hint.  The
# firmware emits these after its own preflight checks so the host can point
# the user at the real cause instead of blaming the arena for every failure.
# Every code in ``wire.FirmwareErrorCode`` has an entry here; that
# completeness is pinned by tests/contracts/test_wire_protocol.py, so a new
# code without a hint fails that test.
ERROR_HINTS: dict[FirmwareErrorCode, str] = {
    FirmwareErrorCode.SCHEMA_MISMATCH: (
        "The model's schema version does not match what the firmware was "
        "built for.  Re-export the model with a matching TFLite version."
    ),
    FirmwareErrorCode.UNSUPPORTED_OP: (
        "The model uses an operator the firmware resolver did not register.  "
        "Add the missing op to the resolver (firmware/templates/main.cc.j2 "
        "get_resolver()) or re-export the model without that op."
    ),
    FirmwareErrorCode.MISSING_OPS: (
        "One or more operators in the model are not registered in the "
        "MicroMutableOpResolver.  See the preceding HPX_ERROR=unsupported_op "
        "lines for the specific ops."
    ),
    FirmwareErrorCode.ALLOC_TENSORS_FAILED: (
        "TFLM AllocateTensors() failed.  Likely causes, in order of "
        "probability: (1) the arena is too small — increase --arena-size; "
        "(2) a kernel's Prepare() rejected an op (shape/dtype/parameter "
        "mismatch not caught by preflight).  The firmware reports the "
        "configured arena size in the error line."
    ),
    FirmwareErrorCode.MODEL_INIT_FAILED: (
        "heliaAOT model init returned a non-zero status.  Check that the "
        "generated module was built against the correct board and that any "
        "required memories (PSRAM, SHARED_SRAM) are initialised."
    ),
    FirmwareErrorCode.NPU_INIT_FAILED: (
        "Ethos-U NPU bring-up failed before any inference ran.  On FPGA "
        "targets this almost always means the loaded bitstream's generation "
        "does not match the SDK the firmware was built against (no working "
        "Ethos-U at the expected base address); reload the matching "
        "bitstream.  On silicon, check that the board actually has the NPU "
        "the SoC definition claims and power-cycle the target."
    ),
    FirmwareErrorCode.STIMER_DEAD: (
        "The 32.768 kHz crystal (XT) that clocks the measurement window "
        "never produced a plausible tick rate within the 1 s settle "
        "deadline. This is a BOARD condition, not the debug-domain "
        "frozen-clock bug: check the X32 crystal and its jumpers/straps on "
        "the EVB, and any shield or rework touching the XT pins. The window "
        "error was emitted before the window opened and the host discards "
        "the run, so no misleading power figures are reported."
    ),
    FirmwareErrorCode.PSRAM_INIT_FAILED: (
        "PSRAM initialisation failed on the target.  Verify the board "
        "actually has PSRAM populated and that --weights-location / "
        "--arena-location psram is appropriate for this hardware."
    ),
    FirmwareErrorCode.PSRAM_INFO_FAILED: (
        "PSRAM initialised but its info query failed, so the firmware has no "
        "base address to place PSRAM data at.  This is a driver/hardware "
        "fault, not a model problem: power-cycle the board and retry, and if "
        "it persists check that target.psram.clock_hz suits the populated "
        "PSRAM part.  If this board has no usable PSRAM, move the model and "
        "arena off it (model/arena location settings)."
    ),
    FirmwareErrorCode.BIND_ARENA_FAILED: (
        "heliaAOT rejected an externally allocated arena region — the "
        "payload carries the runtime's status and the region id.  The "
        "module and its arena table come from the SAME compiler run, so "
        "re-running changes nothing.  The known way a region id goes "
        "missing host-side is an arena whose memory kind hpx cannot map: "
        "that is skipped with a warning printed at NORMAL verbosity — look "
        "above the build output for 'AOT planner emitted unrecognised "
        "memory ... skipping'."
    ),
    FirmwareErrorCode.CONST_BLOB_PSRAM_WRITE_FAILED: (
        "Copying a constant sidecar blob into its PSRAM-placed arena region "
        "failed (the payload names the region).  PSRAM itself came up, so "
        "this points at the MSPI write path: power-cycle and retry, or try "
        "a lower target.psram.clock_hz.  Constant arenas follow the "
        "WEIGHTS placement (arena-location does not move them): set "
        "--weights-location / model.weights_location off psram, or steer "
        "the AOT planner via engine.config.aot_args.memory.tensors."
    ),
    FirmwareErrorCode.EXECUTORCH: (
        "An ExecuTorch runtime call failed: stage= names the failing call "
        "and error= is the numeric executorch::Error.  If planned= is "
        "non-zero the method needs that many planned-arena bytes — raise "
        "model.arena_size (or engine.config.planned_arena_size, which "
        "overrides it when set) to at least that value.  Otherwise check the "
        "engine config's method_arena_size / temporary_arena_size and that "
        "the .pte was exported for this runtime version."
    ),
    FirmwareErrorCode.OPERATOR_COUNT_EXCEEDS_CAPACITY: (
        "The model ran more operators than the per-layer record array holds "
        "(the payload reports the capacity); the firmware PARKS here, so no "
        "CSV follows at all.  Capacity comes from the SoC's pmu_max_ops — "
        "raise it via target.custom_socs.<name>.pmu_max_ops (a custom SoC "
        "based_on this one, with the board repointed at it) — each entry "
        "reserves a per-record struct of RAM (see the engine template's "
        "LayerRecord), so a large raise can turn this into a link failure "
        "on a small part — or profile a model with fewer executed "
        "operators."
    ),
    FirmwareErrorCode.PMU_INIT_OR_SELFTEST_FAILED: (
        "PMU bring-up failed for the named counter pass: either the PMU "
        "driver rejected the event selection or the CPU-cycles self-test "
        "read zero from a frozen counter.  The preceding "
        "HPX_PMU_INIT_STATUS / HPX_PMU_SELFTEST_CPU_CYCLES lines say which "
        "(the self-test also fails on a read error, not only a frozen "
        "counter).  Check that every selected --pmu-counters event exists "
        "on this core.  Over-selecting counters does NOT cause this error — "
        "the firmware clamps to its own per-pass counter capacity (4) and "
        "silently drops the extra columns."
    ),
}


def firmware_error_kind(line: str) -> str:
    """Return the code of an ``HPX_ERROR=<code> ...`` line.

    The code is the first token of the payload, ended by a space or a colon:
    ``unsupported_op kind=builtin ...`` and ``schema_mismatch:1234_vs_3``.
    """
    payload = line.strip().removeprefix(HPX_ERROR_PREFIX)
    return re.split(r"[ :]", payload, maxsplit=1)[0]


def raise_on_firmware_error(lines: list[str], *, power_enabled: bool = True) -> None:
    """Raise :class:`CaptureError` if the firmware reported an HPX_ERROR.

    Finds the first ``HPX_ERROR=<kind> ...`` line, extracts the kind and
    the full payload, looks up a hint, and raises.  Unknown kinds still
    raise — with a generic hint — so nothing slips through silently.

    One severity exception (#180): ``stimer_dead`` corrupts only the
    STIMER-timed clean window (per-layer counters come from the PMU), so
    without power it is a warning and the validity layer marks the
    zero-elapsed window; with power enabled it stays fatal.
    """
    for line in lines:
        s = line.strip()
        if not s.startswith(HPX_ERROR_PREFIX):
            continue

        kind = firmware_error_kind(s)
        if kind == FirmwareErrorCode.STIMER_DEAD and not power_enabled:
            log.warning(
                "Firmware reported %s but this run measures no power: "
                "per-layer PMU data is unaffected, only the clean-window "
                "duration is untrusted (the validity layer marks it). %s",
                kind,
                s,
            )
            continue

        # FirmwareErrorCode is a StrEnum, so the raw ``kind`` string off the
        # wire indexes this dict directly — including a code from firmware
        # newer than this host, which simply misses and gets the generic hint.
        hint = ERROR_HINTS.get(
            kind,
            "Firmware reported an error.  The payload is shown above.",
        )
        raise CaptureError(
            f"Firmware error: {s}",
            hint=hint,
        )
