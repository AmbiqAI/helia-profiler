"""Rendering of the final results panel (``hpx run`` success output)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich import box
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .tables import _fmt_bytes, _progress_bar, _to_float

if TYPE_CHECKING:
    from ..pipeline import PipelineContext
    from .base import HpxConsole

# Cache counters used in the summary display.
_CACHE_DISPLAY = (
    "ARM_PMU_L1D_CACHE",
    "ARM_PMU_L1D_CACHE_RD",
    "ARM_PMU_L1D_CACHE_REFILL",
    "ARM_PMU_L1D_CACHE_MISS_RD",
    "ARM_PMU_DTCM_ACCESS",
    "ARM_PMU_MEM_ACCESS",
    "ARM_PMU_BUS_ACCESS",
)

_MVE_INST = "ARM_PMU_MVE_INST_RETIRED"
_MVE_INT_MAC = "ARM_PMU_MVE_INT_MAC_RETIRED"
_MVE_LDST = "ARM_PMU_MVE_LDST_RETIRED"
_MVE_STALL = "ARM_PMU_MVE_STALL"
_INST_RETIRED = "ARM_PMU_INST_RETIRED"


def _format_mve_cells(counters: dict[str, float], cycles: float) -> list[str]:
    mve_inst = _to_float(counters.get(_MVE_INST))
    inst = _to_float(counters.get(_INST_RETIRED))
    mve_mac = _to_float(counters.get(_MVE_INT_MAC))
    mve_ldst = _to_float(counters.get(_MVE_LDST))
    mve_stall = _to_float(counters.get(_MVE_STALL))

    mve_pct = f"{mve_inst / inst * 100:.1f}%" if mve_inst is not None and inst else "—"
    mac_density = f"{mve_mac / mve_inst:.2f}" if mve_mac is not None and mve_inst else "—"
    ldst_density = f"{mve_ldst / mve_inst:.2f}" if mve_ldst is not None and mve_inst else "—"
    stall_pct = f"{mve_stall / cycles * 100:.1f}%" if mve_stall is not None and cycles else "—"
    return [mve_pct, mac_density, ldst_density, stall_pct]


def render_memory_plan(console: HpxConsole, plan: Any) -> None:
    """Render the engine-agnostic MemoryPlan as a region usage table.

    Shows each region present on the SoC with its used / capacity
    totals, a progress bar, and a breakdown of consumers when
    non-trivial.  Overflow rows are highlighted in red.
    """
    regions_with_capacity = [r for r in plan.regions if r.capacity > 0]
    if not regions_with_capacity:
        return

    table = Table(
        title=f"[bold]Memory Plan[/bold] [dim]({plan.engine})[/dim]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
    )
    table.add_column("Region", style="dim", min_width=6)
    table.add_column("Used", justify="right", min_width=10)
    table.add_column("Capacity", justify="right", min_width=10)
    table.add_column("", min_width=22)  # bar
    table.add_column("%", justify="right", width=6)
    table.add_column("Consumers", style="dim")

    for r in regions_with_capacity:
        pct = (r.used / r.capacity * 100) if r.capacity else 0.0
        bar = _progress_bar(min(pct, 100.0), width=20)
        consumers = (
            ", ".join(f"{c.name}={_fmt_bytes(c.size)}" for c in r.consumers if c.size) or "—"
        )

        if r.overflow:
            used_cell = f"[bold red]{_fmt_bytes(r.used)}[/bold red]"
            pct_cell = f"[bold red]{pct:.0f}% OVER[/bold red]"
        elif pct >= 90:
            used_cell = f"[yellow]{_fmt_bytes(r.used)}[/yellow]"
            pct_cell = f"[yellow]{pct:.0f}%[/yellow]"
        else:
            used_cell = _fmt_bytes(r.used)
            pct_cell = f"{pct:.0f}%"

        table.add_row(
            r.region,
            used_cell,
            _fmt_bytes(r.capacity),
            bar,
            pct_cell,
            consumers,
        )

    console._console.print(table)
    console._console.print()


def print_results(console: HpxConsole, ctx: PipelineContext) -> None:
    """Render the rich results panel after a successful run."""
    assert ctx.pmu_result is not None
    pmu = ctx.pmu_result
    meta = pmu.meta
    layers = pmu.layers

    console._console.print()

    # ── Header ────────────────────────────────────────────────
    console._console.print(
        Rule("[bold]Results[/bold]", style="bright_blue"),
    )
    console._console.print()

    # ── Overview table ────────────────────────────────────────
    total_cycles = sum(l.cycles or 0 for l in layers)

    overview = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        expand=False,
    )
    overview.add_column("key", style="dim", no_wrap=True)
    overview.add_column("value")

    overview.add_row("Engine", f"[bold]{ctx.config.engine.type.value}[/bold]")

    if ctx.board is not None:
        overview.add_row("Board", ctx.board.name)

    overview.add_row("Layers", str(len(layers)))
    overview.add_row("Total cycles", f"[bold cyan]{total_cycles:,.0f}[/bold cyan]")

    # Clean end-to-end cycles (no per-layer instrumentation), with the
    # delta vs the per-layer sum so the instrumentation overhead is visible.
    clean_cycles = meta.clean_infer_avg_cycles
    if clean_cycles:
        delta_txt = ""
        if total_cycles > 0:
            delta_pct = (clean_cycles - total_cycles) / total_cycles * 100.0
            delta_txt = f"  [dim]({delta_pct:+.1f}% vs per-layer sum)[/dim]"
        overview.add_row(
            "Clean E2E cycles",
            f"[bold green]{clean_cycles:,.0f}[/bold green]{delta_txt}",
        )

    # Model analysis summary
    if ctx.model_analysis is not None:
        ma = ctx.model_analysis
        overview.add_row("Total MACs", f"{ma.total_macs:,}")
        overview.add_row("Total OPS", f"{ma.total_ops:,}")
        if ma.total_macs > 0 and total_cycles > 0:
            cpm = total_cycles / ma.total_macs
            overview.add_row("Cycles/MAC", f"{cpm:.2f}")
        overview.add_row("Parameters", f"{ma.num_parameters:,}")

    if pmu.overflow_detected:
        overview.add_row(
            "⚠ Overflow",
            "[bold yellow]PMU counter overflow detected — some values unreliable[/bold yellow]",
        )

    console._console.print(overview)
    console._console.print()

    # ── Top layers by cycles ──────────────────────────────────
    sorted_layers = sorted(layers, key=lambda l: l.cycles or 0, reverse=True)
    top_n = sorted_layers[:5]

    layer_table = Table(
        title="[bold]Top Layers by Cycles[/bold]",
        box=box.SIMPLE_HEAVY,
        show_edge=False,
        title_justify="left",
        padding=(0, 1),
    )
    layer_table.add_column("#", style="dim", width=3, justify="right")
    layer_table.add_column("Operator", min_width=20)
    layer_table.add_column("Cycles", justify="right", min_width=12)
    layer_table.add_column("%", justify="right", width=7)
    has_macs = ctx.model_analysis is not None
    if has_macs:
        layer_table.add_column("MACs", justify="right", min_width=12)
        layer_table.add_column("Cyc/MAC", justify="right", min_width=8)
    has_mve = any(
        any(k in layer.counters for k in (_MVE_INST, _MVE_INT_MAC, _MVE_LDST, _MVE_STALL))
        for layer in layers
    )
    if has_mve:
        layer_table.add_column("MVE inst\n/ all inst", justify="right", min_width=10)
        layer_table.add_column("MVE MACs\n/ MVE inst", justify="right", min_width=10)
        layer_table.add_column("MVE LD/ST\n/ MVE inst", justify="right", min_width=10)
        layer_table.add_column("MVE stalls\n/ all cycles", justify="right", min_width=10)
    layer_table.add_column("", width=4)  # overflow marker

    # Build a lookup from layer id -> LayerOps
    macs_lookup: dict[int, int] = {}
    if has_macs:
        for la in ctx.model_analysis.layers:  # type: ignore[union-attr]
            macs_lookup[la.id] = la.macs

    for i, layer in enumerate(top_n, 1):
        cyc = layer.cycles or 0
        pct = cyc / total_cycles * 100 if total_cycles else 0

        # Color-coded percentage
        if pct >= 20:
            pct_style = "bold red"
        elif pct >= 10:
            pct_style = "yellow"
        else:
            pct_style = "dim"

        ovf = "[yellow]OVF[/yellow]" if layer.overflow else ""

        row_vals = [
            str(i),
            str(layer.op),
            f"{cyc:,.0f}",
            f"[{pct_style}]{pct:.1f}%[/{pct_style}]",
        ]
        if has_macs:
            lid = int(layer.id) if isinstance(layer.id, (int, float)) else -1
            lm = macs_lookup.get(lid, 0)
            row_vals.append(f"{lm:,}" if lm else "—")
            row_vals.append(f"{cyc / lm:.1f}" if lm and cyc else "—")
        if has_mve:
            row_vals.extend(_format_mve_cells(layer.counters, cyc))
        row_vals.append(ovf)

        layer_table.add_row(*row_vals)

    console._console.print(layer_table)
    console._console.print()

    # ── Memory panel ──────────────────────────────────────────
    mem_parts: list[str] = []
    if meta.allocated_arena and meta.arena_size:
        pct = meta.allocated_arena / meta.arena_size * 100
        bar = _progress_bar(pct, width=20)
        mem_parts.append(
            f"Arena    {meta.allocated_arena:>8,} / {meta.arena_size:,} bytes  {bar}  {pct:.0f}%"
        )
    if meta.model_size:
        mem_parts.append(f"Model    {meta.model_size:>8,} bytes")

    if ctx.binary_sections is not None:
        bs = ctx.binary_sections
        bin_table = Table(
            box=None,
            show_header=True,
            padding=(0, 2),
            expand=False,
        )
        bin_table.add_column("Section", style="dim")
        bin_table.add_column("Size", justify="right")
        bin_table.add_row("text", f"{bs.text:,}")
        bin_table.add_row("data", f"{bs.data:,}")
        bin_table.add_row("bss", f"{bs.bss:,}")
        bin_table.add_row("[bold]total[/bold]", f"[bold]{bs.total:,}[/bold]")

        if mem_parts:
            mem_parts.append("")  # blank line
        mem_parts.append("[bold]Binary Sections[/bold]")

    if mem_parts:
        mem_text = "\n".join(mem_parts)
        console._console.print(
            Panel(
                mem_text,
                title="[bold]Memory[/bold]",
                title_align="left",
                border_style="dim",
                padding=(1, 2),
                expand=False,
            ),
        )

        # Binary table below the panel if present
        if ctx.binary_sections is not None:
            console._console.print(bin_table)  # type: ignore[possibly-undefined]

        console._console.print()

    # ── Memory plan (per-region capacity vs used) ─────────────────
    if ctx.memory_plan is not None and ctx.memory_plan.regions:
        render_memory_plan(console, ctx.memory_plan)

    # ── Cache/memory counters ─────────────────────────────────
    cache_totals: dict[str, float] = {}
    for layer in layers:
        for cname in _CACHE_DISPLAY:
            if cname in layer.counters:
                cache_totals[cname] = cache_totals.get(cname, 0) + layer.counters[cname]

    if cache_totals:
        cache_table = Table(
            title="[bold]Cache & Memory[/bold]",
            box=box.SIMPLE_HEAVY,
            show_edge=False,
            title_justify="left",
            padding=(0, 1),
        )
        cache_table.add_column("Counter", min_width=24)
        cache_table.add_column("Total", justify="right", min_width=14)

        for cname in _CACHE_DISPLAY:
            if cname in cache_totals:
                short = cname.replace("ARM_PMU_", "")
                cache_table.add_row(short, f"{cache_totals[cname]:,.0f}")

        # Derived: L1D hit rate
        l1d_acc = cache_totals.get(
            "ARM_PMU_L1D_CACHE_RD", cache_totals.get("ARM_PMU_L1D_CACHE", 0)
        )
        l1d_miss = cache_totals.get(
            "ARM_PMU_L1D_CACHE_MISS_RD",
            cache_totals.get("ARM_PMU_L1D_CACHE_REFILL", 0),
        )
        if l1d_acc > 0:
            hit_rate = (1 - l1d_miss / l1d_acc) * 100
            style = "green" if hit_rate >= 95 else "yellow" if hit_rate >= 80 else "red"
            cache_table.add_row(
                "[bold]L1D hit rate[/bold]",
                f"[{style}]{hit_rate:.1f}%[/{style}]",
            )

        console._console.print(cache_table)
        console._console.print()

    # ── Power ─────────────────────────────────────────────────
    if ctx.power_result is not None:
        ps = ctx.power_result.summary
        degraded = ctx.power_result.metadata.get("integrity") == "degraded"
        power_table = Table(
            title=(
                "[bold yellow]Power diagnostics (degraded)[/bold yellow]"
                if degraded
                else "[bold]Power[/bold]"
            ),
            box=box.SIMPLE_HEAVY,
            show_edge=False,
            title_justify="left",
            padding=(0, 1),
        )
        power_table.add_column("Metric", min_width=16)
        power_table.add_column("Value", justify="right", min_width=14)

        power_table.add_row("Avg current", f"{ps.avg_current_a * 1000:.3f} mA")
        power_table.add_row("Avg power", f"{ps.avg_power_w * 1000:.3f} mW")
        power_table.add_row("Peak current", f"{ps.peak_current_a * 1000:.3f} mA")
        power_table.add_row(
            "Captured energy" if degraded else "Energy",
            f"{ps.energy_j * 1e6:.3f} µJ",
        )
        if degraded:
            failure = ctx.power_result.metadata.get("gate_failure", {})
            if isinstance(failure, dict) and failure.get("kind"):
                power_table.add_row("Integrity", f"degraded ({failure['kind']})")
        if ctx.power_run is not None and ctx.power_run.terminal is not None:
            terminal = ctx.power_run.terminal
            power_table.add_row(
                "Firmware status",
                f"{terminal.status} ({terminal.completed_count}/{terminal.requested_count})",
            )
            if terminal.elapsed_us is not None:
                power_table.add_row(
                    "Firmware elapsed",
                    f"{terminal.elapsed_us / 1_000_000:.6f} s",
                )
        if ctx.power_run is not None and ctx.power_run.on_device_summary is not None:
            device_power = ctx.power_run.on_device_summary
            power_table.add_row("On-device source", device_power.source)
            power_table.add_row(
                "On-device energy",
                f"{device_power.energy_nj / 1000:.3f} µJ",
            )

        console._console.print(power_table)
        console._console.print()

    # ── Output files ──────────────────────────────────────────
    output_dir = ctx.config.output.dir.resolve()
    elapsed = time.monotonic() - console._run_start

    files_text = Text()
    if ctx.report_paths:
        for p in ctx.report_paths:
            try:
                rel = p.relative_to(output_dir)
            except ValueError:
                rel = p
            files_text.append(f"  {rel}\n", style="dim")

    console._console.print(
        Panel(
            files_text,
            title=f"[bold]Output → [link=file://{output_dir}]{output_dir}[/link][/bold]",
            title_align="left",
            subtitle=f"[dim]{elapsed:.1f}s total[/dim]",
            subtitle_align="right",
            border_style="bright_blue",
            padding=(0, 2),
            expand=False,
        ),
    )
