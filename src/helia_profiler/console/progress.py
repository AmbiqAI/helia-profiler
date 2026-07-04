"""Banner, spinner, and stage-progress rendering for :class:`HpxConsole`."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import HpxConsole

# Map stage names to friendlier labels + icons.
_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "preflight": ("Preflight", "✈️"),
    "resolve_platform": ("Resolve platform", "🔍"),
    "prepare_engine": ("Prepare engine", "⚙️"),
    "analyze_model": ("Analyze model", "🧠"),
    "plan_memory": ("Plan memory", "🧮"),
    "generate_firmware": ("Generate firmware", "📝"),
    "build_firmware": ("Build firmware", "🔨"),
    "flash_firmware": ("Flash firmware", "⚡"),
    "capture_pmu": ("Capture PMU", "📊"),
    "capture_power": ("Capture power", "🔋"),
    "generate_report": ("Generate report", "📄"),
}


def _mini_progress_bar(done: int, total: int, width: int = 20) -> str:
    """Return a compact Unicode progress bar like ``[████████░░░░]``."""
    if total <= 0:
        total = 1
    filled = int(width * done / total)
    empty = width - filled
    pct = int(100 * done / total)
    return f"[cyan]{'━' * filled}[/cyan][dim]{'╌' * empty}[/dim] {pct:>3}%"


def print_banner(console: HpxConsole) -> None:
    """Print the startup banner (verbosity >= 1)."""
    if console.verbosity < 1:
        return
    from .._version import __version__

    console._console.print(
        f"[bold]heliaPROFILER[/bold] [dim]v{__version__}[/dim]",
    )
    console._console.print()


def stage_start(console: HpxConsole, name: str) -> None:
    """Called when a pipeline stage begins."""
    console._stage_start = time.monotonic()
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))

    if console.verbosity >= 1:
        # Verbose: one line per stage with a live spinner while running.
        stop_spinner(console)
        console._spinner = console._console.status(
            f"  {icon}  [bold]{label}[/bold]",
            spinner="dots",
            spinner_style="cyan",
        )
        console._spinner.start()
    else:
        # Default: compact live spinner showing current stage + progress bar.
        done = len(console._completed_stages)
        total = 11  # total pipeline stages
        bar = _mini_progress_bar(done, total)
        status_text = f"{bar}  {icon}  [bold]{label}[/bold] [dim]({done}/{total})[/dim]"
        if console._spinner is None:
            console._spinner = console._console.status(
                status_text,
                spinner="dots",
                spinner_style="cyan",
            )
            console._spinner.start()
        else:
            console._spinner.update(status_text)


def stage_done(console: HpxConsole, name: str) -> None:
    """Called when a pipeline stage completes."""
    elapsed = time.monotonic() - (console._stage_start or time.monotonic())
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))
    console._completed_stages.append(name)

    if console.verbosity >= 1:
        stop_spinner(console)
        console._console.print(
            f"  {icon}  [bold]{label}[/bold] [green]✓[/green] [dim]{elapsed:.1f}s[/dim]"
        )


def stage_skip(console: HpxConsole, name: str) -> None:
    """Called when a pipeline stage is skipped."""
    console._completed_stages.append(name)
    if console.verbosity < 1:
        return
    stop_spinner(console)
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))
    console._console.print(f"  {icon}  [dim]{label} — skipped[/dim]")


def pipeline_done(console: HpxConsole) -> None:
    """Called after all stages complete — clean up any live spinner."""
    stop_spinner(console)
    if console.verbosity < 1:
        done = len(console._completed_stages)
        bar = _mini_progress_bar(done, done or 11)
        console._console.print(f"  {bar}  [green]Done[/green]")


def stop_spinner(console: HpxConsole) -> None:
    if console._spinner is not None:
        console._spinner.stop()
        console._spinner = None
