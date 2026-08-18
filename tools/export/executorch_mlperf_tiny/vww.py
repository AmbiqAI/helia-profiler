#!/usr/bin/env python3
"""Export a deterministic random-weight MLPerf Tiny VWW MobileNetV1 PTE.

The exported method has one float32 channels-last tensor with logical shape
``[1, 3, 96, 96]`` (physical NHWC bytes) and one float32 output ``[1, 2]``.
The pinned ExecuTorch Cortex-M PT2E flow quantizes the network body to int8
CMSIS-NN operators while retaining float method I/O, as required by that flow.
The two classes are ``not_person`` (index 0) and ``person`` (index 1).

Run this script with ExecuTorch v1.3.0's Python dependencies available and
point ``--executorch-root`` at the pinned source checkout. It writes both the
PTE and metadata derived by deserializing the resulting PTE.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

SEED = 20260817
INPUT_SHAPE = [1, 3, 96, 96]
OUTPUT_SHAPE = [1, 2]
CLASS_COUNT = 2
CALIBRATION_SAMPLES = 16
EXPECTED_EXECUTORCH_COMMIT = "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
EXPECTED_EXECUTORCH_VERSION = "1.3.0"
DEFAULT_EXECUTORCH_ROOT = Path(
    os.environ.get(
        "EXECUTORCH_ROOT",
        Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
    )
)


def _parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=Path(os.environ.get("EXECUTORCH_ROOT", DEFAULT_EXECUTORCH_ROOT)),
        help="Pinned pytorch/executorch source root (v1.3.0).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=here / "vww_mobilenetv1_random_int8.pte",
        help="Output PTE path.",
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=here / "metadata.json",
        help="Output metadata JSON path.",
    )
    parser.add_argument(
        "--inspect-only",
        type=Path,
        help="Only deserialize and print a summary for an existing PTE.",
    )
    return parser.parse_args()


def _configure_executorch_imports(root: Path) -> None:
    root = root.expanduser().resolve()
    if not (root / "version.txt").is_file():
        raise SystemExit(f"Invalid ExecuTorch source root: {root}")
    version = (root / "version.txt").read_text(encoding="utf-8").strip()
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if version != EXPECTED_EXECUTORCH_VERSION or commit != EXPECTED_EXECUTORCH_COMMIT:
        raise SystemExit(
            "ExecuTorch source mismatch: "
            f"version={version!r}, commit={commit}; expected "
            f"{EXPECTED_EXECUTORCH_VERSION!r}, {EXPECTED_EXECUTORCH_COMMIT}"
        )
    try:
        installed_version = importlib.metadata.version("executorch")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SystemExit(
            "Install the pinned executorch==1.3.0 wheel in the export environment."
        ) from exc
    if installed_version != EXPECTED_EXECUTORCH_VERSION:
        raise SystemExit(
            f"Installed executorch is {installed_version}, expected {EXPECTED_EXECUTORCH_VERSION}"
        )
    packaged_flatc = Path(sys.executable).parent / "flatc"
    if packaged_flatc.is_file():
        os.environ.setdefault("FLATC_EXECUTABLE", str(packaged_flatc))


def _operator_name(operator: object) -> str:
    name = str(getattr(operator, "name"))
    overload = str(getattr(operator, "overload"))
    return f"{name}.{overload}" if overload else name


def _tensor_metadata(plan: object, indexes: list[int]) -> list[dict[str, object]]:
    tensors: list[dict[str, object]] = []
    values = getattr(plan, "values")
    for index in indexes:
        value = values[index].val
        scalar_type = getattr(value, "scalar_type", None)
        if scalar_type is None:
            tensors.append({"value_index": index, "kind": type(value).__name__})
            continue
        tensors.append(
            {
                "value_index": index,
                "shape": list(value.sizes),
                "scalar_type": getattr(scalar_type, "name", str(scalar_type)),
                "dim_order": list(value.dim_order),
            }
        )
    return tensors


def inspect_pte(data: bytes) -> dict[str, object]:
    from executorch.exir._serialize import _deserialize_pte_binary

    pte_file = _deserialize_pte_binary(data)
    program = pte_file.program
    if len(program.execution_plan) != 1:
        raise RuntimeError(f"Expected one execution plan, got {len(program.execution_plan)}")
    plan = program.execution_plan[0]
    operators = [_operator_name(operator) for operator in plan.operators]
    portable = sorted({name for name in operators if not name.startswith("cortex_m::")})
    cortex_m = sorted({name for name in operators if name.startswith("cortex_m::")})
    delegates = [delegate.id for delegate in plan.delegates]
    operator_sequence: list[str] = []
    for chain in plan.chains:
        for instruction in chain.instructions:
            args = instruction.instr_args
            if hasattr(args, "op_index"):
                operator_sequence.append(operators[args.op_index])
            elif hasattr(args, "delegate_index"):
                operator_sequence.append(f"delegate::{delegates[args.delegate_index]}")
    operator_counts = {
        name: operator_sequence.count(name) for name in sorted(set(operator_sequence))
    }
    non_const_sizes = list(plan.non_const_buffer_sizes)
    if len(non_const_sizes) != 2 or non_const_sizes[0] != 0:
        raise RuntimeError(
            "nsx-executorch requires exactly one planned buffer; serialized "
            f"sizes were {non_const_sizes}"
        )
    return {
        "program_version": program.version,
        "method": plan.name,
        "inputs": _tensor_metadata(plan, list(plan.inputs)),
        "outputs": _tensor_metadata(plan, list(plan.outputs)),
        "operators": operators,
        "operator_sequence": operator_sequence,
        "operator_counts": operator_counts,
        "portable_operators": portable,
        "cortex_m_operators": cortex_m,
        "delegated_backends": delegates,
        "non_const_buffer_sizes": non_const_sizes,
        "planned_arena_size": non_const_sizes[1],
    }


def _build_model():
    import torch

    class ConvBNReLU(torch.nn.Sequential):
        def __init__(
            self,
            in_channels: int,
            out_channels: int,
            kernel_size: int,
            stride: int,
            groups: int = 1,
        ) -> None:
            super().__init__(
                torch.nn.Conv2d(
                    in_channels,
                    out_channels,
                    kernel_size,
                    stride=stride,
                    padding=kernel_size // 2,
                    groups=groups,
                    bias=True,
                ),
                torch.nn.BatchNorm2d(out_channels),
                torch.nn.ReLU(inplace=False),
            )

    class DepthwiseSeparable(torch.nn.Module):
        def __init__(self, in_channels: int, out_channels: int, stride: int) -> None:
            super().__init__()
            self.depthwise = ConvBNReLU(
                in_channels, in_channels, kernel_size=3, stride=stride, groups=in_channels
            )
            self.pointwise = ConvBNReLU(in_channels, out_channels, kernel_size=1, stride=1)

        def forward(self, x):
            return self.pointwise(self.depthwise(x))

    class VwwMobileNetV1(torch.nn.Module):
        """MLPerf Tiny MobileNetV1 at width multiplier 0.25."""

        def __init__(self) -> None:
            super().__init__()
            self.stem = ConvBNReLU(3, 8, kernel_size=3, stride=2)
            block_spec = [
                (8, 16, 1),
                (16, 32, 2),
                (32, 32, 1),
                (32, 64, 2),
                (64, 64, 1),
                (64, 128, 2),
                *[(128, 128, 1)] * 5,
                (128, 256, 2),
                (256, 256, 1),
            ]
            self.blocks = torch.nn.Sequential(*[DepthwiseSeparable(*spec) for spec in block_spec])
            self.pool = torch.nn.AvgPool2d(kernel_size=3)
            self.classifier = torch.nn.Linear(256, CLASS_COUNT)

        def forward(self, x):
            x = self.stem(x)
            x = self.blocks(x)
            x = self.pool(x)
            x = torch.flatten(x, 1)
            return torch.softmax(self.classifier(x), dim=-1)

    return VwwMobileNetV1()


def export_pte() -> bytes:
    import torch
    from executorch.backends.cortex_m.passes.cortex_m_pass_manager import (
        CortexMPassManager,
    )
    from executorch.backends.cortex_m.quantizer.quantizer import CortexMQuantizer
    from executorch.backends.cortex_m.target_config import CortexM, CortexMTargetConfig
    from executorch.exir import EdgeCompileConfig, ExecutorchBackendConfig, to_edge
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    model = _build_model().eval().to(memory_format=torch.channels_last)
    example = torch.zeros(INPUT_SHAPE, dtype=torch.float32).to(memory_format=torch.channels_last)

    exported = torch.export.export(model, (example,), strict=True)
    prepared = prepare_pt2e(exported.module(), CortexMQuantizer())
    generator = torch.Generator().manual_seed(SEED + 1)
    with torch.no_grad():
        for _ in range(CALIBRATION_SAMPLES):
            calibration = torch.rand(INPUT_SHAPE, dtype=torch.float32, generator=generator).to(
                memory_format=torch.channels_last
            )
            prepared(calibration)
    quantized = convert_pt2e(prepared)
    quantized_export = torch.export.export(quantized, (example,), strict=True)

    edge_config = EdgeCompileConfig(
        preserve_ops=[torch.ops.aten.linear.default],
        _check_ir_validity=False,
    )
    edge = to_edge(quantized_export, compile_config=edge_config)
    pass_manager = CortexMPassManager(
        edge.exported_program(),
        target_config=CortexMTargetConfig(cpu=CortexM.M55),
    )
    edge._edge_programs["forward"] = pass_manager.transform()
    program = edge.to_executorch(config=ExecutorchBackendConfig(extract_delegate_segments=False))
    return bytes(program.buffer)


def _metadata(data: bytes, inspection: dict[str, object]) -> dict[str, object]:
    planned_size = int(inspection["planned_arena_size"])
    return {
        "model_id": "mlperf-tiny-vww-mobilenetv1-random-int8",
        "architecture": {
            "name": "MobileNetV1",
            "benchmark": "MLPerf Tiny visual wake words",
            "input_resolution": [96, 96],
            "width_multiplier": 0.25,
            "stem_channels": 8,
            "depthwise_separable_blocks": 13,
            "final_channels": 256,
            "class_count": CLASS_COUNT,
            "class_labels": ["not_person", "person"],
            "head": "3x3 average pool, 256x2 linear, softmax",
            "weights": "deterministic random initialization; not trained",
        },
        "seed": SEED,
        "calibration": {
            "seed": SEED + 1,
            "samples": CALIBRATION_SAMPLES,
            "distribution": "uniform [0, 1)",
        },
        "input": {
            "shape": INPUT_SHAPE,
            "canonical_nhwc_shape": [1, 96, 96, 3],
            "dtype": "float32",
            "byte_size": 1 * 3 * 96 * 96 * 4,
            "memory_format": "channels_last (physical NHWC)",
            "value_range": "[0, 1]",
            "note": "Pinned Cortex-M PT2E flow keeps float method I/O; body is int8.",
        },
        "output": {
            "shape": OUTPUT_SHAPE,
            "dtype": "float32",
            "byte_size": 1 * CLASS_COUNT * 4,
            "semantics": "softmax probabilities [not_person, person]",
        },
        "pte": {
            "file": "vww_mobilenetv1_random_int8.pte",
            "byte_size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "format": "ExecuTorch PTE flatbuffer",
            "executorch_version": EXPECTED_EXECUTORCH_VERSION,
            "executorch_commit": EXPECTED_EXECUTORCH_COMMIT,
            "target": "Cortex-M55 / CMSIS-NN MVE",
        },
        "operators": {
            "program": inspection["operators"],
            "execution_sequence": inspection["operator_sequence"],
            "execution_counts": inspection["operator_counts"],
            "portable": inspection["portable_operators"],
            "cortex_m": inspection["cortex_m_operators"],
            "delegated": inspection["delegated_backends"],
        },
        "arenas": {
            "planned_arena_size": planned_size,
            "planned_arena_source": "exact PTE execution_plan non-constant buffer",
            "method_arena_size": 65536,
            "method_arena_source": "nsx-executorch/helia-profiler conservative default",
            "temporary_arena_size": 32768,
            "temporary_arena_source": (
                "nsx-executorch/helia-profiler conservative default; CMSIS-NN "
                "scratch is allocated here"
            ),
            "serialized_non_const_buffer_sizes": inspection["non_const_buffer_sizes"],
        },
        "helia_profiler_engine_config": {
            "planned_arena_size": planned_size,
            "method_arena_size": 65536,
            "temporary_arena_size": 32768,
            "input_size": 1 * 3 * 96 * 96 * 4,
            "output_size": 1 * CLASS_COUNT * 4,
            "portable_ops": inspection["portable_operators"],
        },
        "inspection": {
            "tool": "executorch.exir._serialize._deserialize_pte_binary",
            "program_version": inspection["program_version"],
            "method": inspection["method"],
            "inputs": inspection["inputs"],
            "outputs": inspection["outputs"],
        },
        "provenance": {
            "canonical_architecture": (
                "AmbiqAI/ai mlperf/src/training/visual_wake_words/vww_model.py "
                "@ be45105372078725809f52b4054bbd0ea0bca257"
            ),
            "export_flow": (
                "pytorch/executorch CortexMQuantizer + CortexMPassManager, "
                "pinned by AmbiqAI/nsx-executorch"
            ),
        },
    }


def main() -> None:
    args = _parse_args()
    _configure_executorch_imports(args.executorch_root)
    if args.inspect_only:
        data = args.inspect_only.read_bytes()
        print(json.dumps(inspect_pte(data), indent=2))
        return

    data = export_pte()
    inspection = inspect_pte(data)
    if inspection["delegated_backends"]:
        raise RuntimeError("Cortex-M export unexpectedly contains delegated operators")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    metadata = _metadata(data, inspection)
    args.metadata.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {args.output} ({len(data)} bytes, sha256={metadata['pte']['sha256']})")
    print(f"Wrote {args.metadata}")


if __name__ == "__main__":
    main()
