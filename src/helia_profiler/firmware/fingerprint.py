"""Code fingerprint of a rendered firmware source (#138 / #115 / #173).

``POWER_FIRMWARE_FINGERPRINT`` hashes the RENDERED SOURCE SET of the binary
``CapturePowerStage`` measures -- the main translation unit plus
``hpx_pmu_profiler.{cc,h}``, whose hooks run inside the gated window -- so a
firmware-semantics change stops comparing as fully comparable, while a
comment-only template change leaves stored baselines untouched.

Not covered, by design: rendered build configuration (``CMakeLists.txt``,
``modules.cmake``, ``nsx.yml``) and external module sources -- build
configuration is a separate comparison axis, and dependency identity is
partly covered by the toolchain dimensions. The claim is "the rendered C
sources of the measured target", not "the exact binary".

The canonical form strips comments and collapses whitespace with the C
scanner discipline the render census uses (string/char literals verbatim),
keeping newlines that touch a preprocessing directive and the directive
state across backslash continuations, so it is token-stream- and
directive-structure-preserving for the C the templates emit; its known
over-sensitivities (#173) can only over-differentiate, never collide.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def canonical_code(text: str) -> str:
    """Canonical token-preserving form: comments and whitespace normalized.

    Every comment becomes a single space (never nothing, so adjacent tokens
    cannot glue) and every whitespace run outside string/char literals
    collapses to one space -- except a newline run touching a preprocessing
    directive line, which survives because newline is significant to the
    preprocessor (#173). A ``/* */`` comment's internal newlines do not count.
    Literals are preserved verbatim, but literal scanning stops at an
    unescaped newline so one stray apostrophe cannot swallow the file (#173).
    Malformed input degrades rather than fails: a fingerprint must never fail
    a run, only be deterministic.
    """
    out: list[str] = []
    pending_ws = False
    pending_nl = False
    line_is_directive = False
    line_has_content = False
    last_char = ""

    def emit(chunk: str) -> None:
        nonlocal pending_ws, pending_nl, line_is_directive, line_has_content, last_char
        if pending_ws:
            if pending_nl and (line_is_directive or chunk.startswith("#")):
                if out:
                    out.append("\n")
                # A directive line ending in a backslash CONTINUES past the
                # newline (translation phase 2 splices it), so the directive
                # state must survive onto the next physical line — resetting
                # it here let a continued macro body's real terminating
                # newline collapse, hashing two semantically different
                # programs equal (#173 round-2 review M-A).
                if not (line_is_directive and last_char == "\\"):
                    line_is_directive = False
                line_has_content = line_is_directive
            elif out:
                out.append(" ")
            pending_ws = False
            pending_nl = False
        if not line_has_content and chunk.startswith("#"):
            line_is_directive = True
        line_has_content = True
        last_char = chunk[-1]
        out.append(chunk)

    def note_ws(newline: bool) -> None:
        nonlocal pending_ws, pending_nl
        pending_ws = True
        pending_nl = pending_nl or newline

    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            note_ws(False)
        elif char == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            note_ws(False)
        elif char == '"' or char == "'":
            quote = char
            j = i + 1
            buf = [quote]
            while j < n and text[j] != quote and text[j] != "\n":
                # The newline guard outranks the escape pair: consuming a
                # backslash-newline as an "escape" let an unterminated
                # literal on a continued line swallow the next physical line
                # (#173 round-2 review M-A, second route).
                if text[j] == "\\" and j + 1 < n and text[j + 1] != "\n":
                    buf.append(text[j : j + 2])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            if j < n and text[j] == quote:
                buf.append(quote)
                j += 1
            emit("".join(buf))
            i = j
        elif char.isspace():
            note_ws(char == "\n")
            i += 1
        else:
            emit(char)
            i += 1
    return "".join(out).strip()


def firmware_code_fingerprint(rendered_source: str) -> str:
    """SHA-256 (hex) of the canonicalized rendered source."""
    return hashlib.sha256(canonical_code(rendered_source).encode("utf-8")).hexdigest()


def measured_power_fingerprint(ctx: PipelineContext) -> str | None:
    """Fingerprint of the measured target's rendered C source set.

    The measured binary is derived from the SAME fact the pipeline routed on
    (``power_run.plan.firmware_mode``): the dedicated ``main_power.cc``
    render, or the profile ``main.cc`` for shared firmware — plus
    ``hpx_pmu_profiler.{cc,h}``, compiled into the same target (see the
    module docstring for what is deliberately NOT covered). Returns ``None``
    — the legacy/no-power value the comparability reader skips — whenever no
    power run was planned or the rendered source is not readable: a
    fingerprint must never fail a run.

    Computed at report time rather than capture time: ``CapturePowerStage``
    does not run for internal mode and no earlier stage covers every mode x
    firmware combination, while the rendered sources persist in
    ``firmware_dir`` through report generation (#115, #173).
    """
    if ctx.power_run is None or ctx.firmware_dir is None:
        return None
    src_dir = ctx.firmware_dir / "src"
    main_name = (
        "main_power.cc" if ctx.power_run.plan.firmware_mode == "dedicated" else "main.cc"
    )
    try:
        main_digest = firmware_code_fingerprint(
            (src_dir / main_name).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError: a partially-written or
        # corrupt source must degrade to the absent/legacy value, never
        # fail the run at report time (#173 round-2 review m-F).
        return None
    # The scheme tag makes hasher changes legible: a canonicalizer or
    # file-set change shifts every fingerprint, and without the tag that
    # mismatch would present as "the firmware changed" — asserting a cause
    # that is false (#173 round-2 review m-C). Bump it whenever the
    # construction below changes.
    parts = ["scheme\x00hpx-power-fingerprint-v2", f"{main_name}\x00{main_digest}"]
    # The TFLM/heliaRT builds compile these into the SAME target (AOT and
    # ExecuTorch render no profiler TU — they fold in as "absent", matching
    # not-compiled). In the dedicated power binary the profiler's hooks
    # early-return, but its code is linked and its prologue runs in-window —
    # hashing only the main TU let a profiler-template edit reproduce the
    # #115 shape undetected (#173 review M1). A missing file folds in as
    # "absent": deterministic, and distinct from any present content.
    for name in ("hpx_pmu_profiler.cc", "hpx_pmu_profiler.h"):
        try:
            digest = firmware_code_fingerprint(
                (src_dir / name).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            digest = "absent"
        parts.append(f"{name}\x00{digest}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
