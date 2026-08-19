#!/usr/bin/env python3
"""Re-export the MLPerf Tiny fixture models through nsx_cortex_m.export().

The per-model scripts in this directory (ad/ic/kws/vww.py) inline the stock
Cortex-M pipeline, so their PTEs contain only cortex_m:: ops. This wrapper
reuses their deterministic model builders but lowers through the
nsx-executorch AOT package instead, once per kernel provider:

- kernel_provider=arm reproduces the stock flow (byte-compatible operator
  contract with the existing fixtures);
- kernel_provider=ns additionally lowers Tier-1 ops (sub, hardswish, mean,
  standalone relu/relu6/hardtanh/clamp, leaky_relu) to cortex_m_ns::.

Run with the export venv:

  PYTHONPATH=<nsx-executorch>/external/executorch/src:<nsx-executorch>/aot \
    python export_nsx_aot.py --output-dir <dir> [--models ic,vww,...]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import sys
from pathlib import Path

import torch


def _load(module_name: str):
    sys.path.insert(0, str(Path(__file__).parent))
    return importlib.import_module(module_name)


def _build(module_name: str):
    """Return (model, example_inputs, calibration, channels_last)."""
    module = _load(module_name)
    torch.manual_seed(module.SEED)
    if module_name == "ad":
        model = module._make_model().eval()
        calibration = list(module._calibration_samples())
        return model, calibration[0], calibration, False
    if module_name == "ic":
        module._seed_everything(torch)
        model = module._make_model(torch).eval().to(memory_format=torch.channels_last)
        generator = torch.Generator().manual_seed(module.SEED)
        calibration = [
            (
                torch.rand(module.INPUT_SHAPE, generator=generator).to(
                    memory_format=torch.channels_last
                ),
            )
            for _ in range(module.CALIBRATION_SAMPLES)
        ]
        return model, calibration[0], calibration, True
    if module_name == "kws":
        model = module._make_model(torch).eval().to(memory_format=torch.channels_last)
        calibration = [(sample,) for sample in module._calibration_data(torch)]
        return model, calibration[0], calibration, True
    if module_name == "vww":
        model = module._build_model().eval().to(memory_format=torch.channels_last)
        generator = torch.Generator().manual_seed(module.SEED)
        calibration = [
            (
                torch.rand(module.INPUT_SHAPE, generator=generator).to(
                    memory_format=torch.channels_last
                ),
            )
            for _ in range(8)
        ]
        return model, calibration[0], calibration, True
    raise SystemExit(f"unknown model {module_name}")


def _plan_facts(result) -> dict:
    program = result.executorch_program._emitter_output.program
    plan = program.execution_plan[0]
    op_names = [op.name + (f".{op.overload}" if op.overload else "") for op in plan.operators]
    instructions = []
    for chain_index, chain in enumerate(plan.chains):
        for instr_index, instruction in enumerate(chain.instructions):
            kind = type(instruction.instr_args).__name__
            label = op_names[instruction.instr_args.op_index] if kind == "KernelCall" else kind
            instructions.append({"id": f"c{chain_index}i{instr_index}", "op": label})
    buffer_sizes = list(plan.non_const_buffer_sizes)
    return {
        "operators": sorted(set(op_names)),
        "instructions": instructions,
        "planned_arena_size": int(buffer_sizes[1]) if len(buffer_sizes) == 2 else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--models", default="ad,ic,kws,vww")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    from nsx_cortex_m import export

    manifest: dict = {"torch_version": torch.__version__, "models": {}}
    for module_name in args.models.split(","):
        entry: dict = {"providers": {}}
        for provider in ("arm", "ns"):
            model, example, calibration, _ = _build(module_name)
            result = export(
                model, example, kernel_provider=provider, calibration_samples=calibration
            )
            pte_path = args.output_dir / f"{module_name}_{provider}.pte"
            result.write_pte(pte_path)
            facts = _plan_facts(result)
            portable_ops = sorted(
                name
                for name in facts["operators"]
                if not name.startswith(("cortex_m::", "cortex_m_ns::"))
            )
            ns_ops = sorted(
                name for name in facts["operators"] if name.startswith("cortex_m_ns::")
            )
            entry["providers"][provider] = {
                "pte": pte_path.name,
                "sha256": hashlib.sha256(pte_path.read_bytes()).hexdigest(),
                "byte_size": pte_path.stat().st_size,
                "portable_ops": portable_ops,
                "cortex_m_ns_ops": ns_ops,
                **facts,
            }
            print(f"[{module_name}/{provider}] {pte_path.name}: "
                  f"planned={facts['planned_arena_size']} portable={portable_ops} ns={ns_ops}")
        manifest["models"][module_name] = entry

    manifest_path = args.output_dir / "nsx_aot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()
