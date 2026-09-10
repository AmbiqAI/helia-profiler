"""Shared subprocess mechanics for read-only host tool probes."""

from __future__ import annotations

import subprocess


def run_text(command: list[str], *, timeout_s: int) -> subprocess.CompletedProcess[str]:
    """Capture UTF-8 output, replacing undecodable bytes; leave failures to the caller."""
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
