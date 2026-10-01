"""Implementation of the ``hpx analyze`` command."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..engines import EngineType

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ..modelcost import ModelAnalysis


def _cmd_analyze(
    *,
    model: Path,
    engine: str | None = None,
    compare: bool = False,
    format: str = "table",
    output: Path | None = None,
    board: str = "apollo510_evb",
) -> None:
    from ..console import HpxConsole
    from ..errors import ConfigError, HpxError
    from ..evaluation import analyze_for_engine

    console = HpxConsole(verbosity=1)  # always show output

    try:
        if compare and format == "csv":
            raise ConfigError(
                "--compare is not supported with --format csv.",
                hint="Use --format json to write both graphs, or drop --compare.",
            )
        if engine == EngineType.HELIA_AOT:
            primary = analyze_for_engine(model, engine=EngineType.HELIA_AOT, board=board)
            reference = (
                analyze_for_engine(model, engine=EngineType.TFLM, board=board) if compare else None
            )
            graphs = {"original": reference, "aot_transformed": primary}
        else:
            primary = analyze_for_engine(model, engine=EngineType.TFLM, board=board)
            reference = None
            graphs = {"original": primary}
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    if format == "json":
        _write_json(graphs, output)
    elif format == "csv":
        _write_csv(primary, output)
    else:
        console.print_analysis(primary, model.name, reference)


def _write_csv(analysis: ModelAnalysis, output: Path | None) -> None:
    from ..results.serde import write_dict_csv

    rows = [
        {
            "id": la.id,
            "op": la.op,
            "macs": la.macs,
            "ops": la.ops,
            "input_shapes": str(la.input_shapes),
            "output_shapes": str(la.output_shapes),
            **la.params,
        }
        for la in analysis.layers
    ]
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    dest = output or Path("model_analysis.csv")
    write_dict_csv(dest, fieldnames, rows)
    print(f"Wrote {dest}")


def _write_json(graphs: Mapping[str, ModelAnalysis | None], output: Path | None) -> None:
    import json

    data = {name: graph.to_dict() for name, graph in graphs.items() if graph is not None}
    dest = output or Path("model_analysis.json")
    dest.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {dest}")
