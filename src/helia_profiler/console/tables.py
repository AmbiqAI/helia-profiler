"""Shared numeric/byte formatting helpers used across console submodules."""

from __future__ import annotations

from typing import Any


def _progress_bar(pct: float, width: int = 20) -> str:
    """Return a simple Unicode progress bar."""
    filled = int(pct / 100 * width)
    filled = max(0, min(width, filled))
    return f"[cyan]{'━' * filled}[/cyan][dim]{'╌' * (width - filled)}[/dim]"


def _fmt_bytes(n: int) -> str:
    """Format a byte count with KB/MB suffix for display."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
