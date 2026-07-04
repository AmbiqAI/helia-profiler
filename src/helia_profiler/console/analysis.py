"""Rendering of standalone ``hpx analyze`` output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich import box
from rich.rule import Rule
from rich.table import Table

if TYPE_CHECKING:
    from .base import HpxConsole


def print_analysis(
    console: HpxConsole,
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

    console._console.print()
    title = f"[bold]Model Analysis — {model_name}[/bold]"
    if engine_label != "tflite":
        title += f"  [dim]({engine_label})[/dim]"
    console._console.print(Rule(title, style="bright_blue"))
    console._console.print()

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

    console._console.print(summary)
    console._console.print()

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

    console._console.print(layer_table)
    console._console.print()

    # ── Reference comparison ──────────────────────────────────
    if reference is not None:
        ref_label = reference.engine if hasattr(reference, "engine") else "tflite"
        console._console.print(
            Rule(
                f"[bold]Comparison — {ref_label} vs {engine_label}[/bold]",
                style="bright_green",
            ),
        )
        console._console.print()

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

        console._console.print(cmp_table)
        console._console.print()

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

        console._console.print(mapped_table)
        console._console.print()
