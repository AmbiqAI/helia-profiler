#!/usr/bin/env python3
"""Lower the INT8 MLPerf Tiny .pt2 fixtures once per nsx kernel provider.

export_pte.py lowers the .pt2 fixtures with the stock Cortex-M pipeline, so
its PTEs contain only cortex_m:: ops. This wrapper loads the same INT8
.pt2 ExportedProgram fixtures (see make_pt2.py) and lowers with the
nsx-executorch AOT (helia-torch) pass managers instead, once per provider:

- kernel_provider=arm reproduces the stock flow (byte-compatible operator
  contract with the existing fixtures);
- kernel_provider=ns additionally lowers Tier-1 ops (sub, hardswish, mean,
  standalone relu/relu6/hardtanh/clamp, leaky_relu) to cortex_m_ns::.

The .pt2 fixtures are already quantized, so ``nsx_cortex_m.export()`` takes
its pre-quantized path: no re-quantization, straight to kernel matching.
Note the ns quantizer annotations are NOT present in a .pt2 quantized by
make_pt2.py's stock CortexMQuantizer, so provider=ns Tier-1 coverage is
limited to what the stock annotations allow; none of the four MLPerf Tiny
fixtures currently exercises a Tier-1 op either way.

Run with the export venv:

  PYTHONPATH=<nsx-executorch>/external/executorch/src:<nsx-executorch>/aot \
    python export_nsx_aot.py --output-dir <dir> [--models ic,vww,...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch

from common import FIXTURE_ROOT, MODELS, load_quantized_pt2, parse_model_keys


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

    from nsx_cortex_m import export as nsx_export

    manifest: dict = {"torch_version": torch.__version__, "models": {}}
    for key in parse_model_keys(args.models):
        spec = MODELS[key]
        entry: dict = {"pt2": spec.pt2_path, "providers": {}}
        quantized_exported = load_quantized_pt2(torch, FIXTURE_ROOT / spec.pt2_path)
        example_inputs = (quantized_exported.example_inputs[0][0],)
        for provider in ("arm", "ns"):
            result = nsx_export(
                quantized_exported,
                example_inputs,
                kernel_provider=provider,
                int8_io=True,
            )
            pte_path = args.output_dir / f"{key}_{provider}.pte"
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
            print(f"[{key}/{provider}] {pte_path.name}: "
                  f"planned={facts['planned_arena_size']} portable={portable_ops} ns={ns_ops}")
        manifest["models"][key] = entry

    manifest_path = args.output_dir / "nsx_aot_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")


if __name__ == "__main__":
    main()
