"""Rich console output for heliaPROFILER.

Provides the :class:`HpxConsole` singleton that renders pipeline progress,
stage status, and final results.  Output detail adapts to verbosity level:

- **0** (default): results summary only — clean, copyable tables.
- **1** (``-v``): stage progress with timing + results.
- **2** (``-vv``): full debug logging via standard ``logging``.

All user-facing terminal output flows through this module.  The underlying
``logging`` system is still used for DEBUG-level diagnostics; this module
handles the *presentation* layer that users see.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from .compare import CompareResult, MetricDiff
    from .pipeline import PipelineContext
    from .power.base import PowerResult
    from .results import BinarySections, PmuResult

# Module-level console — reused everywhere.
_console = Console(highlight=False)

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


def _mini_progress_bar(done: int, total: int, width: int = 20) -> str:
    """Return a compact Unicode progress bar like ``[████████░░░░]``."""
    if total <= 0:
        total = 1
    filled = int(width * done / total)
    empty = width - filled
    pct = int(100 * done / total)
    return f"[cyan]{'━' * filled}[/cyan][dim]{'╌' * empty}[/dim] {pct:>3}%"


class HpxConsole:
    """Manages all user-facing terminal output.

    Instantiate once per run via ``HpxConsole(verbosity)``.

    At default verbosity (0), a compact live spinner shows the current
    pipeline stage alongside a progress bar.  At ``-v``, each stage gets
    its own spinner that resolves to a ✓ + elapsed time.
    """

    def __init__(self, verbosity: int = 0) -> None:
        self.verbosity = verbosity
        self._console = _console
        self._stage_start: float | None = None
        self._run_start: float = time.monotonic()
        self._spinner: Any | None = None  # rich.status.Status when active
        self._completed_stages: list[str] = []

    # ------------------------------------------------------------------
    # Banner
    # ------------------------------------------------------------------

    def print_banner(self) -> None:
        """Print the startup banner (verbosity >= 1)."""
        if self.verbosity < 1:
            return
        from ._version import __version__

        self._console.print(
            f"[bold]heliaPROFILER[/bold] [dim]v{__version__}[/dim]",
        )
        self._console.print()

    # ------------------------------------------------------------------
    # Pipeline progress (verbosity >= 1)
    # ------------------------------------------------------------------

    def stage_start(self, name: str) -> None:
        """Called when a pipeline stage begins."""
        self._stage_start = time.monotonic()
        label, icon = _STAGE_LABELS.get(name, (name, "▸"))

        if self.verbosity >= 1:
            # Verbose: one line per stage with a live spinner while running.
            self._stop_spinner()
            self._spinner = self._console.status(
                f"  {icon}  [bold]{label}[/bold]",
                spinner="dots",
                spinner_style="cyan",
            )
            self._spinner.start()
        else:
            # Default: compact live spinner showing current stage + progress bar.
            done = len(self._completed_stages)
            total = 11  # total pipeline stages
            bar = _mini_progress_bar(done, total)
            status_text = f"{bar}  {icon}  [bold]{label}[/bold] [dim]({done}/{total})[/dim]"
            if self._spinner is None:
                self._spinner = self._console.status(
                    status_text,
                    spinner="dots",
                    spinner_style="cyan",
                )
                self._spinner.start()
            else:
                self._spinner.update(status_text)

    def stage_done(self, name: str) -> None:
        """Called when a pipeline stage completes."""
        elapsed = time.monotonic() - (self._stage_start or time.monotonic())
        label, icon = _STAGE_LABELS.get(name, (name, "▸"))
        self._completed_stages.append(name)

        if self.verbosity >= 1:
            self._stop_spinner()
            self._console.print(
                f"  {icon}  [bold]{label}[/bold] [green]✓[/green] [dim]{elapsed:.1f}s[/dim]"
            )

    def stage_skip(self, name: str) -> None:
        """Called when a pipeline stage is skipped."""
        self._completed_stages.append(name)
        if self.verbosity < 1:
            return
        self._stop_spinner()
        label, icon = _STAGE_LABELS.get(name, (name, "▸"))
        self._console.print(f"  {icon}  [dim]{label} — skipped[/dim]")

    def pipeline_done(self) -> None:
        """Called after all stages complete — clean up any live spinner."""
        self._stop_spinner()
        if self.verbosity < 1:
            done = len(self._completed_stages)
            bar = _mini_progress_bar(done, done or 11)
            self._console.print(f"  {bar}  [green]Done[/green]")

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    # ------------------------------------------------------------------
    # Final results display (always shown)
    # ------------------------------------------------------------------

    def print_results(self, ctx: PipelineContext) -> None:
        """Render the rich results panel after a successful run."""
        assert ctx.pmu_result is not None
        pmu = ctx.pmu_result
        meta = pmu.meta
        layers = pmu.layers

        self._console.print()

        # ── Header ────────────────────────────────────────────────
        self._console.print(
            Rule("[bold]Results[/bold]", style="bright_blue"),
        )
        self._console.print()

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

        self._console.print(overview)
        self._console.print()

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
            row_vals.append(ovf)

            layer_table.add_row(*row_vals)

        self._console.print(layer_table)
        self._console.print()

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
            self._console.print(
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
                self._console.print(bin_table)  # type: ignore[possibly-undefined]

            self._console.print()

        # ── Memory plan (per-region capacity vs used) ─────────────────
        if ctx.memory_plan is not None and ctx.memory_plan.regions:
            self._render_memory_plan(ctx.memory_plan)

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

            self._console.print(cache_table)
            self._console.print()

        # ── Power ─────────────────────────────────────────────────
        if ctx.power_result is not None:
            ps = ctx.power_result.summary
            power_table = Table(
                title="[bold]Power[/bold]",
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
            power_table.add_row("Energy", f"{ps.energy_j * 1e6:.3f} µJ")

            self._console.print(power_table)
            self._console.print()

        # ── Output files ──────────────────────────────────────────
        output_dir = ctx.config.output.dir.resolve()
        elapsed = time.monotonic() - self._run_start

        files_text = Text()
        if ctx.report_paths:
            for p in ctx.report_paths:
                try:
                    rel = p.relative_to(output_dir)
                except ValueError:
                    rel = p
                files_text.append(f"  {rel}\n", style="dim")

        self._console.print(
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

    # ------------------------------------------------------------------
    # Compare display
    # ------------------------------------------------------------------

    def print_compare(
        self,
        result: CompareResult,
        *,
        top_layers: int = 10,
        output_paths: list[Path] | None = None,
    ) -> None:
        """Render a rich comparison between two completed profile runs."""

        self._console.print()
        self._console.print(Rule("[bold]Compare Results[/bold]", style="bright_blue"))
        self._console.print()

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

        self._console.print(overview)
        self._console.print()

        if result.warnings:
            warnings = Text()
            for warning in result.warnings:
                warnings.append("  - ", style="yellow")
                warnings.append(f"{warning}\n")
            self._console.print(
                Panel(
                    warnings,
                    title="[bold yellow]Warnings[/bold yellow]",
                    title_align="left",
                    border_style="yellow",
                    padding=(0, 2),
                    expand=False,
                ),
            )
            self._console.print()

        self._console.print(_build_compare_config_table(result))
        self._console.print()
        self._console.print(_build_compare_run_table(result.metrics))
        self._console.print()
        self._console.print(_build_compare_layer_table(result.layer_rows, top_layers=top_layers))
        self._console.print()

        if output_paths:
            output_dir = output_paths[0].parent.resolve()
            files_text = Text()
            for path in output_paths:
                try:
                    rel = path.resolve().relative_to(output_dir)
                except ValueError:
                    rel = path
                files_text.append(f"  {rel}\n", style="dim")

            self._console.print(
                Panel(
                    files_text,
                    title=f"[bold]Output → [link={output_dir.as_uri()}]{escape(str(output_dir))}[/link][/bold]",
                    title_align="left",
                    border_style="bright_blue",
                    padding=(0, 2),
                    expand=False,
                ),
            )

    # ------------------------------------------------------------------
    # Memory plan rendering
    # ------------------------------------------------------------------

    def _render_memory_plan(self, plan: Any) -> None:
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

        self._console.print(table)
        self._console.print()

    # ------------------------------------------------------------------
    # Standalone model analysis display
    # ------------------------------------------------------------------

    def print_analysis(
        self,
        primary: Any,
        model_name: str,
        reference: Any | None = None,
    ) -> None:
        """Render standalone ``hpx analyze`` results.

        *primary* is the engine-specific analysis (what the engine actually
        executes).  *reference* is the original tflite analysis shown when
        ``--compare`` is used.
        """
        engine_label = primary.engine if hasattr(primary, "engine") else "tflite"

        self._console.print()
        title = f"[bold]Model Analysis — {model_name}[/bold]"
        if engine_label != "tflite":
            title += f"  [dim]({engine_label})[/dim]"
        self._console.print(Rule(title, style="bright_blue"))
        self._console.print()

        # ── Summary ───────────────────────────────────────────────
        summary = Table(show_header=False, box=None, padding=(0, 2), expand=False)
        summary.add_column("key", style="dim", no_wrap=True)
        summary.add_column("value")

        if engine_label != "tflite":
            summary.add_row("Engine", f"[bold]{engine_label}[/bold]")
        summary.add_row("Layers", str(len(primary.layers)))
        summary.add_row("Total MACs", f"[bold cyan]{primary.total_macs:,}[/bold cyan]")
        summary.add_row("Total OPS", f"{primary.total_ops:,}")
        summary.add_row("Parameters", f"{primary.num_parameters:,}")

        self._console.print(summary)
        self._console.print()

        # ── Per-layer breakdown ───────────────────────────────────
        layer_table = Table(
            title="[bold]Per-Layer Breakdown[/bold]",
            box=box.SIMPLE_HEAVY,
            show_edge=False,
            title_justify="left",
            padding=(0, 1),
        )
        layer_table.add_column("#", style="dim", width=4, justify="right")
        if engine_label != "tflite":
            layer_table.add_column("Src", style="dim", width=4, justify="right")
        layer_table.add_column("Operator", min_width=22)
        layer_table.add_column("MACs", justify="right", min_width=14)
        layer_table.add_column("OPS", justify="right", min_width=14)
        layer_table.add_column("% MACs", justify="right", width=8)
        layer_table.add_column("Output Shape", min_width=20)

        for la in primary.layers:
            pct = la.macs / primary.total_macs * 100 if primary.total_macs else 0
            if pct >= 20:
                pct_style = "bold red"
            elif pct >= 10:
                pct_style = "yellow"
            elif pct > 0:
                pct_style = ""
            else:
                pct_style = "dim"

            out_shape = str(la.output_shapes[0]) if la.output_shapes else "—"
            pct_str = f"{pct:.1f}%" if pct > 0 else "—"
            macs_str = f"{la.macs:,}" if la.macs else "—"
            ops_str = f"{la.ops:,}" if la.ops else "—"

            row_vals = [str(la.id)]
            if engine_label != "tflite":
                oid = (
                    la.original_id
                    if hasattr(la, "original_id") and la.original_id is not None
                    else "—"
                )
                row_vals.append(str(oid))
            row_vals.extend(
                [
                    la.op,
                    macs_str,
                    ops_str,
                    f"[{pct_style}]{pct_str}[/{pct_style}]" if pct_style else pct_str,
                    out_shape,
                ]
            )
            layer_table.add_row(*row_vals)

        self._console.print(layer_table)
        self._console.print()

        # ── Reference comparison ──────────────────────────────────
        if reference is not None:
            ref_label = reference.engine if hasattr(reference, "engine") else "tflite"
            self._console.print(
                Rule(
                    f"[bold]Comparison — {ref_label} vs {engine_label}[/bold]",
                    style="bright_green",
                ),
            )
            self._console.print()

            cmp_table = Table(
                show_header=True,
                box=box.SIMPLE_HEAVY,
                show_edge=False,
                padding=(0, 1),
            )
            cmp_table.add_column("Metric", min_width=16)
            cmp_table.add_column(ref_label, justify="right", min_width=14)
            cmp_table.add_column(engine_label, justify="right", min_width=14)
            cmp_table.add_column("Δ", justify="right", min_width=10)

            def _delta(orig: int, eng: int) -> str:
                if orig == 0:
                    return "—"
                d = (eng - orig) / orig * 100
                if d < -1:
                    return f"[green]{d:+.1f}%[/green]"
                elif d > 1:
                    return f"[red]{d:+.1f}%[/red]"
                return f"{d:+.1f}%"

            cmp_table.add_row(
                "Layers",
                str(len(reference.layers)),
                str(len(primary.layers)),
                _delta(len(reference.layers), len(primary.layers)),
            )
            cmp_table.add_row(
                "Total MACs",
                f"{reference.total_macs:,}",
                f"{primary.total_macs:,}",
                _delta(reference.total_macs, primary.total_macs),
            )
            cmp_table.add_row(
                "Total OPS",
                f"{reference.total_ops:,}",
                f"{primary.total_ops:,}",
                _delta(reference.total_ops, primary.total_ops),
            )
            cmp_table.add_row(
                "Parameters",
                f"{reference.num_parameters:,}",
                f"{primary.num_parameters:,}",
                _delta(reference.num_parameters, primary.num_parameters),
            )

            self._console.print(cmp_table)
            self._console.print()

            # Per-layer mapped comparison using original_id
            ref_by_id: dict[int, Any] = {la.id: la for la in reference.layers}

            mapped_table = Table(
                title=f"[bold]Per-Layer Mapping — {ref_label} → {engine_label}[/bold]",
                box=box.SIMPLE_HEAVY,
                show_edge=False,
                title_justify="left",
                padding=(0, 1),
            )
            mapped_table.add_column("#", style="dim", width=4, justify="right")
            mapped_table.add_column("Src", style="dim", width=4, justify="right")
            mapped_table.add_column("Operator", min_width=18)
            mapped_table.add_column(f"MACs ({ref_label})", justify="right", min_width=12)
            mapped_table.add_column(f"MACs ({engine_label})", justify="right", min_width=12)
            mapped_table.add_column("Δ", justify="right", width=8)

            for la in primary.layers:
                oid = (
                    la.original_id
                    if hasattr(la, "original_id") and la.original_id is not None
                    else None
                )
                ref_layer = ref_by_id.get(oid) if oid is not None else None

                ref_macs_str = f"{ref_layer.macs:,}" if ref_layer and ref_layer.macs else "—"
                eng_macs_str = f"{la.macs:,}" if la.macs else "—"
                delta_str = "—"
                if ref_layer and ref_layer.macs and la.macs:
                    delta_str = _delta(ref_layer.macs, la.macs)
                elif ref_layer is None:
                    delta_str = "[cyan]new[/cyan]"

                mapped_table.add_row(
                    str(la.id),
                    str(oid) if oid is not None else "—",
                    la.op,
                    ref_macs_str,
                    eng_macs_str,
                    delta_str,
                )

            # Also show reference layers that were removed (not in engine graph)
            engine_orig_ids = {
                la.original_id
                for la in primary.layers
                if hasattr(la, "original_id") and la.original_id is not None
            }
            for rl in reference.layers:
                if rl.id not in engine_orig_ids:
                    mapped_table.add_row(
                        "—",
                        str(rl.id),
                        f"[dim strikethrough]{rl.op}[/dim strikethrough]",
                        f"{rl.macs:,}" if rl.macs else "—",
                        "—",
                        "[green]fused[/green]" if rl.macs == 0 else "[green]removed[/green]",
                    )

            self._console.print(mapped_table)
            self._console.print()

    # ------------------------------------------------------------------
    # Error display
    # ------------------------------------------------------------------

    def print_error(self, exc: Exception) -> None:
        """Render a user-facing error."""
        from .errors import HpxError

        if isinstance(exc, HpxError):
            msg = Text()
            msg.append("Error: ", style="bold red")
            msg.append(str(exc))
            if exc.hint:
                msg.append(f"\n  hint: {exc.hint}", style="dim")
            self._console.print(msg, highlight=False)
        else:
            self._console.print(f"[bold red]Error:[/bold red] {exc}")

    def print_interrupted(self) -> None:
        """Print a clean one-liner on Ctrl-C."""
        self._stop_spinner()
        self._console.print("\n[dim]Interrupted.[/dim]")

    # ------------------------------------------------------------------
    # Doctor
    # ------------------------------------------------------------------

    def print_doctor(
        self,
        checks: list[tuple[str, str, str | None]],
        required_python: list[tuple[str, str, bool]],
        optional: list[tuple[str, str, bool]],
    ) -> None:
        """Render ``hpx doctor`` results.

        *checks*: list of ``(label, binary_name, path_or_none)``
        *required_python*: list of ``(label, package_name, available)``
        *optional*: list of ``(label, package_name, available)``
        """
        self._console.print()
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

        self._console.print(table)
        self._console.print()

        if all_ok:
            self._console.print("  [green]All required tools found.[/green]")
        else:
            self._console.print(
                "  [yellow]Some required tools are missing. Install them before profiling.[/yellow]"
            )
        self._console.print()

    # ------------------------------------------------------------------
    # Boards & Engines
    # ------------------------------------------------------------------

    def print_boards(self, boards: list[tuple[str, str, str, str, str, str]]) -> None:
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

        self._console.print(table)

    def print_engines(self, engines: list[str]) -> None:
        """Render the engine list."""
        for engine in engines:
            self._console.print(f"  [bold]{engine}[/bold]")


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


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
        changed = row.get("status") == "diff"
        status = "[yellow]diff[/yellow]" if changed else "[green]same[/green]"
        value_style = "yellow" if changed else ""
        table.add_row(
            str(row.get("field", "")),
            _style_compare_value(row.get("baseline"), value_style),
            _style_compare_value(row.get("candidate"), value_style),
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


def _build_compare_layer_table(layer_rows: list[dict[str, Any]], *, top_layers: int) -> Table:
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
        key=lambda row: abs(_to_compare_float(row.get("delta_cycles")) or 0.0),
        reverse=True,
    )[:top_layers]
    for row in top:
        op = str(row.get("candidate_op", ""))
        if not row.get("op_match", True):
            op = f"{row.get('baseline_op', '<missing>')} -> {row.get('candidate_op', '<missing>')}"
        overflow = row.get("baseline_overflow") or row.get("candidate_overflow")
        op_cell = escape(op)
        if overflow:
            op_cell = f"{op_cell} [yellow]OVF[/yellow]"

        table.add_row(
            str(row.get("id", "")),
            op_cell,
            _format_compact_number(row.get("baseline_cycles")),
            _format_compact_number(row.get("candidate_cycles")),
            _format_layer_change_compact(row),
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


def _format_layer_change_compact(row: dict[str, Any]) -> str:
    delta = _to_compare_float(row.get("delta_cycles"))
    if delta is None:
        return "—"
    parts = [_format_compact_number(delta)]
    pct = row.get("delta_pct")
    speedup = _to_compare_float(row.get("speedup"))
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


def _to_compare_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
