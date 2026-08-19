#!/usr/bin/env python3
"""Build the Tier-1 arm-vs-ns comparison report from paired hpx runs.

`hpx compare` deliberately refuses cross-PTE comparisons (model SHA gate);
the arm and ns Tier-1 PTEs are different serializations of the same source
model, so this tool aligns their per-instruction PMU records via the
instruction maps recorded in tier1_manifest.json and reports the deltas.

Usage:
  python compare_tier1.py --manifest examples/models/tier1/tier1_manifest.json \
      --model tier1 --baseline results/tier1_arm --candidate results/tier1_ns
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _layers(run_dir: Path) -> list[dict]:
    with open(run_dir / "detailed" / "profile_cpu.csv") as handle:
        return list(csv.DictReader(handle))


def _clean_cycles(run_dir: Path) -> int:
    summary = json.loads((run_dir / "summary.json").read_text())
    return int(summary["latency"]["device_clean_infer_avg_cycles"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="arm run dir")
    parser.add_argument("--candidate", type=Path, required=True, help="ns run dir")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text())["models"][args.model]
    maps = {
        provider: {i["id"]: i["op"] for i in manifest["providers"][provider]["instructions"]}
        for provider in ("arm", "ns")
    }

    rows = {}
    for provider, run_dir in (("arm", args.baseline), ("ns", args.candidate)):
        for record in _layers(run_dir):
            instr = record["op"].split(":")[-1]
            op = maps[provider].get(instr, instr)
            rows.setdefault(provider, []).append(
                {"id": instr, "op": op, "cycles": float(record["ARM_PMU_CPU_CYCLES"])}
            )

    print(f"## {args.model}: per-instruction CPU cycles\n")
    print("| # | arm op | arm cycles | ns op | ns cycles |")
    print("|---|---|---:|---|---:|")
    length = max(len(rows["arm"]), len(rows["ns"]))
    for index in range(length):
        arm = rows["arm"][index] if index < len(rows["arm"]) else None
        ns = rows["ns"][index] if index < len(rows["ns"]) else None
        print(
            f"| {index} "
            f"| {arm['op'] if arm else ''} | {arm['cycles']:,.0f}" if arm else f"| {index} | | ",
            end="",
        )
        print(f" | {ns['op'] if ns else ''} | {ns['cycles']:,.0f} |" if ns else " | | |")

    arm_total = sum(r["cycles"] for r in rows["arm"])
    ns_total = sum(r["cycles"] for r in rows["ns"])
    arm_clean = _clean_cycles(args.baseline)
    ns_clean = _clean_cycles(args.candidate)
    print("\n| metric | arm | ns | delta |")
    print("|---|---:|---:|---:|")
    print(
        f"| per-layer sum | {arm_total:,.0f} | {ns_total:,.0f} "
        f"| {100 * (ns_total - arm_total) / arm_total:+.1f}% |"
    )
    print(
        f"| clean E2E cycles | {arm_clean:,} | {ns_clean:,} "
        f"| {100 * (ns_clean - arm_clean) / arm_clean:+.1f}% |"
    )


if __name__ == "__main__":
    main()
