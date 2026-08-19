#!/usr/bin/env python3
"""Verify which CMSIS-NN provider and kernels are linked into an hpx
ExecuTorch firmware ELF.

Usage:
  python tools/verify_executorch_kernels.py --provider arm <elf>
  python tools/verify_executorch_kernels.py --provider ns --ns-ops <elf>

Checks (via arm-none-eabi-nm):
- ns provider evidence: the ns-cmsis-nn v7.29.2 weight-sum ABI symbols
  (arm_convolve_weight_sum / arm_convolve_s8_get_weights_sum_size) that do
  not exist in stock Arm CMSIS-NN.
- cortex_m_ns:: Tier-1 kernels (mangled names contain "cortex_m_ns") —
  required with --ns-ops, forbidden otherwise.
- Portable ATen fallback kernels (torch::executor::native::) — expected
  whenever the PTE keeps portable ops.

Exit code 0 iff every expectation for the declared build flavor holds.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

NS_ABI_SYMBOLS = ("arm_convolve_weight_sum", "arm_convolve_s8_get_weights_sum_size")


def _symbols(elf: Path, nm: str) -> list[str]:
    out = subprocess.run(
        [nm, "--defined-only", str(elf)], check=True, capture_output=True, text=True
    ).stdout
    return [line.split()[-1] for line in out.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("--provider", choices=("arm", "ns"), required=True)
    parser.add_argument("--ns-ops", action="store_true")
    parser.add_argument(
        "--expect-portable",
        action="store_true",
        help="Require the selective portable-op registration table (set when the PTE keeps aten:: fallbacks)",
    )
    parser.add_argument("--nm", default="arm-none-eabi-nm")
    args = parser.parse_args()

    nm = shutil.which(args.nm)
    if nm is None:
        parser.error(
            f"{args.nm!r} not found on PATH; install the Arm GNU toolchain or "
            "pass --nm /path/to/arm-none-eabi-nm"
        )
    if not args.elf.is_file():
        parser.error(f"ELF not found: {args.elf}")

    syms = _symbols(args.elf, nm)
    ns_abi = sorted({s for s in syms if any(s.startswith(k) for k in NS_ABI_SYMBOLS)})
    cortex_m_ns = sorted({s for s in syms if "cortex_m_ns" in s})
    # Named native symbols disappear when size optimization localizes the
    # kernel bodies into the codegen registration lambdas, so also accept the
    # registration table itself as portable-kernel evidence.
    portable = sorted(
        {
            s
            for s in syms
            if "torch8executor6native" in s
            or "RegisterCodegenUnboxedKernels" in s
            or "kernels_to_register" in s
        }
    )
    cmsis = sorted({s for s in syms if s.startswith("arm_convolve_wrapper_s8")})

    print(f"ELF: {args.elf}")
    print(f"declared flavor: provider={args.provider} ns_ops={args.ns_ops}")
    print(f"ns weight-sum ABI symbols ({len(ns_abi)}): {ns_abi}")
    print(f"cortex_m_ns kernel symbols ({len(cortex_m_ns)}): {cortex_m_ns[:12]}")
    print(f"portable aten kernel symbols ({len(portable)}): {portable[:12]}")
    print(f"conv wrapper symbols: {cmsis}")

    failures: list[str] = []
    if args.provider == "ns":
        if not ns_abi:
            failures.append("ns provider build lacks the ns-cmsis-nn weight-sum ABI symbols")
    else:
        if ns_abi:
            failures.append("arm provider build unexpectedly links ns-cmsis-nn ABI symbols")
    if args.ns_ops:
        if not cortex_m_ns:
            failures.append("ns_ops build lacks cortex_m_ns:: kernel symbols")
    else:
        if cortex_m_ns:
            failures.append("build without ns_ops unexpectedly links cortex_m_ns:: kernels")
    if args.expect_portable and not portable:
        failures.append("PTE needs portable aten fallbacks but no registration table is linked")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: linked kernels match the declared build flavor")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
