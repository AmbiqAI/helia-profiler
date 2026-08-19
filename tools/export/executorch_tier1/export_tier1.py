#!/usr/bin/env python3
"""Export the Tier-1 ns-ops comparison models for both kernel providers.

Two deterministic random-weight int8 models cover every Tier-1 op that
nsx-executorch PR #2 can lower to cortex_m_ns:: kernels:

- tier1: a channels_last conv trunk exercising hardswish, leaky_relu, sub
  and standalone relu on 16x32x32 tensors. mean is excluded here — the ns
  qualifier rejects channels_last input, and the portable mean.out kernel
  hard-requires default dim order (it fails Method::execute() with
  InvalidArgument on channels_last data), so a channels_last mean cannot
  run on either provider.
- tier1mean: a contiguous-layout micro model exercising mean(dim=(2,3),
  keepdim=True) alone, where both the ns lowering and the portable
  fallback are valid.

Each model is exported twice with nsx_cortex_m.export():

- kernel_provider=arm: Tier-1 ops stay portable aten fallbacks; the printed
  portable_ops list goes in the hpx config's engine.config.portable_ops.
- kernel_provider=ns: Tier-1 ops lower to cortex_m_ns:: kernels; the PTE
  requires an hpx run with engine.backend=ns and engine.config.ns_ops=true.

Run with the export venv and the pinned checkout:

  PYTHONPATH=<nsx-executorch>/external/executorch/src:<nsx-executorch>/aot \
    python export_tier1.py --output-dir <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import torch
from torch import nn

SEED = 20260819
CALIBRATION_SAMPLES = 16


class Tier1Net(nn.Module):
    """Conv trunk with the channels_last-safe Tier-1 ops on the datapath.

    The elementwise ops run on 16x32x32 int8 tensors (16,384 elements) so the
    portable-vs-ns per-operator gap is well above PMU noise.
    """

    def __init__(self) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(16, 16, 3, padding=1)
        self.conv2 = nn.Conv2d(16, 16, 3, padding=1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        a = self.conv1(x)
        a = torch.nn.functional.hardswish(a)
        b = self.conv2(a)
        b = torch.nn.functional.leaky_relu(b, 0.125)
        c = b - a
        return torch.relu(c)


class MeanNet(nn.Module):
    """Contiguous-layout global spatial mean; qualifies for cortex_m_ns."""

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x.mean(dim=(2, 3), keepdim=True)


@dataclass
class ModelSpec:
    name: str
    build: Callable[[], nn.Module]
    input_shape: list[int]
    output_shape: list[int]
    channels_last: bool
    required_ns_ops: set[str] = field(default_factory=set)


def _tier1() -> nn.Module:
    torch.manual_seed(SEED)
    model = Tier1Net().eval()
    for module in model.modules():
        if isinstance(module, nn.Conv2d):
            nn.init.kaiming_normal_(module.weight, mode="fan_out", nonlinearity="relu")
            if module.bias is not None:
                nn.init.zeros_(module.bias)
    return model


MODELS = [
    ModelSpec(
        name="tier1",
        build=_tier1,
        input_shape=[1, 16, 32, 32],
        output_shape=[1, 16, 32, 32],
        channels_last=True,
        required_ns_ops={
            "cortex_m_ns::quantized_hardswish",
            "cortex_m_ns::quantized_leaky_relu",
            "cortex_m_ns::quantized_sub",
            "cortex_m_ns::quantized_relu",
        },
    ),
    ModelSpec(
        name="tier1mean",
        build=lambda: MeanNet().eval(),
        input_shape=[1, 16, 32, 32],
        output_shape=[1, 16, 1, 1],
        channels_last=False,
        required_ns_ops={"cortex_m_ns::quantized_mean"},
    ),
]


def _tensors(spec: ModelSpec, seed: int, count: int) -> list[tuple[torch.Tensor, ...]]:
    torch.manual_seed(seed)
    samples = []
    for _ in range(count):
        tensor = torch.randn(*spec.input_shape)
        if spec.channels_last:
            tensor = tensor.to(memory_format=torch.channels_last)
        samples.append((tensor,))
    return samples


def _plan_facts(result) -> dict:
    program = result.executorch_program._emitter_output.program
    plan = program.execution_plan[0]
    operators = sorted(
        op.name + (f".{op.overload}" if op.overload else "") for op in plan.operators
    )
    buffer_sizes = list(plan.non_const_buffer_sizes)
    if len(buffer_sizes) != 2:
        raise RuntimeError(
            f"nsx-executorch requires exactly one planned buffer, got {buffer_sizes}"
        )
    op_names = [op.name + (f".{op.overload}" if op.overload else "") for op in plan.operators]
    instructions = []
    for chain_index, chain in enumerate(plan.chains):
        for instr_index, instruction in enumerate(chain.instructions):
            kind = type(instruction.instr_args).__name__
            label = (
                op_names[instruction.instr_args.op_index] if kind == "KernelCall" else kind
            )
            instructions.append({"id": f"c{chain_index}i{instr_index}", "op": label})
    return {
        "operators": operators,
        "instructions": instructions,
        "planned_arena_size": int(buffer_sizes[1]),
        "input_shape": list(plan.values[plan.inputs[0]].val.sizes),
        "output_shape": list(plan.values[plan.outputs[0]].val.sizes),
    }


def _export_one(spec: ModelSpec, provider: str, output_dir: Path) -> dict:
    from nsx_cortex_m import export

    result = export(
        spec.build(),
        _tensors(spec, SEED + 1, 1)[0:1][0],
        kernel_provider=provider,
        calibration_samples=_tensors(spec, SEED + 2, CALIBRATION_SAMPLES),
    )
    pte_path = output_dir / f"{spec.name}_{provider}.pte"
    result.write_pte(pte_path)
    facts = _plan_facts(result)

    # Serialized operator names carry overload suffixes (.out/.default);
    # compare on the base name.
    base_names = {name.rsplit(".", 1)[0] for name in facts["operators"]}
    ns_ops_in_pte = sorted(name for name in base_names if name.startswith("cortex_m_ns::"))
    if provider == "ns":
        missing = spec.required_ns_ops - set(ns_ops_in_pte)
        if missing:
            raise RuntimeError(f"{spec.name}: ns export failed to lower: {missing}")
    elif ns_ops_in_pte:
        raise RuntimeError(
            f"{spec.name}: arm export must not contain cortex_m_ns ops: {ns_ops_in_pte}"
        )

    # engine.config.portable_ops must register every serialized operator
    # outside the cortex_m/cortex_m_ns namespaces. The ExportResult fallback
    # report only tracks NS-candidate ops, so derive the list from the PTE.
    portable_ops = sorted(
        name
        for name in facts["operators"]
        if not name.startswith(("cortex_m::", "cortex_m_ns::"))
    )

    entry = {
        "pte": pte_path.name,
        "sha256": hashlib.sha256(pte_path.read_bytes()).hexdigest(),
        "byte_size": pte_path.stat().st_size,
        "edge_ops": result.edge_ops,
        "reported_portable_fallback_ops": result.portable_fallback_ops,
        "portable_ops": portable_ops,
        **facts,
    }
    print(f"[{spec.name}/{provider}] wrote {pte_path}")
    print(f"[{spec.name}/{provider}] operators: {facts['operators']}")
    print(f"[{spec.name}/{provider}] portable_ops for hpx config: {portable_ops}")
    print(f"[{spec.name}/{provider}] planned_arena_size: {facts['planned_arena_size']}")
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {"seed": SEED, "torch_version": torch.__version__, "models": {}}
    for spec in MODELS:
        elem_count_in = 1
        for dim in spec.input_shape:
            elem_count_in *= dim
        elem_count_out = 1
        for dim in spec.output_shape:
            elem_count_out *= dim
        manifest["models"][spec.name] = {
            "input_shape": spec.input_shape,
            "output_shape": spec.output_shape,
            "channels_last": spec.channels_last,
            "input_size_bytes": 4 * elem_count_in,
            "output_size_bytes": 4 * elem_count_out,
            "providers": {
                provider: _export_one(spec, provider, args.output_dir)
                for provider in ("arm", "ns")
            },
        }

    manifest_path = args.output_dir / "tier1_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()
