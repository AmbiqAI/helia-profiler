#!/usr/bin/env python3
"""Export a deterministic random-weight MLPerf Tiny CIFAR-10 ResNet-8.

The exported method accepts one float32 tensor with logical NCHW shape
[1, 3, 32, 32] and channels-last physical storage, then returns one float32
tensor with shape [1, 10]. Quantize/dequantize nodes at the method boundary
keep this contract while Cortex-M operators use int8 data.

This script targets the ExecuTorch checkout pinned by nsx-executorch. Pass that
checkout's ``external/executorch`` directory with ``--executorch-root``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

MODEL_ID = "mlperf-tiny-ic-resnet8-cifar10-random-int8"
ARCHITECTURE = "MLPerf Tiny CIFAR-10 ResNet-v1 (ResNet-8)"
SEED = 20260817
INPUT_SHAPE = [1, 3, 32, 32]
OUTPUT_SHAPE = [1, 10]
CLASS_COUNT = 10
CALIBRATION_SAMPLES = 32
EXPECTED_EXECUTORCH_VERSION = "1.3.0"
EXPECTED_EXECUTORCH_COMMIT = "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
HELIA_QUALIFIED_NSX_EXECUTORCH_COMMIT = "62b22f96dc49e2c28eb20aee0f15ebb7ad1c1d59"
EXPECTED_TORCH_VERSION = "2.12.0"
METHOD_ARENA_ESTIMATE = 64 * 1024
TEMPORARY_ARENA_ESTIMATE = 32 * 1024


def _git_commit(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _configure_imports(executorch_root: Path) -> None:
    package_root = executorch_root / "src"
    version_file = executorch_root / "version.txt"
    if not (package_root / "executorch").is_dir() or not version_file.is_file():
        raise SystemExit(
            f"{executorch_root} is not an ExecuTorch source checkout "
            "(expected src/executorch and version.txt)"
        )
    version = version_file.read_text(encoding="utf-8").strip()
    commit = _git_commit(executorch_root)
    if version != EXPECTED_EXECUTORCH_VERSION or commit != EXPECTED_EXECUTORCH_COMMIT:
        raise SystemExit(
            "ExecuTorch pin mismatch: "
            f"found version={version!r} commit={commit}, expected "
            f"version={EXPECTED_EXECUTORCH_VERSION!r} "
            f"commit={EXPECTED_EXECUTORCH_COMMIT}"
        )
    # A normal pinned ExecuTorch installation includes schema resources beside
    # the package. Fall back to the source package only for an editable setup.
    if importlib.util.find_spec("executorch") is None:
        sys.path.insert(0, str(package_root))


def _seed_everything(torch: Any) -> None:
    random.seed(SEED)
    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)


def _make_model(torch: Any) -> Any:
    nn = torch.nn

    class ResidualStack(nn.Module):
        def __init__(self, in_channels: int, out_channels: int, stride: int) -> None:
            super().__init__()
            self.conv1 = nn.Conv2d(
                in_channels, out_channels, kernel_size=3, stride=stride, padding=1
            )
            self.bn1 = nn.BatchNorm2d(out_channels)
            self.relu = nn.ReLU()
            self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1, padding=1)
            self.bn2 = nn.BatchNorm2d(out_channels)
            self.projection = (
                nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=stride, padding=0)
                if stride != 1 or in_channels != out_channels
                else nn.Identity()
            )

        def forward(self, x: Any) -> Any:
            residual = self.projection(x)
            y = self.relu(self.bn1(self.conv1(x)))
            y = self.bn2(self.conv2(y))
            return self.relu(residual + y)

    class ResNet8(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.stem = nn.Sequential(
                nn.Conv2d(3, 16, kernel_size=3, stride=1, padding=1),
                nn.BatchNorm2d(16),
                nn.ReLU(),
            )
            self.stack1 = ResidualStack(16, 16, 1)
            self.stack2 = ResidualStack(16, 32, 2)
            self.stack3 = ResidualStack(32, 64, 2)
            self.pool = nn.AvgPool2d(kernel_size=8)
            self.classifier = nn.Linear(64, CLASS_COUNT)

        def forward(self, x: Any) -> Any:
            x = self.stack3(self.stack2(self.stack1(self.stem(x))))
            x = self.pool(x)
            x = torch.flatten(x, 1)
            return torch.softmax(self.classifier(x), dim=1)

    model = ResNet8().eval()
    for module in model.modules():
        if isinstance(module, (nn.Conv2d, nn.Linear)):
            nn.init.kaiming_normal_(module.weight, mode="fan_out", nonlinearity="relu")
            if module.bias is not None:
                nn.init.zeros_(module.bias)
    return model


def _operator_name(operator: Any) -> str:
    return operator.name + (f".{operator.overload}" if operator.overload else "")


def _metadata(
    *,
    pte_path: Path,
    executorch_root: Path,
    torch: Any,
    program: Any,
) -> dict[str, Any]:
    plan = program.execution_plan[0]
    operators = sorted({_operator_name(operator) for operator in plan.operators})
    portable = [name for name in operators if name.startswith("aten::")]
    cortex_m = [name for name in operators if name.startswith("cortex_m::")]
    delegates = sorted({delegate.id for delegate in plan.delegates})
    buffer_sizes = list(plan.non_const_buffer_sizes)
    if len(buffer_sizes) != 2:
        raise RuntimeError(
            "nsx-executorch requires exactly one planned buffer; "
            f"PTE contains {len(buffer_sizes) - 1}: {buffer_sizes}"
        )
    pte_bytes = pte_path.read_bytes()
    planned_size = int(buffer_sizes[1])
    input_tensor = plan.values[plan.inputs[0]].val
    output_tensor = plan.values[plan.outputs[0]].val
    if list(input_tensor.sizes) != INPUT_SHAPE or list(output_tensor.sizes) != OUTPUT_SHAPE:
        raise RuntimeError(
            f"unexpected serialized I/O shapes: {input_tensor.sizes}, {output_tensor.sizes}"
        )
    return {
        "model_id": MODEL_ID,
        "architecture": {
            "name": ARCHITECTURE,
            "stages": [
                {"filters": 16, "blocks": 1, "stride": 1},
                {"filters": 32, "blocks": 1, "stride": 2},
                {"filters": 64, "blocks": 1, "stride": 2},
            ],
            "pool": "8x8 average",
            "class_count": CLASS_COUNT,
            "reference": "AmbiqAI/ai mlperf/src/training/image_classification/keras_model.py",
        },
        "seed": SEED,
        "calibration": {
            "kind": "deterministic random uniform [0, 1)",
            "sample_count": CALIBRATION_SAMPLES,
            "seed": SEED,
        },
        "input": {
            "shape": INPUT_SHAPE,
            "dtype": "float32",
            "layout": "NCHW logical shape; channels-last physical storage",
            "dim_order": list(input_tensor.dim_order),
            "byte_size": 4 * 3 * 32 * 32,
        },
        "output": {
            "shape": OUTPUT_SHAPE,
            "dtype": "float32",
            "dim_order": list(output_tensor.dim_order),
            "semantics": "softmax probabilities for 10 CIFAR-10 classes",
            "byte_size": 4 * CLASS_COUNT,
        },
        "quantization": {
            "scheme": "PT2E CortexMQuantizer int8",
            "weights": "int8 per-channel where supported",
            "activations": "int8 per-tensor",
            "method_boundary": "float32 Q/DQ",
        },
        "pte": {
            "file": pte_path.name,
            "byte_size": len(pte_bytes),
            "sha256": hashlib.sha256(pte_bytes).hexdigest(),
            "method": plan.name,
        },
        "operators": {
            "all": operators,
            "cortex_m": cortex_m,
            "portable": portable,
            "delegated": delegates,
        },
        "arena": {
            "non_const_buffer_sizes": buffer_sizes,
            "planned_arena_size": planned_size,
            "method_arena_size_estimate": METHOD_ARENA_ESTIMATE,
            "temporary_arena_size_estimate": TEMPORARY_ARENA_ESTIMATE,
            "input_size": 4 * 3 * 32 * 32,
            "output_size": 4 * CLASS_COUNT,
            "combined_runtime_arena_estimate": (
                planned_size + METHOD_ARENA_ESTIMATE + TEMPORARY_ARENA_ESTIMATE
            ),
        },
        "source_pins": {
            "torch": torch.__version__,
            "nsx_executorch_helia_qualified_commit": (HELIA_QUALIFIED_NSX_EXECUTORCH_COMMIT),
            "executorch": EXPECTED_EXECUTORCH_VERSION,
            "executorch_commit": _git_commit(executorch_root),
            "cmsis_nn_commit": "d933672e7ca97eec70ef43230baee7b20c2a28ae",
            "export_target": "cortex-m55",
            "delegation": "none; stock Cortex-M/CMSIS-NN portable-kernel path",
        },
        "helia_profiler_config": {
            "model.arena_size": planned_size,
            "engine.config.planned_arena_size": planned_size,
            "engine.config.method_arena_size": METHOD_ARENA_ESTIMATE,
            "engine.config.temporary_arena_size": TEMPORARY_ARENA_ESTIMATE,
            "engine.config.input_size": 4 * 3 * 32 * 32,
            "engine.config.output_size": 4 * CLASS_COUNT,
            "engine.config.portable_ops": portable,
        },
    }


def export(executorch_root: Path, output: Path, metadata_path: Path) -> None:
    _configure_imports(executorch_root)
    torch = importlib.import_module("torch")
    if torch.__version__ != EXPECTED_TORCH_VERSION:
        raise SystemExit(
            f"PyTorch pin mismatch: found {torch.__version__}, expected {EXPECTED_TORCH_VERSION}"
        )

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
    from executorch.extension.export_util.utils import save_pte_program
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    _seed_everything(torch)
    model = _make_model(torch).to(memory_format=torch.channels_last)
    example = torch.rand(INPUT_SHAPE, dtype=torch.float32).to(memory_format=torch.channels_last)

    exported = torch.export.export(model, (example,), strict=True)
    prepared = prepare_pt2e(exported.module(), CortexMQuantizer())
    calibration_generator = torch.Generator().manual_seed(SEED)
    with torch.no_grad():
        for _ in range(CALIBRATION_SAMPLES):
            sample = torch.rand(
                INPUT_SHAPE, dtype=torch.float32, generator=calibration_generator
            ).to(memory_format=torch.channels_last)
            prepared(sample)
    quantized = convert_pt2e(prepared)
    quantized_export = torch.export.export(quantized, (example,), strict=True)

    compile_config = EdgeCompileConfig(
        preserve_ops=[
            torch.ops.aten.linear.default,
            torch.ops.aten.hardsigmoid.default,
            torch.ops.aten.hardsigmoid_.default,
            torch.ops.aten.hardswish.default,
            torch.ops.aten.hardswish_.default,
        ],
        _check_ir_validity=False,
    )
    edge = to_edge_transform_and_lower(
        quantized_export,
        compile_config=compile_config,
    )
    target = CortexMTargetConfig(cpu=CortexM.M55)
    edge._edge_programs["forward"] = CortexMPassManager(  # noqa: SLF001
        edge.exported_program(), target_config=target
    ).transform()
    executorch_program = edge.to_executorch(
        config=ExecutorchBackendConfig(extract_delegate_segments=False)
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    save_pte_program(executorch_program, str(output))
    metadata = _metadata(
        pte_path=output,
        executorch_root=executorch_root,
        torch=torch,
        program=executorch_program.executorch_program,
    )
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {output} ({metadata['pte']['byte_size']} bytes, sha256={metadata['pte']['sha256']})"
    )
    print(
        f"Planned arena: {metadata['arena']['planned_arena_size']} bytes; "
        f"portable fallbacks: {metadata['operators']['portable']}"
    )


def main() -> None:
    default_root = Path(
        os.environ.get(
            "EXECUTORCH_ROOT",
            Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
        )
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=default_root,
        help="Pinned ExecuTorch source checkout (default: NSX_EXECUTORCH_ROOT/external/executorch)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("ic_resnet8_random_int8.pte"),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        default=Path(__file__).with_name("metadata.json"),
    )
    args = parser.parse_args()
    export(
        args.executorch_root.expanduser().resolve(),
        args.output.expanduser().resolve(),
        args.metadata.expanduser().resolve(),
    )


if __name__ == "__main__":
    main()
