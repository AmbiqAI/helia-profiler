"""Rendering of ``hpx compare`` output (compare tables + formatting helpers)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich import box
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .tables import _to_float as _to_compare_float

if TYPE_CHECKING:
    from ..evaluation import CompareResult, LayerDiffRow, MetricDiff
    from .base import HpxConsole


def _build_verdict_panel(result: CompareResult) -> Panel | None:
    verdict = result.verdict
    if verdict is None:
        return None
    styles = {
        "pass": ("green", "PASS"),
        "warn": ("yellow", "WARN"),
        "fail": ("red", "FAIL"),
        "skip": ("dim", "SKIP"),
    }
    style, label = styles[verdict.status.value]
    body = Text()
    body.append(label, style=f"bold {style}")
    if verdict.profile_name:
        body.append(f"  {verdict.profile_name}")
    failed = [item.metric for item in verdict.metrics if item.status.value == "fail"]
    warned = [item.metric for item in verdict.metrics if item.status.value == "warn"]
    if verdict.dimension_mismatches:
        body.append("\nDimensions: " + ", ".join(verdict.dimension_mismatches), style="red")
    if failed:
        body.append("\nRegressions: " + ", ".join(failed), style="red")
    if warned:
        body.append("\nWarnings: " + ", ".join(warned), style="yellow")
    return Panel(
        body,
        title="[bold]Regression Verdict[/bold]",
        title_align="left",
        border_style=style,
        padding=(0, 2),
        expand=False,
    )


def _find_compare_metric(metrics: list[MetricDiff], name: str) -> MetricDiff | None:
    for metric in metrics:
        if metric.name == name:
            return metric
    return None


def _build_compare_config_table(result: CompareResult) -> Table:
    table = Table(
        title="[bold]Config[/bold]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("Field", style="dim", min_width=14)
    table.add_column("Baseline", min_width=18, overflow="ellipsis")
    table.add_column("Candidate", min_width=18, overflow="ellipsis")
    table.add_column("Status", justify="center", width=8)

    for row in result.config_rows:
        changed = row.status == "diff"
        status = "[yellow]diff[/yellow]" if changed else "[green]same[/green]"
        value_style = "yellow" if changed else ""
        table.add_row(
            str(row.field or ""),
            _style_compare_value(row.baseline, value_style),
            _style_compare_value(row.candidate, value_style),
            status,
        )

    return table


def _build_compare_run_table(metrics: list[MetricDiff]) -> Table:
    table = Table(
        title="[bold]Run[/bold]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("Metric", min_width=24)
    table.add_column("Baseline", justify="right", min_width=12)
    table.add_column("Candidate", justify="right", min_width=12)
    table.add_column("Change", justify="right", min_width=16)

    for metric in metrics:
        lower_is_better = metric.name != "layers"
        table.add_row(
            _friendly_metric_name(metric.name),
            _format_compare_value_compact(metric.baseline, metric.unit),
            _format_compare_value_compact(metric.candidate, metric.unit),
            _format_compare_change_compact(metric, lower_is_better=lower_is_better),
        )

    return table


def _build_compare_layer_table(layer_rows: list[LayerDiffRow], *, top_layers: int) -> Table:
    top_layers = max(0, top_layers)
    table = Table(
        title=f"[bold]Layers[/bold] [dim](top {top_layers} by absolute cycle delta)[/dim]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Operator", min_width=16, overflow="ellipsis")
    table.add_column("Baseline", justify="right", min_width=8)
    table.add_column("Candidate", justify="right", min_width=8)
    table.add_column("Change", justify="right", min_width=20)

    top = sorted(
        layer_rows,
        key=lambda row: abs(_to_compare_float(row.delta_cycles) or 0.0),
        reverse=True,
    )[:top_layers]
    for row in top:
        op = str(row.candidate_op or "")
        if not row.op_match:
            op = f"{row.baseline_op or '<missing>'} -> {row.candidate_op or '<missing>'}"
        overflow = row.baseline_overflow or row.candidate_overflow
        op_cell = escape(op)
        if overflow:
            op_cell = f"{op_cell} [yellow]OVF[/yellow]"

        row_values = [
            str(row.id or ""),
            op_cell,
            _format_compact_number(row.baseline_cycles),
            _format_compact_number(row.candidate_cycles),
            _format_layer_change_compact(row),
        ]
        table.add_row(*row_values)

    return table


def _build_compare_placement_table(layer_rows: list[LayerDiffRow], *, top_layers: int) -> Table | None:
    changed = [row for row in layer_rows if row.memory_changed]
    if not changed:
        return None

    top_layers = max(0, top_layers)
    rows = sorted(
        changed,
        key=lambda row: abs(_to_compare_float(row.delta_cycles) or 0.0),
        reverse=True,
    )[:top_layers]

    table = Table(
        title=f"[bold]Buffer Placement Changes[/bold] [dim](top {top_layers} by absolute cycle delta)[/dim]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
        expand=True,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Operator", min_width=14, overflow="ellipsis")
    table.add_column("Before", min_width=26, ratio=1, overflow="fold")
    table.add_column("After", min_width=26, ratio=1, overflow="fold")

    for row in rows:
        op = str(row.candidate_op or "")
        if not row.op_match:
            op = f"{row.baseline_op or '<missing>'} -> {row.candidate_op or '<missing>'}"
        table.add_row(
            str(row.id or ""),
            escape(op),
            escape(str(row.baseline_memory or "")),
            f"[yellow]{escape(str(row.candidate_memory or ''))}[/yellow]",
        )

    return table


def _format_compare_headline(metric: MetricDiff, *, lower_is_better: bool) -> str:
    baseline = _format_compare_value(metric.baseline, metric.unit)
    candidate = _format_compare_value(metric.candidate, metric.unit)
    delta = _format_compare_delta(metric, lower_is_better=lower_is_better)
    return f"{baseline} → [bold]{candidate}[/bold]  {delta}"


def _style_compare_value(value: Any, style: str) -> str:
    rendered = _brief_compare_value(value, limit=34)
    escaped = escape(rendered)
    return f"[{style}]{escaped}[/{style}]" if style else escaped


def _brief_compare_value(value: Any, *, limit: int = 56) -> str:
    if value is None:
        return "—"
    text = str(value)
    if len(text) <= limit:
        return text
    keep_left = max(12, limit // 2 - 3)
    keep_right = max(8, limit - keep_left - 5)
    return f"{text[:keep_left]}...{text[-keep_right:]}"


def _friendly_metric_name(name: str) -> str:
    labels = {
        "total_cycles": "Total cycles",
        "device_profiled_infer_avg_us": "Avg profiled inference",
        "device_profiled_infer_total_us": "Total profiled inference",
        "layers": "Layers",
        "binary.text": "Binary .text",
        "binary.data": "Binary .data",
        "binary.bss": "Binary .bss",
        "binary.total": "Binary total",
        "memory.arena_size": "Arena size",
        "memory.allocated_arena": "Allocated arena",
        "memory.model_size": "Model size",
    }
    return labels.get(name, name)


def _format_compare_value(value: Any, unit: str) -> str:
    if value is None:
        return "—"
    number = _to_compare_float(value)
    if number is not None:
        suffix = f" {_short_compare_unit(unit)}" if unit else ""
        return f"{_format_compare_number(number)}{suffix}"
    return escape(str(value))


def _format_compare_value_compact(value: Any, unit: str) -> str:
    if value is None:
        return "—"
    number = _to_compare_float(value)
    if number is not None:
        suffix = f" {_short_compare_unit(unit)}" if unit else ""
        return f"{_format_compact_number(number)}{suffix}"
    return escape(str(value))


def _format_compare_delta(metric: MetricDiff, *, lower_is_better: bool) -> str:
    if metric.delta is None:
        return "—"
    pct = f" ({metric.delta_pct:+.1f}%)" if metric.delta_pct is not None else ""
    body = f"{_format_compare_number(metric.delta)}"
    if metric.unit:
        body += f" {_short_compare_unit(metric.unit)}"
    body += pct

    style = _delta_style(metric.delta, lower_is_better=lower_is_better)
    return f"[{style}]{body}[/{style}]" if style else body


def _format_compare_change_compact(metric: MetricDiff, *, lower_is_better: bool) -> str:
    if metric.delta is None:
        return "—"
    pct = f" ({metric.delta_pct:+.1f}%)" if metric.delta_pct is not None else ""
    body = _format_compact_number(metric.delta)
    if metric.unit:
        body += f" {_short_compare_unit(metric.unit)}"
    body += pct
    style = _delta_style(metric.delta, lower_is_better=lower_is_better)
    return f"[{style}]{body}[/{style}]" if style else body


def _format_layer_change_compact(row: LayerDiffRow) -> str:
    delta = _to_compare_float(row.delta_cycles)
    if delta is None:
        return "—"
    parts = [_format_compact_number(delta)]
    pct = row.delta_pct
    speedup = _to_compare_float(row.speedup)
    extras: list[str] = []
    if isinstance(pct, (int, float)):
        extras.append(f"{pct:+.1f}%")
    if speedup is not None:
        extras.append(f"{speedup:.2f}x")
    if extras:
        parts.append(f"({', '.join(extras)})")
    body = " ".join(parts)
    style = _delta_style(delta, lower_is_better=True)
    return f"[{style}]{body}[/{style}]" if style else body


def _delta_style(delta: float, *, lower_is_better: bool) -> str:
    if abs(delta) < 1e-12:
        return "dim"
    improved = delta < 0 if lower_is_better else delta > 0
    return "green" if improved else "red"


def _format_compare_number(value: Any) -> str:
    number = _to_compare_float(value)
    if number is None:
        return "—"
    if abs(number) >= 1000:
        return f"{number:,.0f}"
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}"


def _format_compact_number(value: Any) -> str:
    number = _to_compare_float(value)
    if number is None:
        return "—"
    sign = "-" if number < 0 else ""
    abs_number = abs(number)
    if abs_number >= 1_000_000:
        return f"{sign}{abs_number / 1_000_000:.2f}M"
    if abs_number >= 10_000:
        return f"{sign}{abs_number / 1_000:.0f}k"
    if abs_number >= 1_000:
        return f"{sign}{abs_number / 1_000:.1f}k"
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}"


def _short_compare_unit(unit: str) -> str:
    return {"cycles": "cyc", "bytes": "B"}.get(unit, unit)


def print_compare(
    console: HpxConsole,
    result: CompareResult,
    *,
    top_layers: int = 10,
    output_paths: list[Path] | None = None,
) -> None:
    """Render a rich comparison between two completed profile runs."""

    console._console.print()
    console._console.print(Rule("[bold]Compare Results[/bold]", style="bright_blue"))
    console._console.print()

    total_cycles = _find_compare_metric(result.metrics, "total_cycles")
    latency_avg = _find_compare_metric(result.metrics, "device_profiled_infer_avg_us")

    overview = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    overview.add_column("key", style="dim", no_wrap=True)
    overview.add_column("value")
    baseline_uri = result.baseline.path.as_uri()
    candidate_uri = result.candidate.path.as_uri()
    overview.add_row(
        "Baseline",
        f"[link={baseline_uri}]{escape(str(result.baseline.path))}[/link]",
    )
    overview.add_row(
        "Candidate",
        f"[link={candidate_uri}]{escape(str(result.candidate.path))}[/link]",
    )
    if total_cycles is not None:
        overview.add_row(
            "Total cycles",
            _format_compare_headline(total_cycles, lower_is_better=True),
        )
    if latency_avg is not None and latency_avg.delta is not None:
        overview.add_row(
            "Avg inference",
            _format_compare_headline(latency_avg, lower_is_better=True),
        )

    console._console.print(overview)
    console._console.print()

    verdict_panel = _build_verdict_panel(result)
    if verdict_panel is not None:
        console._console.print(verdict_panel)
        console._console.print()

    if result.warnings:
        warnings = Text()
        for warning in result.warnings:
            warnings.append("  - ", style="yellow")
            warnings.append(f"{warning}\n")
        console._console.print(
            Panel(
                warnings,
                title="[bold yellow]Warnings[/bold yellow]",
                title_align="left",
                border_style="yellow",
                padding=(0, 2),
                expand=False,
            ),
        )
        console._console.print()

    console._console.print(_build_compare_config_table(result))
    console._console.print()
    console._console.print(_build_compare_run_table(result.metrics))
    console._console.print()
    console._console.print(_build_compare_layer_table(result.layer_rows, top_layers=top_layers))
    console._console.print()
    placement_table = _build_compare_placement_table(result.layer_rows, top_layers=top_layers)
    if placement_table is not None:
        console._console.print(placement_table)
        console._console.print()

    if output_paths:
        output_dir = output_paths[0].parent.resolve()
        files_text = Text()
        for path in output_paths:
            try:
                rel = path.resolve().relative_to(output_dir)
            except ValueError:
                rel = path
            files_text.append(f"  {rel}\n", style="dim")

        console._console.print(
            Panel(
                files_text,
                title=f"[bold]Output → [link={output_dir.as_uri()}]{escape(str(output_dir))}[/link][/bold]",
                title_align="left",
                border_style="bright_blue",
                padding=(0, 2),
                expand=False,
            ),
        )
