"""Code fingerprint of a rendered firmware source (#138 / #115).

The power-comparability dimension ``POWER_FIRMWARE_FINGERPRINT`` hashes the
rendered main source of whichever binary ``CapturePowerStage`` measures, so a
firmware-semantics change stops comparing as "fully comparable" against a
baseline captured by different code (#115's +678% phantom delta) — while a
COMMENT-only template change (a frequent, deliberately byte-visible event in
this repo's review culture) leaves stored baselines untouched.

The comment stripper is the same C scanner discipline the render census uses
(``tests/contracts/test_wire_protocol.py::_split_c``): character-walk the
source, preserve string and char literals verbatim (a ``//`` inside a printf
format is content, not a comment), drop ``//`` and ``/* */`` comments. It is
deliberately NOT a curated variable set: hashing the rendered artifact after
every override is what makes attempt 1's four documented regressions
structurally impossible (see #138).

The canonical form replaces every comment with a single space and collapses
whitespace runs (outside string/char literals) to single spaces. Both are
token-stream-preserving in C — two sources equal under this canonicalization
compile to identical token sequences — and the whitespace collapse is what
makes comment-ONLY changes truly invisible: a line comment occupies a line,
so merely deleting its text while keeping its newline would still shift the
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

    Every ``//`` and ``/* */`` comment becomes a single space (never nothing —
    ``int a// c<newline>int b`` must not glue to ``int aint b``), and every
    whitespace run outside string/char literals collapses to a single space.
    String literals (``"…"``) and character literals (``'…'``) are preserved
    verbatim, escapes included, so comment markers and spacing inside them
    survive. An unterminated literal or block comment consumes to
    end-of-input rather than raising — a fingerprint must never fail a run
    over malformed input; it only has to be deterministic.
    """
    out: list[str] = []

    def emit_space() -> None:
        if out and out[-1] != " ":
            out.append(" ")

    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
            emit_space()
        elif char == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            emit_space()
        elif char == '"' or char == "'":
            quote = char
            out.append(text[i])
            i += 1
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i : i + 2])
                    i += 2
                else:
                    out.append(text[i])
                    i += 1
            if i < n:
                out.append(text[i])
                i += 1
        elif char.isspace():
            emit_space()
            i += 1
        else:
            out.append(char)
            i += 1
    return "".join(out).strip()


def firmware_code_fingerprint(rendered_source: str) -> str:
    """SHA-256 (hex) of the canonicalized rendered source."""
    return hashlib.sha256(canonical_code(rendered_source).encode("utf-8")).hexdigest()


def measured_power_fingerprint(ctx: PipelineContext) -> str | None:
    """Fingerprint of the binary ``CapturePowerStage``/the terminal measured.

    The measured binary is derived from the SAME fact the pipeline routed on
    (``power_run.plan.firmware_mode``): the dedicated ``main_power.cc``
    render, or the profile ``main.cc`` for shared firmware. Returns ``None``
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
    name = "main_power.cc" if ctx.power_run.plan.firmware_mode == "dedicated" else "main.cc"
    path = ctx.firmware_dir / "src" / name
    try:
        return firmware_code_fingerprint(path.read_text(encoding="utf-8"))
    except OSError:
        return None
