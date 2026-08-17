#!/usr/bin/env python3
"""Export a deterministic random-weight MLPerf Tiny KWS DS-CNN.

Contract:
  input:  float32 MFCC tensor, NCHW shape [1, 1, 49, 10]
  output: float32 logits, shape [1, 12]

The network follows ExecuTorch's canonical MLPerf Tiny implementation: a
10x4/2 stem with 64 channels, four 3x3 depthwise-separable blocks, global
24x5 average pooling, and a 12-class linear head. The graph is statically
INT8-quantized and lowered to the non-delegate Cortex-M/CMSIS-NN operators.
Weights and synthetic calibration inputs are reproducible, not trained.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_EXECUTORCH_ROOT = Path(
    os.environ.get(
        "EXECUTORCH_ROOT",
        Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
    )
)
MODEL_ID = "mlperf-tiny-kws-dscnn-random-cortex-m55"
SEED = 20260817
INPUT_SHAPE = [1, 1, 49, 10]
OUTPUT_SHAPE = [1, 12]
NUM_CLASSES = 12
CALIBRATION_SAMPLES = 32


def _configure_import_path(executorch_root: Path) -> None:
    for path in (executorch_root / "src", executorch_root):
        sys.path.insert(0, str(path))


def _make_model(torch):
    nn = torch.nn

    class _DepthwiseSeparableConv(nn.Module):
        def __init__(self, channels: int) -> None:
            super().__init__()
            self.depthwise = nn.Conv2d(
                channels,
                channels,
                kernel_size=(3, 3),
                padding=(1, 1),
                groups=channels,
                bias=False,
            )
            self.depthwise_bn = nn.BatchNorm2d(channels)
            self.pointwise = nn.Conv2d(channels, channels, kernel_size=(1, 1), bias=False)
            self.pointwise_bn = nn.BatchNorm2d(channels)
            self.relu = nn.ReLU(inplace=True)

        def forward(self, x):
            x = self.relu(self.depthwise_bn(self.depthwise(x)))
            return self.relu(self.pointwise_bn(self.pointwise(x)))

    class DSCNNKWS(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.feature_extractor = nn.Sequential(
                nn.Conv2d(
                    1,
                    64,
                    kernel_size=(10, 4),
                    stride=(2, 2),
                    padding=(5, 1),
                    bias=False,
                ),
                nn.BatchNorm2d(64),
                nn.ReLU(inplace=True),
                nn.Dropout(p=0.2),
                _DepthwiseSeparableConv(64),
                _DepthwiseSeparableConv(64),
                _DepthwiseSeparableConv(64),
                _DepthwiseSeparableConv(64),
                nn.Dropout(p=0.4),
            )
            self.pool = nn.AvgPool2d(kernel_size=(24, 5))
            self.classifier = nn.Linear(64, NUM_CLASSES)

        def forward(self, x):
            x = self.feature_extractor(x)
            x = self.pool(x)
            x = torch.flatten(x, 1)
            return self.classifier(x)

    torch.manual_seed(SEED)
    return DSCNNKWS().eval()


def _calibration_data(torch):
    generator = torch.Generator().manual_seed(SEED + 1)
    samples = []
    for index in range(CALIBRATION_SAMPLES):
        sample = torch.rand(INPUT_SHAPE, generator=generator) * 2.0 - 1.0
        # Add deterministic full-range samples so observers do not depend on
        # unusually narrow random extrema.
        if index == 0:
            sample.fill_(-1.0)
        elif index == 1:
            sample.fill_(1.0)
        samples.append(sample.to(memory_format=torch.channels_last))
    return samples


def _operator_name(operator) -> str:
    overload = getattr(operator, "overload", "")
    return f"{operator.name}.{overload}" if overload else operator.name


def _inspect_program(pte_path: Path):
    from executorch.exir._serialize import _deserialize_pte_binary
    from executorch.exir.schema import KernelCall

    pte_file = _deserialize_pte_binary(pte_path.read_bytes())
    program = pte_file.program
    plan = program.execution_plan[0]
    operators = [_operator_name(operator) for operator in plan.operators]
    counts = collections.Counter()
    for chain in plan.chains:
        for instruction in chain.instructions:
            if isinstance(instruction.instr_args, KernelCall):
                counts[operators[instruction.instr_args.op_index]] += 1
    buffers = list(plan.non_const_buffer_sizes)
    delegates = [delegate.id for delegate in plan.delegates]
    return operators, counts, delegates, buffers


def _use_checkout_schema_resources(executorch_root: Path) -> None:
    """Supply resources normally copied into an installed ExecuTorch wheel."""
    import executorch.exir._serialize._flatbuffer as flatbuffer

    schema_dir = executorch_root / "schema"

    class CheckoutResourceFiles:
        def __init__(self, resource_names) -> None:
            self._files = {name: (schema_dir / name).read_bytes() for name in resource_names}

        def patch_files(self, patch_fn) -> None:
            self._files = {name: patch_fn(data) for name, data in self._files.items()}

        def get(self, name):
            return self._files[name]

        def write_to(self, out_dir) -> None:
            for name, data in self._files.items():
                (Path(out_dir) / name).write_bytes(data)

    flatbuffer._ResourceFiles = CheckoutResourceFiles


def _git_revision(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def export(output: Path, metadata_path: Path, executorch_root: Path) -> None:
    _configure_import_path(executorch_root)

    import torch
    import torchao
    from executorch.backends.cortex_m.passes.cortex_m_pass_manager import (
        CortexMPassManager,
    )
    from executorch.backends.cortex_m.quantizer.quantizer import CortexMQuantizer
    from executorch.backends.cortex_m.target_config import CortexM, CortexMTargetConfig
    from executorch.exir import (
        EdgeCompileConfig,
        ExecutorchBackendConfig,
        to_edge_transform_and_lower,
    )
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    _use_checkout_schema_resources(executorch_root)
    torch.use_deterministic_algorithms(True)
    model = _make_model(torch)
    example_input = (
        torch.linspace(-1.0, 1.0, 490).reshape(INPUT_SHAPE).to(memory_format=torch.channels_last)
    )

    exported = torch.export.export(model, (example_input,))
    graph_module = exported.module().to(memory_format=torch.channels_last)
    prepared = prepare_pt2e(graph_module, CortexMQuantizer())
    with torch.no_grad():
        for sample in _calibration_data(torch):
            prepared(sample)
    quantized = convert_pt2e(prepared)
    quantized_exported = torch.export.export(quantized, (example_input,))

    edge_config = EdgeCompileConfig(
        preserve_ops=[
            torch.ops.aten.linear.default,
            torch.ops.aten.hardsigmoid.default,
            torch.ops.aten.hardsigmoid_.default,
            torch.ops.aten.hardswish.default,
            torch.ops.aten.hardswish_.default,
        ],
        _check_ir_validity=False,
    )
    edge = to_edge_transform_and_lower(quantized_exported, compile_config=edge_config)
    target = CortexMTargetConfig(cpu=CortexM.M55)
    edge._edge_programs["forward"] = CortexMPassManager(
        edge.exported_program(), target_config=target
    ).transform()
    executorch_program = edge.to_executorch(
        config=ExecutorchBackendConfig(extract_delegate_segments=False)
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(executorch_program.buffer))

    operators, counts, delegates, non_const_buffers = _inspect_program(output)
    cortex_m_ops = sorted(name for name in operators if name.startswith("cortex_m::"))
    portable_ops = sorted(
        name
        for name in operators
        if not name.startswith("cortex_m::") and not name.startswith("executorch_prim::")
    )
    planned_arena = sum(non_const_buffers[1:])
    pte = output.read_bytes()
    nsx_root = executorch_root.parents[1]

    with torch.no_grad():
        eager_output = model(example_input)
        quantized_output = quantized(example_input)
    assert list(eager_output.shape) == OUTPUT_SHAPE
    assert list(quantized_output.shape) == OUTPUT_SHAPE

    metadata = {
        "model_id": MODEL_ID,
        "architecture": {
            "name": "MLPerf Tiny keyword spotting DS-CNN",
            "stem": "Conv2d(1,64,kernel=10x4,stride=2x2,padding=5x1)+BN+ReLU",
            "blocks": 4,
            "block": "depthwise 3x3 + pointwise 1x1, each BN+ReLU",
            "pool": "AvgPool2d(24x5)",
            "classifier": f"Linear(64,{NUM_CLASSES})",
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "weights": "deterministic random initialization; not trained",
            "reference": "executorch/examples/models/mlperf_tiny/ds_cnn.py",
        },
        "seed": SEED,
        "calibration": {
            "kind": "deterministic synthetic uniform [-1,1]",
            "seed": SEED + 1,
            "samples": CALIBRATION_SAMPLES,
        },
        "input": {
            "shape": INPUT_SHAPE,
            "dtype": "float32",
            "layout": "NCHW (channels_last physical memory format during export)",
            "bytes": 490 * 4,
        },
        "output": {
            "shape": OUTPUT_SHAPE,
            "dtype": "float32",
            "semantics": "12 keyword-class logits",
            "bytes": NUM_CLASSES * 4,
        },
        "pte": {
            "file": output.name,
            "bytes": len(pte),
            "sha256": hashlib.sha256(pte).hexdigest(),
            "format": "ExecuTorch PTE flatbuffer",
        },
        "lowering": {
            "target": "cortex-m55",
            "backend": target.backend.name,
            "quantization": "symmetric INT8, float32 model boundary",
            "delegated": False,
            "extract_delegate_segments": False,
        },
        "operators": {
            "serialized": [{"name": name, "invocations": counts[name]} for name in operators],
            "cortex_m_cmsis_nn": cortex_m_ops,
            "portable_required_by_helia_config": portable_ops,
            "delegated": delegates,
        },
        "arena_sizes": {
            "planned_arena_size": {
                "bytes": planned_arena,
                "source": "PTE execution_plan[0].non_const_buffer_sizes",
                "buffers": non_const_buffers,
            },
            "method_arena_size": {
                "bytes": 65536,
                "source": "conservative helia-profiler ExecuTorch config value",
            },
            "temporary_arena_size": {
                "bytes": 32768,
                "source": "conservative helia-profiler ExecuTorch config value",
            },
            "total_runtime_arenas": {
                "bytes": planned_arena + 65536 + 32768,
                "excludes": ["PTE storage", "input buffer", "output buffer"],
            },
        },
        "helia_profiler_config": {
            "model.arena_size": planned_arena,
            "engine.config.planned_arena_size": planned_arena,
            "engine.config.method_arena_size": 65536,
            "engine.config.temporary_arena_size": 32768,
            "engine.config.input_size": 490 * 4,
            "engine.config.output_size": NUM_CLASSES * 4,
            "engine.config.portable_ops": portable_ops,
        },
        "toolchain": {
            "executorch_version": (executorch_root / "version.txt").read_text().strip(),
            "executorch_git_revision": _git_revision(executorch_root),
            "nsx_executorch_git_revision": _git_revision(nsx_root),
            "torch_version": torch.__version__,
            "torchao_version": torchao.__version__,
            "torchao_git_revision": "02105d46c61dc80a8c9d39d5836e827ba3af8439",
            "cmsis_nn_git_revision": "d933672e7ca97eec70ef43230baee7b20c2a28ae",
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

    print(f"Wrote {output} ({len(pte)} bytes)")
    print(f"Wrote {metadata_path}")
    print(f"SHA-256: {metadata['pte']['sha256']}")
    print(f"Planned arena: {planned_arena} bytes")
    print(f"Portable ops: {portable_ops}")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=script_dir / "kws_dscnn_random_cortex_m55.pte",
    )
    parser.add_argument("--metadata", type=Path, default=script_dir / "metadata.json")
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=Path(os.environ.get("EXECUTORCH_ROOT", DEFAULT_EXECUTORCH_ROOT)),
    )
    args = parser.parse_args()
    export(
        args.output.resolve(),
        args.metadata.resolve(),
        args.executorch_root.resolve(),
    )


if __name__ == "__main__":
    main()
