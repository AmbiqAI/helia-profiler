"""heliaAOT operator manifest persistence (engine-specific report outputs)."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def _write_aot_manifest(ctx: PipelineContext, output_dir: Path) -> Path | None:
    """Persist the heliaAOT operator manifest into the report directory.

    The manifest captures exactly which operators the AOT compiler emitted
    (after fusion / DCE / etc.) together with input/output tensor shapes
    and dtypes — so a post-run consumer can line the CSV layer rows up
    with the actual compiled graph.  Silently no-ops for non-AOT engines
    or when the manifest is empty.
    """
    artifacts = getattr(ctx, "engine_artifacts", None)
    if artifacts is None:
        return None
    manifest = artifacts.aot_op_manifest
    if not manifest:
        return None
    out_path = output_dir / "aot_operator_manifest.json"
    out_path.write_text(
        json.dumps(manifest, indent=2, default=str),
        encoding="utf-8",
        newline="\n",
    )
    log.info("Wrote AOT operator manifest: %s", out_path)
    return out_path


def _write_aot_memory_layers(ctx: PipelineContext, output_dir: Path) -> Path | None:
    """Write a flat per-layer/per-buffer AOT placement CSV.

    ``aot_operator_manifest.json`` is the rich source of truth. This CSV is
    intentionally redundant and spreadsheet-friendly so customers can sort by
    layer, tensor kind, runtime memory, or staged source/destination.
    """

    artifacts = getattr(ctx, "engine_artifacts", None)
    if artifacts is None or not artifacts.aot_op_manifest:
        return None

    rows: list[dict[str, Any]] = []
    for op in artifacts.aot_op_manifest:
        for group, tensor_role in (
            ("inputs", "input"),
            ("outputs", "output"),
            ("local_tensors", "local"),
        ):
            for tensor in op.get(group, []) or []:
                if not isinstance(tensor, dict):
                    continue
                rows.append(
                    {
                        "layer_idx": op.get("idx"),
                        "layer_id": op.get("id"),
                        "op_type": op.get("op_type"),
                        "op_name": op.get("name"),
                        "tensor_role": tensor_role,
                        "tensor_id": tensor.get("id"),
                        "tensor_name": tensor.get("name"),
                        "tensor_kind": tensor.get("kind"),
                        "memory": tensor.get("memory"),
                        "source_memory": tensor.get("source_memory"),
                        "staged": tensor.get("staged"),
                        "arena_role": tensor.get("arena_role"),
                        "arena_region_id": tensor.get("arena_region_id"),
                        "offset": tensor.get("offset"),
                        "size": tensor.get("allocation_size", tensor.get("nbytes", tensor.get("size"))),
                        "shape": json.dumps(tensor.get("shape")) if tensor.get("shape") is not None else "",
                    }
                )

    if not rows:
        return None

    out_path = output_dir / "aot_memory_layers.csv"
    fieldnames = [
        "layer_idx",
        "layer_id",
        "op_type",
        "op_name",
        "tensor_role",
        "tensor_id",
        "tensor_name",
        "tensor_kind",
        "memory",
        "source_memory",
        "staged",
        "arena_role",
        "arena_region_id",
        "offset",
        "size",
        "shape",
    ]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote AOT memory placement CSV: %s", out_path)
    return out_path
