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

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.rule import Rule
from rich import box

if TYPE_CHECKING:
    from .pipeline import PipelineContext
    from .power.base import PowerResult
    from .results import BinarySections, PmuResult

# Module-level console — reused everywhere.
_console = Console(highlight=False)

# Map stage names to friendlier labels + icons.
_STAGE_LABELS: dict[str, tuple[str, str]] = {
    "resolve_platform": ("Resolve platform", "🔍"),
    "prepare_engine": ("Prepare engine", "⚙️"),
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


class HpxConsole:
    """Manages all user-facing terminal output.

    Instantiate once per run via ``HpxConsole(verbosity)``.
    """

    def __init__(self, verbosity: int = 0) -> None:
        self.verbosity = verbosity
        self._console = _console
        self._stage_start: float | None = None
        self._run_start: float = time.monotonic()

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
        if self.verbosity < 1:
            return
        label, icon = _STAGE_LABELS.get(name, (name, "▸"))
        self._console.print(f"  {icon}  [bold]{label}[/bold] [dim]…[/dim]", end="")

    def stage_done(self, name: str) -> None:
        """Called when a pipeline stage completes."""
        if self.verbosity < 1:
            return
        elapsed = time.monotonic() - (self._stage_start or time.monotonic())
        self._console.print(f" [green]✓[/green] [dim]{elapsed:.1f}s[/dim]")

    def stage_skip(self, name: str) -> None:
        """Called when a pipeline stage is skipped."""
        if self.verbosity < 1:
            return
        label, icon = _STAGE_LABELS.get(name, (name, "▸"))
        self._console.print(f"  {icon}  [dim]{label} — skipped[/dim]")

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
        overview.add_row("Total cycles", f"[bold cyan]{total_cycles:,}[/bold cyan]")

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
        layer_table.add_column("", width=4)  # overflow marker

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
            layer_table.add_row(
                str(i),
                str(layer.op),
                f"{cyc:,}",
                f"[{pct_style}]{pct:.1f}%[/{pct_style}]",
                ovf,
            )

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
                box=None, show_header=True, padding=(0, 2), expand=False,
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

    # ------------------------------------------------------------------
    # Doctor
    # ------------------------------------------------------------------

    def print_doctor(
        self,
        checks: list[tuple[str, str, str | None]],
        optional: list[tuple[str, str, bool]],
    ) -> None:
        """Render ``hpx doctor`` results.

        *checks*: list of ``(label, binary_name, path_or_none)``
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

        Each tuple: ``(board, soc, core, pmu, mve, channel)``
        """
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_edge=False,
            padding=(0, 1),
        )
        table.add_column("Board", min_width=22)
        table.add_column("SoC", min_width=12)
        table.add_column("Core", min_width=12)
        table.add_column("PMU", width=5, justify="center")
        table.add_column("MVE", width=5, justify="center")
        table.add_column("Channel")

        for brd, soc, core, pmu, mve, channel in boards:
            pmu_fmt = "[green]full[/green]" if pmu == "full" else "[dim]dwt[/dim]"
            mve_fmt = "[green]yes[/green]" if mve == "yes" else "[dim]no[/dim]"
            table.add_row(brd, soc, core, pmu_fmt, mve_fmt, channel)

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
