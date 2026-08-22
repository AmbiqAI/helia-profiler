"""Code fingerprint of a rendered firmware source (#138 / #115).

The power-comparability dimension ``POWER_FIRMWARE_FINGERPRINT`` hashes the
RENDERED SOURCE SET of whichever binary ``CapturePowerStage`` measures — the
main translation unit plus ``hpx_pmu_profiler.{cc,h}``, which the build
compiles into the same target and whose per-operator hooks execute inside
the gated window (#173 review M1) — so a firmware-semantics change stops
comparing as "fully comparable" against a baseline captured by different
code (#115's +678% phantom delta), while a COMMENT-only template change (a
frequent, deliberately byte-visible event in this repo's review culture)
leaves stored baselines untouched.

Accepted residuals, documented rather than silently claimed: rendered build
configuration (``CMakeLists.txt`` compile options/definitions,
``modules.cmake``, ``nsx.yml``) and external module sources are NOT part of
the hash — ``nsx.yml`` carries a known set-ordering nondeterminism that
would make the fingerprint differ run-to-run, and dependency identity is
partially covered by the toolchain/compiler dimensions. The claim is
therefore "the rendered C sources of the measured target", not "the exact
binary".

The comment stripper is the same C scanner discipline the render census uses
(``tests/contracts/test_wire_protocol.py::_split_c``): character-walk the
source, preserve string and char literals verbatim (a ``//`` inside a printf
format is content, not a comment), drop ``//`` and ``/* */`` comments. It is
deliberately NOT a curated variable set: hashing the rendered artifact after
every override is what makes attempt 1's four documented regressions
structurally impossible (see #138).

The canonical form replaces every comment with a single space and collapses
whitespace runs (outside string/char literals) to single spaces — EXCEPT
that a newline adjacent to a preprocessing-directive line survives as a
newline: newline is significant in translation phases 3/4, and collapsing
it let ``#define A 1\nint x;`` canonicalize equal to its one-line,
semantically different join (#173 review m1, demonstrated on a real
render), and the directive state survives a backslash-continued line
(phase-2 splicing — round 2 proved a continued macro body's terminating
newline otherwise collapsed, hashing two different programs equal). With
those carve-outs the canonicalization is token-stream- and
directive-structure-preserving for the C the templates emit. Known
fail-safe over-sensitivities (#173 round-3 review), both unreachable in
rendered C today: a backslash continuation on a NON-directive line is not
spliced (the semantically identical one-line form hashes differently), and
a comment between a directive's backslash and its newline is treated as a
continuation even though real phase-2 splicing precedes comment stripping —
each can only over-differentiate, never collide. Pathological inputs (an
unterminated literal feeding a continuation) may canonicalize
non-idempotently, which is harmless — the function is applied once, to
rendered sources. The whitespace collapse is what makes
comment-ONLY changes truly invisible: a line comment occupies a line, so
merely deleting its text while keeping its newline would still shift the
hash on every comment insertion (found by this module's own stability test
against a real render).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def canonical_code(text: str) -> str:
    """Canonical token-preserving form: comments and whitespace normalized.

    Every comment becomes a single space (never nothing — ``int a// c`` plus
    ``int b`` on the next line must not glue to ``int aint b``), and every
    whitespace run outside string/char literals collapses to a single space —
    except that a newline run touching a preprocessing-directive line (the
    closed line began with ``#``, or the next content starts with ``#``)
    survives as a newline, because newline is significant to the
    preprocessor and collapsing it made semantically different sources hash
    equal (#173 review m1). A ``/* */`` comment's INTERNAL newlines do not
    count — translation phase 3 replaces the whole comment with one space
    before directives are processed, so a directive continues across it.

    String and char literals are preserved verbatim, escapes included, but
    literal scanning terminates at an unescaped newline: C literals cannot
    span lines without continuation, and consuming past the newline let one
    stray apostrophe (a digit separator, ``1'000``) swallow the rest of the
    file and silently disable comment stripping (#173 review m2). Malformed
    input degrades — a fingerprint must never fail a run; it only has to be
    deterministic.
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

    Computed at report time rather than capture time (D3's letter was the
    capture prologue, but ``CapturePowerStage`` never runs for internal mode
    — the very mode of #115's phantom delta — and no earlier single stage
    covers all four mode x firmware combinations; the rendered sources
    persist in ``firmware_dir`` through report generation).
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
