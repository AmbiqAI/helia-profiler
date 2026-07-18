"""Rich presentation helpers for interactive HPX values."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypeVar

from rich import box
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table

from .counters import PmuCounter
from .doctor import DoctorResult
from .engines import EngineType
from .platform import BoardDef
from .target.probe.jlink import JLinkProbe, JLinkProbeMatch
from .transport.ports import SerialPortInfo

T = TypeVar("T")


def show(value: T, *, console: Console | None = None) -> T:
    """Pretty-print an interactive API value and return it unchanged."""
    output = console or Console(highlight=False)
    output.print(_render(value))
    return value


def _render(value: Any) -> Any:
    if isinstance(value, DoctorResult):
        return _doctor_table(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            return "[dim]No results.[/dim]"
        first = value[0]
        if isinstance(first, JLinkProbeMatch):
            return _probe_match_table(value)
        if isinstance(first, JLinkProbe):
            return _probe_table(value)
        if isinstance(first, BoardDef):
            return _board_table(value)
        if isinstance(first, SerialPortInfo):
            return _port_table(value)
        if isinstance(first, PmuCounter):
            return _counter_table(value)
        if isinstance(first, EngineType):
            return _engine_table(value)
    return Pretty(value, expand_all=True)


def _table(*columns: tuple[str, dict[str, Any]], title: str | None = None) -> Table:
    table = Table(
        title=f"[bold]{title}[/bold]" if title else None,
        title_justify="left",
        box=box.ROUNDED,
        padding=(0, 1),
    )
    for label, options in columns:
        table.add_column(label, **options)
    return table


def _doctor_table(result: DoctorResult) -> Table:
    table = _table(
        ("", {"width": 2}),
        ("Dependency", {"min_width": 28}),
        ("Status", {}),
        ("Location", {"style": "dim"}),
        title="Environment Check",
    )
    for check in result.checks:
        if check.available:
            marker = "[green]✓[/green]"
            status = "available"
        elif check.required:
            marker = "[red]✗[/red]"
            status = "[red]missing[/red]"
        else:
            marker = "[dim]–[/dim]"
            status = "[dim]optional[/dim]"
        table.add_row(marker, check.label, status, check.path or "")
    table.caption = (
        "[green]All required dependencies found.[/green]"
        if result.ok
        else "[yellow]Some required dependencies are missing.[/yellow]"
    )
    return table


def _probe_table(probes: Sequence[JLinkProbe]) -> Table:
    table = _table(
        ("Serial", {"min_width": 12}),
        ("Product", {"min_width": 18}),
        ("Connection", {}),
        title="J-Link Probes",
    )
    for probe in probes:
        table.add_row(probe.serial, probe.product or "-", probe.connection)
    return table


def _probe_match_table(matches: Sequence[JLinkProbeMatch]) -> Table:
    table = _table(
        ("Serial", {"min_width": 12}),
        ("Product", {"min_width": 18}),
        ("Detected Core", {}),
        title="J-Link Probe Targets",
    )
    for match in matches:
        core = match.detected_core.value if match.detected_core is not None else "unknown"
        table.add_row(match.probe.serial, match.probe.product or "-", core)
    return table


def _board_table(boards: Sequence[BoardDef]) -> Table:
    table = _table(
        ("Board", {"min_width": 22}),
        ("SoC", {"min_width": 12}),
        ("Channel", {}),
        title="Boards",
    )
    for board in boards:
        table.add_row(board.name, board.soc, board.channel)
    return table


def _port_table(ports: Sequence[SerialPortInfo]) -> Table:
    table = _table(
        ("Device", {"min_width": 20}),
        ("Kind", {}),
        ("Serial", {}),
        ("Description", {}),
        title="Serial Ports",
    )
    for port in ports:
        table.add_row(port.device, port.kind, port.serial_number or "-", port.description or "-")
    return table


def _counter_table(counters: Sequence[PmuCounter]) -> Table:
    table = _table(
        ("Counter", {"min_width": 28}),
        ("Group", {}),
        ("Event", {}),
        ("Description", {}),
        title="PMU Counters",
    )
    for counter in counters:
        table.add_row(
            counter.name,
            counter.group,
            f"0x{counter.event_id:02x}",
            counter.description,
        )
    return table


def _engine_table(engines: Sequence[EngineType]) -> Table:
    table = _table(("Engine", {}), title="Inference Engines")
    for engine in engines:
        table.add_row(engine.value)
    return table
