"""Rendering of ``hpx doctor``, ``hpx boards``, ``hpx engines``, and errors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich import box
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from .base import HpxConsole


def print_error(console: HpxConsole, exc: Exception) -> None:
    """Render a user-facing error."""
    from ..errors import HpxError

    if isinstance(exc, HpxError):
        msg = Text()
        msg.append("Error: ", style="bold red")
        # Use the bare message (args[0]) rather than str(exc): HpxError.__str__
        # embeds the hint for logs/tracebacks, but this renderer emits its own
        # styled hint line below and would otherwise print the hint twice.
        msg.append(str(exc.args[0]) if exc.args else str(exc))
        if exc.hint:
            msg.append(f"\n  hint: {exc.hint}", style="dim")
        details = getattr(exc, "details", None)
        if details:
            rendered = "\n    ".join(str(details).splitlines())
            msg.append(f"\n  details: {rendered}", style="dim")
        console._console.print(msg, highlight=False)
    else:
        console._console.print(f"[bold red]Error:[/bold red] {exc}")


def print_interrupted(console: HpxConsole) -> None:
    """Print a clean one-liner on Ctrl-C."""
    console._stop_spinner()
    console._console.print("\n[dim]Interrupted.[/dim]")


def print_doctor(
    console: HpxConsole,
    checks: list[tuple[str, str, str | None]],
    required_python: list[tuple[str, str, bool]],
    optional: list[tuple[str, str, bool]],
) -> None:
    """Render ``hpx doctor`` results.

    *checks*: list of ``(label, binary_name, path_or_none)``
    *required_python*: list of ``(label, package_name, available)``
    *optional*: list of ``(label, package_name, available)``
    """
    console._console.print()
    table = Table(
        title="[bold]Toolchain Check[/bold]",
        box=box.ROUNDED,
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("", width=2)
    table.add_column("Tool", min_width=28)
    table.add_column("Path", style="dim")

    all_ok = True
    for label, _binary, path in checks:
        if path:
            table.add_row("[green]✓[/green]", label, path)
        else:
            table.add_row("[red]✗[/red]", f"[red]{label}[/red]", "[red]not found[/red]")
            all_ok = False

    for label, _pkg, available in required_python:
        if available:
            table.add_row("[green]✓[/green]", label, "installed")
        else:
            table.add_row("[red]✗[/red]", f"[red]{label}[/red]", "[red]not installed[/red]")
            all_ok = False

    for label, _pkg, available in optional:
        if available:
            table.add_row("[green]✓[/green]", f"[dim]{label}[/dim]", "[dim]installed[/dim]")
        else:
            table.add_row("[dim]–[/dim]", f"[dim]{label}[/dim]", "[dim]not installed[/dim]")

    console._console.print(table)
    console._console.print()

    if all_ok:
        console._console.print("  [green]All required tools found.[/green]")
    else:
        console._console.print(
            "  [yellow]Some required tools are missing. Install them before profiling.[/yellow]"
        )
    console._console.print()


def print_boards(console: HpxConsole, boards: list[tuple[str, str, str, str, str, str]]) -> None:
    """Render the boards list.

    Each tuple: ``(board, soc, core, backends, domains, channel)``
    """
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        padding=(0, 1),
    )
    table.add_column("Board", min_width=22)
    table.add_column("SoC", min_width=12)
    table.add_column("Core", min_width=12)
    table.add_column("Backends", min_width=18)
    table.add_column("Domains", min_width=14)
    table.add_column("Channel")

    for brd, soc, core, backends, domains, channel in boards:
        table.add_row(brd, soc, core, backends, domains, channel)

    console._console.print(table)


def print_engines(console: HpxConsole, engines: list[str]) -> None:
    """Render the engine list."""
    for engine in engines:
        console._console.print(f"  [bold]{engine}[/bold]")
