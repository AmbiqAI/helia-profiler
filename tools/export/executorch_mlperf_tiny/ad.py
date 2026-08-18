#!/usr/bin/env python3
"""Export a deterministic random-weight MLPerf Tiny anomaly detector to PTE.

Contract:
  input:  float32 tensor [1, 640] (one ToyADMOS/ToyADMX feature vector)
  output: float32 tensor [1, 640] (the reconstructed feature vector)

The internal linear layers are statically quantized to int8 and rewritten to
the ExecuTorch Cortex-M operator dialect backed by CMSIS-NN. Cortex-M is an
operator backend, not a delegate backend, so this PTE has no delegate blobs.

This script targets the ExecuTorch v1.3.0 source pin in nsx-executorch:
3a97429b0ce0c192861fc3e3729fb81432fd22cf. Set NSX_EXECUTORCH_ROOT or pass
--executorch-root when the checkout is elsewhere.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


SEED = 20260817
INPUT_SHAPE = [1, 640]
OUTPUT_SHAPE = [1, 640]
HIDDEN_DIMS = [128, 128, 128, 128, 8, 128, 128, 128, 128]
EXPECTED_EXECUTORCH_COMMIT = "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
EXPECTED_TORCH_VERSION = "2.12.0"
DEFAULT_EXECUTORCH_ROOT = Path(
    os.environ.get(
        "EXECUTORCH_ROOT",
        Path(os.environ.get("NSX_EXECUTORCH_ROOT", ".")) / "external" / "executorch",
    )
)


def _configure_source_path(executorch_root: Path) -> None:
    source_package = executorch_root / "src"
    if not (source_package / "executorch" / "exir").exists():
        raise RuntimeError(f"Not an ExecuTorch source checkout: {executorch_root}")
    for path in (str(source_package), str(executorch_root)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _enable_source_checkout_resources(executorch_root: Path) -> None:
    """Make checked-in schemas visible when ExecuTorch is used without a wheel."""
    from executorch.exir._serialize import _flatbuffer

    package_schema = Path(_flatbuffer.__file__).parent / "program.fbs"
    if package_schema.is_file():
        return
    schema_root = executorch_root / "schema"
    original_resource_files = _flatbuffer._ResourceFiles

    class SourceCheckoutResourceFiles(original_resource_files):
        def __init__(self, resource_names) -> None:
            self._files = {name: (schema_root / name).read_bytes() for name in resource_names}

    _flatbuffer._ResourceFiles = SourceCheckoutResourceFiles


def _git_commit(repo: Path) -> str:
    import subprocess

    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _make_model():
    import torch
    from torch import nn

    class DeepAutoEncoder(nn.Module):
        """Canonical MLPerf Tiny DeepAutoEncoder/ToyADMX topology."""

        def __init__(self) -> None:
            super().__init__()
            layers: list[nn.Module] = []
            in_dim = INPUT_SHAPE[-1]
            for out_dim in HIDDEN_DIMS:
                layers.extend(
                    [
                        nn.Linear(in_dim, out_dim, bias=True),
                        nn.BatchNorm1d(out_dim),
                        nn.ReLU(inplace=True),
                    ]
                )
                in_dim = out_dim
            self.encoder_decoder = nn.Sequential(*layers)
            self.output_layer = nn.Linear(in_dim, OUTPUT_SHAPE[-1], bias=True)

        def forward(self, x):
            return self.output_layer(self.encoder_decoder(x))

    torch.manual_seed(SEED)
    return DeepAutoEncoder().eval()


def _calibration_samples():
    import torch

    generator = torch.Generator().manual_seed(SEED + 1)
    return tuple((torch.rand(INPUT_SHAPE, generator=generator) * 2.0 - 1.0,) for _ in range(8))


def _runtime_ops(program) -> list[str]:
    names: set[str] = set()
    for plan in program.execution_plan:
        for operator in plan.operators:
            names.add(
                operator.name if not operator.overload else f"{operator.name}.{operator.overload}"
            )
    return sorted(names)


def _tensor_shape(plan, value_index: int) -> list[int]:
    tensor = plan.values[value_index].val
    return list(tensor.sizes)


def _inspect_program(program) -> dict:
    if len(program.execution_plan) != 1:
        raise RuntimeError("Expected exactly one execution plan")
    plan = program.execution_plan[0]
    if len(plan.inputs) != 1 or len(plan.outputs) != 1:
        raise RuntimeError("Expected one tensor input and one tensor output")
    input_shape = _tensor_shape(plan, plan.inputs[0])
    output_shape = _tensor_shape(plan, plan.outputs[0])
    if input_shape != INPUT_SHAPE or output_shape != OUTPUT_SHAPE:
        raise RuntimeError(f"Unexpected PTE contract: input={input_shape}, output={output_shape}")
    sizes = list(plan.non_const_buffer_sizes)
    return {
        "method": plan.name,
        "input_shape": input_shape,
        "output_shape": output_shape,
        "runtime_ops": _runtime_ops(program),
        "delegates": [delegate.id for delegate in plan.delegates],
        "non_const_buffer_sizes": sizes,
        "planned_arena_size": sum(sizes[1:]),
    }


def export(output_dir: Path, executorch_root: Path) -> None:
    _configure_source_path(executorch_root)

    import torch
    from executorch.backends.cortex_m.passes.cortex_m_pass_manager import (
        CortexMPassManager,
    )
    from executorch.backends.cortex_m.quantizer.quantizer import CortexMQuantizer
    from executorch.exir import EdgeCompileConfig, to_edge_transform_and_lower
    from executorch.exir._serialize._program import deserialize_pte_binary
    from torchao.quantization.pt2e.quantize_pt2e import convert_pt2e, prepare_pt2e

    _enable_source_checkout_resources(executorch_root)
    commit = _git_commit(executorch_root)
    if commit != EXPECTED_EXECUTORCH_COMMIT:
        raise RuntimeError(f"ExecuTorch commit is {commit}; expected {EXPECTED_EXECUTORCH_COMMIT}")
    if torch.__version__.split("+", 1)[0] != EXPECTED_TORCH_VERSION:
        raise RuntimeError(f"PyTorch is {torch.__version__}; expected {EXPECTED_TORCH_VERSION}")

    torch.manual_seed(SEED)
    torch.use_deterministic_algorithms(True)
    model = _make_model()
    example_inputs = _calibration_samples()[0]

    captured = torch.export.export(model, example_inputs).module()
    prepared = prepare_pt2e(captured, CortexMQuantizer())
    for sample in _calibration_samples():
        prepared(*sample)
    quantized = convert_pt2e(prepared)
    exported = torch.export.export(quantized, example_inputs)

    edge = to_edge_transform_and_lower(
        exported,
        compile_config=EdgeCompileConfig(
            preserve_ops=[
                torch.ops.aten.linear.default,
                torch.ops.aten.hardsigmoid.default,
                torch.ops.aten.hardsigmoid_.default,
                torch.ops.aten.hardswish.default,
                torch.ops.aten.hardswish_.default,
            ],
            _check_ir_validity=False,
            _core_aten_ops_exception_list=[torch.ops.aten.max_pool2d.default],
        ),
    )
    edge._edge_programs["forward"] = CortexMPassManager(
        edge.exported_program(), CortexMPassManager.pass_list
    ).transform()
    executorch_program = edge.to_executorch()

    output_dir.mkdir(parents=True, exist_ok=True)
    pte_path = output_dir / "deep_autoencoder_int8_random.pte"
    with pte_path.open("wb") as output_file:
        executorch_program.write_to_file(output_file)

    pte_bytes = pte_path.read_bytes()
    deserialized = deserialize_pte_binary(pte_bytes).program
    inspection = _inspect_program(deserialized)
    if inspection["delegates"]:
        raise RuntimeError("Cortex-M operator export unexpectedly contains delegates")

    metadata = {
        "model_id": "mlperf_tiny_ad_deep_autoencoder_int8_random",
        "model_family": "MLPerf Tiny anomaly detection (ToyADMOS/ToyADMX)",
        "architecture": {
            "name": "DeepAutoEncoder",
            "input_dim": INPUT_SHAPE[-1],
            "hidden_dims": HIDDEN_DIMS,
            "output_dim": OUTPUT_SHAPE[-1],
            "linear_layers": 10,
            "hidden_batch_norm_layers": 9,
            "hidden_activations": "ReLU",
            "quantization": "PT2E static int8 activations/weights; float32 I/O",
            "backend": "ExecuTorch Cortex-M operator dialect / CMSIS-NN",
        },
        "seed": SEED,
        "input": {
            "shape": INPUT_SHAPE,
            "dtype": "float32",
            "bytes": 2560,
            "description": "one flattened 640-element ToyADMOS/ToyADMX feature vector",
        },
        "output": {
            "shape": OUTPUT_SHAPE,
            "dtype": "float32",
            "bytes": 2560,
            "description": "reconstructed feature vector",
        },
        "pte": {
            "path": pte_path.name,
            "byte_size": len(pte_bytes),
            "sha256": hashlib.sha256(pte_bytes).hexdigest(),
            "format_identifier": pte_bytes[4:8].decode("ascii"),
            "method": inspection["method"],
        },
        "operators": {
            "runtime": inspection["runtime_ops"],
            "cortex_m": [op for op in inspection["runtime_ops"] if op.startswith("cortex_m::")],
            "portable": [op for op in inspection["runtime_ops"] if not op.startswith("cortex_m::")],
            "delegated": [],
            "helia_profiler_portable_ops": [],
            "note": "Cortex-M uses registered operators, not delegate partitions.",
        },
        "arenas": {
            "pte_non_const_buffer_sizes": inspection["non_const_buffer_sizes"],
            "planned_arena_size": inspection["planned_arena_size"],
            "method_arena_size_recommended": 65536,
            "temporary_arena_size_recommended": 32768,
            "input_size": 2560,
            "output_size": 2560,
            "config_note": (
                "Use planned_arena_size as model.arena_size and "
                "engine.config.planned_arena_size; method/temporary/input/output "
                "values map directly to the helia-profiler ExecuTorch adapter."
            ),
        },
        "helia_profiler_config": {
            "model": {
                "arena_size": inspection["planned_arena_size"],
            },
            "engine": {
                "backend": "arm_or_ns",
                "config": {
                    "planned_arena_size": inspection["planned_arena_size"],
                    "method_arena_size": 65536,
                    "temporary_arena_size": 32768,
                    "input_size": 2560,
                    "output_size": 2560,
                    "portable_ops": [],
                },
            },
        },
        "toolchain": {
            "executorch_commit": commit,
            "executorch_version": "1.3.0",
            "torch_version": torch.__version__,
            "torchao_version": __import__("torchao").__version__,
            "cmsis_nn_source_commit": ("d933672e7ca97eec70ef43230baee7b20c2a28ae"),
            "cortex_m_target": "M55",
        },
        "verification": {
            "deserializer": "executorch.exir._serialize._program.deserialize_pte_binary",
            "status": "loaded_and_contract_checked",
        },
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(metadata, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--executorch-root",
        type=Path,
        default=DEFAULT_EXECUTORCH_ROOT,
    )
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    export(args.output_dir.resolve(), args.executorch_root.resolve())


if __name__ == "__main__":
    main()
