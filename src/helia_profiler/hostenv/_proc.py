"""Shared subprocess mechanics for read-only host tool probes."""

from __future__ import annotations

import logging
import subprocess

log = logging.getLogger("hpx")


def tool_output(command: list[str], *, timeout_s: int) -> str | None:
    """Stdout of *command*, or ``None`` when it cannot run, times out, or exits nonzero.

    Output is decoded as UTF-8 with replacement: symbol and section names are
    arbitrary bytes, and a strict platform codec (cp1252 on Windows) would
    raise mid-decode instead of degrading to ``None``.
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("%s probe failed: %s", command[0], exc)
        return None
    if result.returncode != 0:
        log.debug(
            "%s exited %d: %s",
            " ".join(command),
            result.returncode,
            (result.stderr or "").strip(),
        )
        return None
    return result.stdout or ""
