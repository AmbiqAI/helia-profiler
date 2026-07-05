"""Implementation of the ``hpx analyze`` command."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..engines import EngineType

if TYPE_CHECKING:
    from ..model_analysis import ModelAnalysis


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze model compute/parameter breakdown without hardware."""
    from ..model_analysis import (
        ModelAnalysis,
        analyze_air_model,
        analyze_model,
        is_aot_available,
        is_available,
    )
    from ..console import HpxConsole

    console = HpxConsole(verbosity=1)  # always show output

    if not args.model.exists():
        print(f"Error: model file not found: {args.model}", file=sys.stderr)
        sys.exit(1)

    if not is_available():
        print(
            "Error: ai-edge-litert is not installed.\n"
            "  Install with: pip install 'helia-profiler[analysis]'",
            file=sys.stderr,
        )
        sys.exit(1)

    engine = args.engine  # None, "helia-aot", or "helia-rt"
    is_aot = engine == EngineType.HELIA_AOT.value

    # --- Original tflite analysis (always needed as baseline) ---
    original = analyze_model(str(args.model))
    if original is None:
        print("Error: failed to analyze model.", file=sys.stderr)
        sys.exit(1)

    # --- Engine-specific analysis ---
    engine_result: ModelAnalysis | None = None
    if is_aot:
        if not is_aot_available():
            print(
                "Error: helia-aot is not installed.\n"
                "  Install with: pip install 'helia-profiler[aot]'",
                file=sys.stderr,
            )
            sys.exit(1)
        engine_result = _run_aot_analysis(args.model, args.board)

    # Determine which analysis is "primary" (what the engine actually runs)
    # and whether to show comparison
    if engine_result is not None:
        primary = engine_result
        reference = original if args.compare else None
    else:
        primary = original
        reference = None

    # --- Output ---
    if args.format == "table":
        console.print_analysis(primary, args.model.name, reference)
    elif args.format in ("csv", "json"):
        _write_analysis_file(primary, args.format, args.output, reference)
    else:
        console.print_analysis(primary, args.model.name, reference)


def _run_aot_analysis(model_path: Path, board: str) -> "ModelAnalysis | None":
    """Run heliaAOT compilation and return analysis of the transformed graph."""
    import tempfile

    from ..model_analysis import analyze_air_model

    try:
        from helia_aot.converter import AotConverter  # type: ignore[import-untyped]
        from helia_aot.cli.defines import ConvertArgs  # type: ignore[import-untyped]
        from helia_aot.defines import ModuleType  # type: ignore[import-untyped]
    except ImportError:
        print("Error: helia-aot import failed.", file=sys.stderr)
        return None

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        convert_args = ConvertArgs(
            model={"path": str(model_path)},
            module={"path": tmp, "type": ModuleType.nsx.value},
            platform={"name": board},
        )
        try:
            ctx = AotConverter(config=convert_args).convert()
        except Exception as exc:
            print(f"Warning: AOT compilation failed: {exc}", file=sys.stderr)
            return None

        return analyze_air_model(ctx.model)


def _write_analysis_file(
    analysis: "ModelAnalysis",
    fmt: str,
    output: Path | None,
    aot: "ModelAnalysis | None" = None,
) -> None:
    """Write analysis results to CSV or JSON."""
    import csv
    import json

    if fmt == "csv":
        rows = []
        for la in analysis.layers:
            row = {
                "id": la.id,
                "op": la.op,
                "macs": la.macs,
                "ops": la.ops,
                "input_shapes": str(la.input_shapes),
                "output_shapes": str(la.output_shapes),
            }
            row.update(la.params)
            rows.append(row)

        if output:
            dest = output
        else:
            dest = Path("model_analysis.csv")

        fieldnames = list(rows[0].keys()) if rows else []
        with open(dest, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        print(f"Wrote {dest}")

    elif fmt == "json":
        data: dict = {
            "original": {
                "total_macs": analysis.total_macs,
                "total_ops": analysis.total_ops,
                "num_parameters": analysis.num_parameters,
                "layers": [
                    {
                        "id": la.id,
                        "op": la.op,
                        "macs": la.macs,
                        "ops": la.ops,
                        "input_shapes": la.input_shapes,
                        "output_shapes": la.output_shapes,
                        "params": la.params,
                    }
                    for la in analysis.layers
                ],
            }
        }
        if aot is not None:
            data["aot_transformed"] = {
                "total_macs": aot.total_macs,
                "total_ops": aot.total_ops,
                "num_parameters": aot.num_parameters,
                "layers": [
                    {
                        "id": la.id,
                        "op": la.op,
                        "macs": la.macs,
                        "ops": la.ops,
                        "input_shapes": la.input_shapes,
                        "output_shapes": la.output_shapes,
                        "params": la.params,
                    }
                    for la in aot.layers
                ],
            }

        dest = output or Path("model_analysis.json")
        dest.write_text(json.dumps(data, indent=2, default=str))
        print(f"Wrote {dest}")
