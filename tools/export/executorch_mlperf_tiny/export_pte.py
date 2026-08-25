#!/usr/bin/env python3
"""Lower the INT8 MLPerf Tiny .pt2 fixtures to the Cortex-M .pte fixtures.

This stage neither defines a network nor quantizes one: the INT8
PT2E-quantized ExportedProgram comes from the ``.pt2`` fixture written by
``make_pt2.py`` (or any drop-in replacement with the same contract, e.g. one
carrying trained weights). It refuses unquantized programs. Lowering goes
through the helia-torch AOT package (``nsx_cortex_m.export``), whose
pre-quantized path skips straight to kernel matching:

  torch.export.load(.pt2) -> nsx_cortex_m.export(kernel_provider="arm")
    -> .pte with cortex_m::/CMSIS-NN operators, INT8 method I/O
       (int8_io=True, matching the int8 .tflite references), no delegates

(the ``arm`` provider reproduces the stock ExecuTorch Cortex-M flow the
qualified fixtures were built with; a float model handed to the same entry
point would be quantized there instead). I/O shapes are read from the
ExportedProgram, not hard-coded.

Run inside the pinned export environment:

  ~/.cache/nsx-executorch-export-venv/bin/python export_pte.py --all \
      --executorch-root ~/nsx-executorch-package/external/executorch
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path

from common import (
    FIXTURE_ROOT,
    MODELS,
    ModelSpec,
    check_pins,
    configure_import_path,
    configure_nsx_import_path,
    git_commit,
    load_quantized_pt2,
    parse_model_keys,
    quantization_ops,
    use_checkout_schema_resources,
)

DEFAULT_EXECUTORCH_ROOT = Path(
    os.environ.get(
        "EXECUTORCH_ROOT",
        Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
    )
)


def lower_to_pte(quantized_exported, example_inputs):
    """Lower one INT8 ExportedProgram via helia-torch (arm provider).

    int8_io=True drops the float32 method boundary so the serialized method
    is int8-in/int8-out, matching the INT8 TFLite reference fixtures.
    """
    from nsx_cortex_m import export as nsx_export

    return nsx_export(
        quantized_exported, example_inputs, kernel_provider="arm", int8_io=True
    )


def _operator_name(operator) -> str:
    overload = getattr(operator, "overload", "")
    return f"{operator.name}.{overload}" if overload else operator.name


def inspect_pte(pte_path: Path) -> dict:
    from executorch.exir._serialize import _deserialize_pte_binary
    from executorch.exir.schema import KernelCall

    program = _deserialize_pte_binary(pte_path.read_bytes()).program
    if len(program.execution_plan) != 1:
        raise SystemExit("Expected exactly one execution plan")
    plan = program.execution_plan[0]
    if len(plan.inputs) != 1 or len(plan.outputs) != 1:
        raise SystemExit("Expected one tensor input and one tensor output")
    operators = [_operator_name(operator) for operator in plan.operators]
    counts = collections.Counter()
    for chain in plan.chains:
        for instruction in chain.instructions:
            if isinstance(instruction.instr_args, KernelCall):
                counts[operators[instruction.instr_args.op_index]] += 1
    buffers = list(plan.non_const_buffer_sizes)
    if len(buffers) != 2:
        raise SystemExit(
            "nsx-executorch requires exactly one planned buffer; "
            f"PTE contains {len(buffers) - 1}: {buffers}"
        )
    input_tensor = plan.values[plan.inputs[0]].val
    output_tensor = plan.values[plan.outputs[0]].val

    # ExecuTorch ScalarType names; CHAR is int8.
    element_bytes = {"CHAR": 1, "FLOAT": 4}

    def _tensor_facts(tensor):
        dtype = tensor.scalar_type.name
        if dtype not in element_bytes:
            raise SystemExit(
                f"unsupported method I/O ScalarType {dtype}; expected one of "
                f"{sorted(element_bytes)}"
            )
        numel = 1
        for dim in tensor.sizes:
            numel *= dim
        return dtype, numel * element_bytes[dtype]

    input_dtype, input_bytes = _tensor_facts(input_tensor)
    output_dtype, output_bytes = _tensor_facts(output_tensor)
    return {
        "method": plan.name,
        "operators": operators,
        "invocations": counts,
        "delegates": [delegate.id for delegate in plan.delegates],
        "non_const_buffer_sizes": buffers,
        "planned_arena_size": sum(buffers[1:]),
        "input_shape": list(input_tensor.sizes),
        "input_dim_order": list(input_tensor.dim_order),
        "input_dtype": input_dtype,
        "input_bytes": input_bytes,
        "output_shape": list(output_tensor.sizes),
        "output_dim_order": list(output_tensor.dim_order),
        "output_dtype": output_dtype,
        "output_bytes": output_bytes,
    }


def _qparams_json(quant_args) -> dict:
    scale, zero_point, qmin, qmax, dtype = quant_args
    return {
        "scale": float(scale),
        "zero_point": int(zero_point),
        "quant_min": int(qmin),
        "quant_max": int(qmax),
        "dtype": str(dtype).replace("torch.", ""),
    }


def export_one(
    torch, spec: ModelSpec, fixture_root: Path, executorch_root: Path, commit: str
) -> dict:
    pt2_path = fixture_root / spec.pt2_path
    pte_path = fixture_root / spec.pte_path
    torch.use_deterministic_algorithms(True)

    quantized_exported = load_quantized_pt2(torch, pt2_path)
    example = quantized_exported.example_inputs[0][0]
    input_shape = list(example.shape)
    with torch.no_grad():
        quantized_output = quantized_exported.module()(example)
    output_shape = list(quantized_output.shape)

    result = lower_to_pte(quantized_exported, (example,))

    pte_path.parent.mkdir(parents=True, exist_ok=True)
    pte_path.write_bytes(bytes(result.executorch_program.buffer))

    inspection = inspect_pte(pte_path)
    if inspection["delegates"]:
        raise SystemExit(f"{spec.key}: Cortex-M operator export unexpectedly contains delegates")
    if inspection["input_shape"] != input_shape or inspection["output_shape"] != output_shape:
        raise SystemExit(
            f"{spec.key}: serialized I/O contract "
            f"({inspection['input_shape']} -> {inspection['output_shape']}) does not match "
            f"the .pt2 ({input_shape} -> {output_shape})"
        )

    if inspection["input_dtype"] != "CHAR" or inspection["output_dtype"] != "CHAR":
        raise SystemExit(
            f"{spec.key}: expected int8 (CHAR) method I/O, got "
            f"{inspection['input_dtype']} -> {inspection['output_dtype']}"
        )

    import torchao

    pte_bytes = pte_path.read_bytes()
    input_size = inspection["input_bytes"]
    output_size = inspection["output_bytes"]
    cortex_m_ops = sorted(n for n in inspection["operators"] if n.startswith("cortex_m::"))
    portable_ops = sorted(
        n
        for n in inspection["operators"]
        if not n.startswith("cortex_m::") and not n.startswith("executorch_prim::")
    )
    planned_arena = inspection["planned_arena_size"]

    metadata = {
        "model_id": spec.model_id,
        "description": spec.description,
        "source": {
            "pt2": spec.pt2_path,
            "pt2_sha256": hashlib.sha256(pt2_path.read_bytes()).hexdigest(),
            "pt2_quantize_dequantize_ops": quantization_ops(quantized_exported),
            "note": (
                "INT8 quantized ExportedProgram loaded from the .pt2 fixture; "
                "this exporter neither defines nor quantizes a model."
            ),
        },
        "input": {
            "shape": input_shape,
            "dtype": "int8",
            "dim_order": inspection["input_dim_order"],
            "memory_format": "channels_last" if spec.channels_last else "contiguous",
            "bytes": input_size,
            "quantization": _qparams_json(result.io_qparams["inputs"][0]),
        },
        "output": {
            "shape": output_shape,
            "dtype": "int8",
            "dim_order": inspection["output_dim_order"],
            "semantics": spec.output_semantics,
            "bytes": output_size,
            "quantization": _qparams_json(result.io_qparams["outputs"][0]),
        },
        "pte": {
            "file": pte_path.name,
            "bytes": len(pte_bytes),
            "sha256": hashlib.sha256(pte_bytes).hexdigest(),
            "method": inspection["method"],
            "format": "ExecuTorch PTE flatbuffer",
        },
        "lowering": {
            "pipeline": "helia-torch nsx_cortex_m.export, pre-quantized path",
            "kernel_provider": result.kernel_provider,
            "target": "cortex-m55 (helia-torch default target config)",
            "quantization": "inherited from the INT8 .pt2; int8 method I/O (int8_io=True)",
            "delegated": False,
            "portable_fallback_ops": result.portable_fallback_ops,
        },
        "operators": {
            "serialized": [
                {"name": name, "invocations": inspection["invocations"][name]}
                for name in inspection["operators"]
            ],
            "cortex_m_cmsis_nn": cortex_m_ops,
            "portable_required_by_helia_config": portable_ops,
            "delegated": [],
        },
        "arena_sizes": {
            "planned_arena_size": {
                "bytes": planned_arena,
                "source": "PTE execution_plan[0].non_const_buffer_sizes",
                "buffers": inspection["non_const_buffer_sizes"],
            },
            "method_arena_size": {
                "bytes": 65536,
                "source": "conservative helia-profiler ExecuTorch config value",
            },
            "temporary_arena_size": {
                "bytes": 32768,
                "source": "conservative helia-profiler ExecuTorch config value",
            },
        },
        "helia_profiler_config": {
            "model.arena_size": planned_arena,
            "engine.config.planned_arena_size": planned_arena,
            "engine.config.method_arena_size": 65536,
            "engine.config.temporary_arena_size": 32768,
            "engine.config.input_size": input_size,
            "engine.config.output_size": output_size,
            "engine.config.portable_ops": portable_ops,
        },
        "toolchain": {
            "executorch_version": (executorch_root / "version.txt").read_text().strip(),
            "executorch_git_revision": commit,
            "nsx_executorch_git_revision": git_commit(executorch_root.parents[1]),
            "torch_version": torch.__version__,
            "torchao_version": torchao.__version__,
        },
    }
    print(
        f"[{spec.key}] {pte_path.name}: {len(pte_bytes)} bytes, "
        f"planned_arena={planned_arena}, portable={portable_ops}"
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=None, help="Comma-separated subset (ad,ic,kws,vww).")
    parser.add_argument("--all", action="store_true", help="Export every model.")
    parser.add_argument("--fixture-root", type=Path, default=FIXTURE_ROOT)
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Directory for the per-model <key>_metadata.json files.",
    )
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=DEFAULT_EXECUTORCH_ROOT,
    )
    args = parser.parse_args()
    keys = list(MODELS) if args.all else parse_model_keys(args.models)

    executorch_root = args.executorch_root.resolve()
    configure_import_path(executorch_root)
    configure_nsx_import_path(executorch_root)
    import torch

    commit = check_pins(torch, executorch_root)
    use_checkout_schema_resources(executorch_root)

    for key in keys:
        metadata = export_one(torch, MODELS[key], args.fixture_root, executorch_root, commit)
        metadata_path = args.metadata_dir / f"{key}_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        print(f"[{key}] wrote {metadata_path}")


if __name__ == "__main__":
    main()
