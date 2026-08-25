"""Rich rendering for completed ``hpx validate`` sweeps."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .tables import _fmt_bytes

if TYPE_CHECKING:
    from ..validation.report import ValidationReport
    from ..validation.runner import CaseResult
    from .base import HpxConsole

_MAX_ROWS = 20


def _cmsis_nn_provider(case: CaseResult) -> str | None:
    """Return explicit provider metadata or infer it for legacy results."""
    if case.cmsis_nn_provider:
        return case.cmsis_nn_provider
    engine = str(case.engine)
    if engine == "executorch" and case.backend in {"arm", "ns"}:
        return case.backend
    if engine == "tflm":
        return "arm"
    if engine in {"helia-rt", "helia-aot"}:
        return "ns"
    return None


def _decision_group(case: CaseResult) -> str:
    return case.comparison_group or case.model_id


def _eligible(case: CaseResult) -> bool:
    return (
        case.status == "pass"
        and not case.health_issues
        and case.total_cycles is not None
        and case.total_cycles > 0
    )


def _pareto_cases(cases: list[CaseResult]) -> set[str]:
    # (case_id, total_cycles, binary_total_bytes); _eligible guarantees
    # total_cycles is present, the size filter guarantees binary_total_bytes.
    candidates: list[tuple[str, int, int]] = []
    for case in cases:
        cycles = case.total_cycles
        size = case.binary_total_bytes
        if _eligible(case) and cycles is not None and size is not None and size > 0:
            candidates.append((case.case_id, cycles, size))
    frontier: set[str] = set()
    for index, (case_id, cycles, size) in enumerate(candidates):
        dominated = any(
            other_cycles <= cycles
            and other_size <= size
            and (other_cycles < cycles or other_size < size)
            for other_index, (_, other_cycles, other_size) in enumerate(candidates)
            if other_index != index
        )
        if not dominated:
            frontier.add(case_id)
    return frontier


def _decision_tags(cases: list[CaseResult]) -> dict[str, tuple[str, ...]]:
    eligible = [case for case in cases if _eligible(case)]
    if not eligible:
        return {}

    tags: dict[str, tuple[str, ...]] = {}
    groups: dict[str, list[CaseResult]] = {}
    for case in eligible:
        groups.setdefault(_decision_group(case), []).append(case)

    for group_cases in groups.values():
        fastest = min(case.total_cycles for case in group_cases if case.total_cycles is not None)
        sized = [
            case
            for case in group_cases
            if case.binary_total_bytes is not None and case.binary_total_bytes > 0
        ]
        smallest = (
            min(case.binary_total_bytes for case in sized if case.binary_total_bytes is not None)
            if sized
            else None
        )
        pareto = _pareto_cases(group_cases)

        for case in group_cases:
            values: list[str] = []
            if case.total_cycles == fastest:
                values.append("fastest")
            if smallest is not None and case.binary_total_bytes == smallest:
                values.append("smallest")
            if case.case_id in pareto and not values:
                values.append("pareto")
            if values:
                tags[case.case_id] = tuple(values)
    return tags


def _selected_cases(cases: list[CaseResult], tags: dict[str, tuple[str, ...]]) -> list[CaseResult]:
    def key(case: CaseResult) -> tuple[int, int, float, int, str]:
        case_tags = tags.get(case.case_id, ())
        return (
            0 if "fastest" in case_tags or "smallest" in case_tags else 1,
            0 if "pareto" in case_tags else 1,
            float(case.total_cycles) if case.total_cycles is not None else float("inf"),
            case.binary_total_bytes if case.binary_total_bytes is not None else 2**63,
            case.case_id,
        )

    eligible = sorted((case for case in cases if _eligible(case)), key=key)
    return eligible[:_MAX_ROWS]


def _format_optional(value: float | int | None, *, digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{digits}f}"


def _format_tags(values: tuple[str, ...]) -> str:
    styles = {
        "fastest": "bold green",
        "smallest": "bold cyan",
        "pareto": "magenta",
    }
    return " ".join(f"[{styles[value]}]{value}[/{styles[value]}]" for value in values)


def _compact_config(case: CaseResult) -> str:
    board = case.board.removesuffix("_evb").replace("apollo", "ap")
    toolchain = {
        "arm-none-eabi-gcc": "gcc",
        "armclang": "acfe",
    }.get(case.toolchain, case.toolchain)
    return f"{board} / {toolchain} / {case.transport} / {case.memory}"


def print_validation(
    console: HpxConsole,
    report: ValidationReport,
    *,
    output_paths: list[Path] | None = None,
) -> None:
    """Render a compact decision view after a validation sweep."""

    cases = list(report.cases)
    tags = _decision_tags(cases)
    selected = _selected_cases(cases, tags)
    passed = sum(case.status == "pass" for case in cases)
    failed = sum(case.status == "fail" for case in cases)
    skipped = sum(case.status == "skip" for case in cases)
    eligible = sum(_eligible(case) for case in cases)

    console._console.print()
    console._console.print(Rule("[bold]Validation Results[/bold]", style="bright_blue"))
    console._console.print()

    overview = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    overview.add_column("key", style="dim", no_wrap=True)
    overview.add_column("value")
    overview.add_row("Cases", f"{len(cases)} total")
    overview.add_row(
        "Outcomes",
        f"[green]{passed} passed[/green]  [red]{failed} failed[/red]  [dim]{skipped} skipped[/dim]",
    )
    overview.add_row("Decision-eligible", str(eligible))
    overview.add_row("Models", ", ".join(sorted({escape(case.model_id) for case in cases})))
    overview.add_row(
        "Comparison groups",
        ", ".join(sorted({escape(_decision_group(case)) for case in cases})),
    )
    console._console.print(overview)
    console._console.print()

    if selected:
        title = "[bold]Performance and Size Candidates[/bold]"
        if len(selected) < eligible:
            title += f" [dim](showing {len(selected)} of {eligible})[/dim]"
        compact = console._console.width < 110
        table = Table(
            title=title,
            box=box.SIMPLE_HEAVY,
            show_edge=False,
            title_justify="left",
            padding=(0, 0) if compact else (0, 1),
        )
        table.add_column("Model", min_width=7)
        show_group = any(_decision_group(case) != case.model_id for case in selected)
        if show_group:
            table.add_column("Group", min_width=7)
        table.add_column("Engine", min_width=9)
        if compact:
            table.add_column("Configuration", min_width=16, overflow="ellipsis")
        else:
            table.add_column("Board", min_width=10, overflow="ellipsis")
            table.add_column("Toolchain / Interface / Memory", min_width=16, overflow="ellipsis")
        show_attempt = any(case.repeat_total > 1 for case in selected)
        show_latency = not compact and any(case.latency_avg_us is not None for case in selected)
        show_binary = any(case.binary_total_bytes is not None for case in selected)
        show_arena = not compact and any(
            case.allocated_arena_bytes is not None or case.arena_size_bytes is not None
            for case in selected
        )
        show_energy = not compact and any(case.energy_uj is not None for case in selected)
        if show_attempt:
            table.add_column("Run", justify="right", width=4)
        table.add_column("Cycles", justify="right", min_width=11)
        if show_latency:
            table.add_column("Latency", justify="right", min_width=10)
        if show_binary:
            table.add_column("Binary", justify="right", min_width=9)
        if show_arena:
            table.add_column("Arena", justify="right", min_width=9)
        if show_energy:
            table.add_column("Energy", justify="right", min_width=9)
        table.add_column("Decision", min_width=9)

        for case in selected:
            row = [
                escape(case.model_id),
            ]
            if show_group:
                row.append(escape(_decision_group(case)))
            engine = str(case.engine)
            provider = _cmsis_nn_provider(case)
            if provider:
                engine = f"{engine}/{provider}"
            row.append(escape(engine))
            if compact:
                row.append(escape(_compact_config(case)))
            else:
                row.extend(
                    [
                        escape(case.board),
                        escape(f"{case.toolchain} / {case.transport} / {case.memory}"),
                    ]
                )
            if show_attempt:
                row.append(str(case.attempt))
            row.append(_format_optional(case.total_cycles))
            if show_latency:
                row.append(
                    f"{_format_optional(case.latency_avg_us, digits=1)} µs"
                    if case.latency_avg_us is not None
                    else "—"
                )
            if show_binary:
                row.append(
                    _fmt_bytes(case.binary_total_bytes)
                    if case.binary_total_bytes is not None
                    else "—"
                )
            if show_arena:
                arena_bytes = case.allocated_arena_bytes or case.arena_size_bytes
                row.append(_fmt_bytes(arena_bytes) if arena_bytes is not None else "—")
            if show_energy:
                row.append(
                    f"{_format_optional(case.energy_uj, digits=1)} µJ"
                    if case.energy_uj is not None
                    else "—"
                )
            row.append(_format_tags(tags.get(case.case_id, ())))
            table.add_row(*row)
        console._console.print(table)
        console._console.print()
    else:
        console._console.print(
            Panel(
                "No passing, healthy cases with cycle measurements were available for comparison.",
                title="[bold yellow]No decision candidates[/bold yellow]",
                border_style="yellow",
                expand=False,
            )
        )
        console._console.print()

    problematic = [case for case in cases if case.status != "pass" or case.health_issues]
    if problematic:
        body = Text()
        for case in problematic[:5]:
            reason = case.error or "; ".join(case.health_issues) or case.status
            body.append(f"  {case.case_id}: ", style="yellow")
            body.append(f"{reason}\n")
        if len(problematic) > 5:
            body.append(f"  … and {len(problematic) - 5} more\n", style="dim")
        console._console.print(
            Panel(
                body,
                title="[bold yellow]Failed, skipped, or unhealthy cases[/bold yellow]",
                title_align="left",
                border_style="yellow",
                expand=False,
            )
        )
        console._console.print()

    if output_paths:
        output_dir = output_paths[0].parent.resolve()
        files = Text()
        for path in output_paths:
            files.append(f"  {path.name}\n", style="dim")
        console._console.print(
            Panel(
                files,
                title=(
                    f"[bold]Output → [link={output_dir.as_uri()}]"
                    f"{escape(str(output_dir))}[/link][/bold]"
                ),
                title_align="left",
                border_style="bright_blue",
                expand=False,
            )
        )
