#!/usr/bin/env python3
"""prepare-commit-msg hook: strip AI-tool attribution trailers.

Removes ``Co-authored-by:`` lines whose value names an AI coding tool
(claude, anthropic, openai, codex, copilot, chatgpt, cursor, gemini, or
``noreply@anthropic.com``) — human co-authors are left untouched — plus any
``Claude-Session:``, ``Agent-Assisted:``, ``Generated-by:``/``Generated-with:``
trailer line, regardless of value.

Git invokes this hook as ``prepare-commit-msg <msg-file> [source] [sha1]``
for every commit source (``-m``, ``-F``, template, merge, squash, amend);
this operates on the message file the same way for all of them. Line
endings are preserved exactly (CRLF-safe) and everything at or after the
commit scissors line (``git commit -v`` / ``--cleanup=scissors``) is left
untouched. An empty or missing message file is a no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SCISSORS = b"# ------------------------ >8 ------------------------"

TRAILER_RE = re.compile(rb"^([A-Za-z][A-Za-z0-9-]*)[ \t]*:[ \t]*(.*)$")
AI_COAUTHOR_RE = re.compile(
    rb"claude|anthropic|openai|codex|copilot|chatgpt|cursor|gemini|noreply@anthropic\.com",
    re.IGNORECASE,
)
COAUTHOR_KEY = b"co-authored-by"
DROP_KEYS = {b"claude-session", b"agent-assisted", b"generated-by", b"generated-with"}


def _split_ending(line: bytes) -> tuple[bytes, bytes]:
    """Return (content, line-ending) so the ending can be reused verbatim."""
    for ending in (b"\r\n", b"\n", b"\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, b""


def _should_drop(content: bytes) -> bool:
    match = TRAILER_RE.match(content)
    if not match:
        return False
    key = match.group(1).lower()
    if key == COAUTHOR_KEY:
        return bool(AI_COAUTHOR_RE.search(match.group(2)))
    return key in DROP_KEYS


def strip_ai_trailers(raw: bytes) -> bytes:
    if not raw:
        return raw
    lines = raw.splitlines(keepends=True)
    scissors_at = next(
        (i for i, line in enumerate(lines) if _split_ending(line)[0] == SCISSORS),
        None,
    )
    editable = lines if scissors_at is None else lines[:scissors_at]
    protected = b"" if scissors_at is None else b"".join(lines[scissors_at:])
    kept = [line for line in editable if not _should_drop(_split_ending(line)[0])]
    return b"".join(kept) + protected


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    path = Path(argv[0])
    try:
        raw = path.read_bytes()
    except OSError:
        return 0
    if not raw:
        return 0
    updated = strip_ai_trailers(raw)
    if updated != raw:
        path.write_bytes(updated)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
