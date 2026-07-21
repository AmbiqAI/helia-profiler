"""Banner, spinner, and stage-progress rendering for :class:`HpxConsole`."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline import ProgressUpdate
    from .base import HpxConsole

# Map stage names to friendlier labels + icons.
_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "preflight": ("Preflight", "✈️"),
    "ensure_board_powered": ("Ensure board powered", "▸"),
    "resolve_platform": ("Resolve platform", "🔍"),
    "resolve_jlink_probe": ("Resolve debug probe", "▸"),
    "prepare_engine": ("Prepare engine", "⚙️"),
    "analyze_model": ("Analyze model", "🧠"),
    "plan_memory": ("Plan memory", "🧮"),
    "generate_firmware": ("Generate firmware", "📝"),
    "build_firmware": ("Build profile firmware", "🔨"),
    "verify_placement": ("Verify placement", "▸"),
    "flash_firmware": ("Flash profile firmware", "⚡"),
    "capture_pmu": ("Capture profile", "📊"),
    "plan_power_run": ("Plan power run", "▸"),
    "build_power_firmware": ("Build power firmware", "🔨"),
    "flash_power_firmware": ("Flash power firmware", "⚡"),
    "capture_power": ("Capture power", "🔋"),
    "collect_power_terminal": ("Collect firmware status", "▸"),
    "generate_report": ("Generate report", "📄"),
}

_PROFILE_STAGES = {
    "generate_firmware",
    "build_firmware",
    "verify_placement",
    "flash_firmware",
    "capture_pmu",
}
_POWER_STAGES = {
    "plan_power_run",
    "build_power_firmware",
    "flash_power_firmware",
    "capture_power",
    "collect_power_terminal",
}


def _phase_for_stage(name: str) -> str:
    if name in _PROFILE_STAGES:
        return "Profile"
    if name in _POWER_STAGES:
        return "Power"
    if name == "generate_report":
        return "Report"
    return "Setup"


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

    console._status_console.print(
        f"[bold]heliaPROFILER[/bold] [dim]v{__version__}[/dim]",
    )
    console._status_console.print()


def stage_start(console: HpxConsole, name: str, index: int = 0, total: int = 0) -> None:
    """Called when a pipeline stage begins."""
    console._stage_start = time.monotonic()
    console._stage_name = name
    console._stage_index = index
    console._stage_total = total
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))
    phase = _phase_for_stage(name)

    if console.verbosity >= 1:
        # Verbose: one line per stage with a live spinner while running.
        stop_spinner(console)
        if phase != console._phase_name:
            console._status_console.print(f"[bold cyan]{phase}[/bold cyan]")
        console._spinner = console._status_console.status(
            f"  {icon}  [bold]{label}[/bold]",
            spinner="dots",
            spinner_style="cyan",
        )
        console._spinner.start()
    else:
        # Default: compact live spinner showing current stage + progress bar.
        done = max(0, index - 1) if index else len(console._completed_stages)
        total = total or max(1, done + 1)
        bar = _mini_progress_bar(done, total)
        status_text = (
            f"{bar}  [cyan]{phase}[/cyan] · {icon}  [bold]{label}[/bold] "
            f"[dim]({done}/{total})[/dim]"
        )
        if console._spinner is None:
            console._spinner = console._status_console.status(
                status_text,
                spinner="dots",
                spinner_style="cyan",
            )
            console._spinner.start()
        else:
            console._spinner.update(status_text)
    console._phase_name = phase


def _format_eta(eta_s: float) -> str:
    seconds = max(0, int(round(eta_s)))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def progress_update(console: HpxConsole, update: ProgressUpdate) -> None:
    """Render a detail or checkpoint for the active stage."""
    if console.verbosity < update.min_verbosity:
        return
    label, icon = _STAGE_LABELS.get(
        console._stage_name or "", (console._stage_name or "Working", "▸")
    )
    details: list[str] = []
    if update.completed is not None and update.total is not None:
        unit = f" {update.unit}" if update.unit else ""
        details.append(f"{update.completed}/{update.total}{unit}")
    if update.eta_s is not None:
        details.append(f"about {_format_eta(update.eta_s)} remaining")
    suffix = f" [dim]({' · '.join(details)})[/dim]" if details else ""

    if update.kind == "checkpoint" and console.verbosity >= 1:
        stop_spinner(console)
        console._status_console.print(f"  {icon}  [bold]{update.message}[/bold]{suffix}")
        return

    position = ""
    if console._stage_index and console._stage_total:
        position = f" [dim]({console._stage_index}/{console._stage_total})[/dim]"
    phase = console._phase_name or "Setup"
    text = (
        f"  [cyan]{phase}[/cyan] · {icon}  [bold]{label}[/bold]: "
        f"{update.message}{suffix}{position}"
    )
    if console._spinner is None:
        console._spinner = console._status_console.status(
            text,
            spinner="dots",
            spinner_style="cyan",
        )
        console._spinner.start()
    else:
        console._spinner.update(text)


def stage_done(console: HpxConsole, name: str) -> None:
    """Called when a pipeline stage completes."""
    elapsed = time.monotonic() - (console._stage_start or time.monotonic())
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))
    console._completed_stages.append(name)

    if console.verbosity >= 1:
        stop_spinner(console)
        console._status_console.print(
            f"  {icon}  [bold]{label}[/bold] [green]✓[/green] [dim]{elapsed:.1f}s[/dim]"
        )


def stage_skip(console: HpxConsole, name: str) -> None:
    """Called when a pipeline stage is skipped."""
    console._completed_stages.append(name)
    if console.verbosity < 1:
        return
    stop_spinner(console)
    label, icon = _STAGE_LABELS.get(name, (name, "▸"))
    console._status_console.print(f"  {icon}  [dim]{label} — skipped[/dim]")


def pipeline_done(console: HpxConsole) -> None:
    """Called after all stages complete — clean up any live spinner."""
    stop_spinner(console)
    if console.verbosity < 1:
        done = len(console._completed_stages)
        bar = _mini_progress_bar(done, console._stage_total or done or 1)
        console._status_console.print(f"  {bar}  [green]Done[/green]")


def stop_spinner(console: HpxConsole) -> None:
    if console._spinner is not None:
        console._spinner.stop()
        console._spinner = None
