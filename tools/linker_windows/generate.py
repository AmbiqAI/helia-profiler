"""Project upstream script/header extraction into HPX classification windows."""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from helia_profiler.platform.linker.files import source_sha256

_ROOT = Path(__file__).resolve().parents[2]
_SOCS = (
    "apollo3p",
    "apollo4p",
    "apollo4l",
    "apollo510",
    "apollo510b",
    "apollo5b",
    "apollo330P",
    "apollo510L",
    "atomiq110",
)
_SCRIPT_REGIONS = {
    "ROMEM": "MRAM",
    "MCU_MRAM": "MRAM",
    "TCM": "DTCM",
    "MCU_TCM": "DTCM",
    "MCU_ITCM": "ITCM",
    "RWMEM": "SRAM",
    "SHARED_SRAM": "SRAM",
}


def _revision(root: Path, expected: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.stdout.strip() != expected:
        raise ValueError(f"{root}: expected source commit {expected}")
    dirty = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if dirty.stdout.strip():
        raise ValueError(f"{root}: source checkout has tracked modifications")


def _span(origin: int, length: int) -> list[int]:
    if origin < 0 or length < 0:
        raise ValueError("Source ranges must have nonnegative origins and lengths")
    return [origin, length]


def _aperture(
    soc: str,
    region: str,
    app: dict[str, Any],
    registers: dict[str, int],
    flash: dict[str, int],
) -> tuple[list[int], str]:
    origin, length = app["origin"], app["length"]
    if region == "MRAM":
        if "MRAM_BASEADDR" in registers:
            end = registers["MRAM_BASEADDR"] + registers["MRAM_MAX_SIZE"]
        elif "RRAM_BASEADDR" in registers:
            end = registers["RRAM_BASEADDR"] + registers["RRAM_MAX_SIZE"]
        else:
            end = flash["AM_HAL_FLASH_ADDR"] + flash["AM_HAL_FLASH_TOTAL_SIZE"]
        return _span(origin, end - origin), "linker-app-origin"
    candidates = {
        "DTCM": ("DTCM", "TCM"),
        "ITCM": ("ITCM",),
        "SRAM": ("SSRAM0", "SSRAM"),
    }[region]
    for name in candidates:
        if f"{name}_BASEADDR" in registers and f"{name}_MAX_SIZE" in registers:
            return _span(
                registers[f"{name}_BASEADDR"], registers[f"{name}_MAX_SIZE"]
            ), "hardware-aperture"
    if soc == "apollo3p" and region == "SRAM":
        base = registers["TCM_BASEADDR"] + registers["TCM_MAX_SIZE"]
        end = flash["AM_HAL_FLASH_SRAM_ADDR"] + flash["AM_HAL_FLASH_SRAM_SIZE"]
        return _span(base, end - base), "hardware-aperture"
    if soc == "atomiq110" and region == "ITCM":
        return _span(origin, length), "linker-script"
    raise ValueError(f"{soc}/{region}: no source-backed classification aperture")


def generate(sdk_root: Path, extractor_root: Path) -> dict[str, Any]:
    baseline = json.loads(
        (_ROOT / "src/helia_profiler/data/compatibility-baseline-v1.json").read_text(
            encoding="utf-8"
        )
    )
    sdk_commit = baseline["projects"]["nsx-ambiq-sdk"]["ref"]
    extractor_pin = json.loads(
        Path(__file__).with_name("extractor.json").read_text(encoding="utf-8")
    )
    _revision(sdk_root, sdk_commit)
    _revision(extractor_root, extractor_pin["commit"])
    sys.path.insert(0, str(extractor_root / "scripts"))
    cmake = importlib.import_module("snapshot_extractor.cmake")
    linker = importlib.import_module("snapshot_extractor.linker")
    cdefines = importlib.import_module("snapshot_extractor.cdefines")
    sdk = importlib.import_module("snapshot_extractor.sdk")
    result: dict[str, Any] = {
        "schema": "hpx.linker-windows",
        "schema_version": 1,
        "sdk_commit": sdk_commit,
        "extractor": {
            **extractor_pin,
            "sha256": {
                name: source_sha256(extractor_root / "scripts/snapshot_extractor" / name)
                for name in extractor_pin["files"]
            },
        },
        "socs": {},
    }
    for soc in _SOCS:
        descriptor = f"cmake/socs/{soc}.cmake"
        register_path = sdk._registers_path(soc)
        register_defs = cdefines.collect((sdk_root / register_path).read_text(encoding="utf-8"))
        wanted = [name for name in register_defs if name.endswith(("_BASEADDR", "_MAX_SIZE"))]
        registers = cdefines.Defines(register_defs).integers(wanted)
        inputs = [
            descriptor,
            f"cmake/socs/facts/{soc}.cmake",
            register_path,
            "cmake/nsx_toolchain_flags.cmake",
        ]
        flash = {}
        if soc in sdk.FLASH_DEFINES:
            flash_path = sdk._flash_path(soc)
            inputs.append(flash_path)
            defines = cdefines.Defines(
                register_defs, cdefines.collect((sdk_root / flash_path).read_text(encoding="utf-8"))
            )
            flash = defines.integers(sdk.FLASH_DEFINES[soc])
        selections = cmake.default_scripts((sdk_root / descriptor).read_text(encoding="utf-8"))
        entry: dict[str, Any] = {
            "inputs": {p: source_sha256(sdk_root / p) for p in sorted(inputs)},
            "families": {},
        }
        for toolchain, selected in sorted(selections.items()):
            script = selected.removeprefix("${NSX_ROOT}/")
            path = sdk_root / script
            parsed = (
                linker.gnu_memory(path.read_text(encoding="utf-8"))
                if toolchain == "gcc"
                else linker.scatter_regions(path.read_text(encoding="utf-8"))
            )
            regions = []
            for name, values in parsed.items():
                if name not in _SCRIPT_REGIONS:
                    if name not in {"ARM_LIB_STACK", "ARM_LIB_HEAP", "STACKMEM"}:
                        raise ValueError(f"{soc}: unmapped script region {name}")
                    continue
                region = _SCRIPT_REGIONS[name]
                aperture, provenance = _aperture(soc, region, values, registers, flash)
                app = _span(values["origin"], values["length"])
                if app[0] < aperture[0] or sum(app) > sum(aperture):
                    raise ValueError(f"{soc}/{region}: application extent is outside aperture")
                stack = parsed.get("ARM_LIB_STACK") or parsed.get("STACKMEM")
                fixed_stack = 0
                if stack and aperture[0] <= stack["origin"] < sum(aperture):
                    stack_end = stack["origin"] + stack["length"]
                    if stack_end > sum(aperture) or not (
                        stack_end <= app[0] or stack["origin"] >= sum(app)
                    ):
                        raise ValueError(f"{soc}/{region}: fixed stack overlaps the app extent")
                    fixed_stack = stack["length"]
                regions.append(
                    {
                        "region": region,
                        "window": aperture,
                        "app_window": app,
                        "window_provenance": provenance,
                        "fixed_stack_bytes": fixed_stack,
                    }
                )
            entry["families"]["gnu" if toolchain == "gcc" else "armlink"] = {
                "script": script,
                "sha256": source_sha256(path),
                "regions": regions,
            }
        if set(entry["families"]) != {"gnu", "armlink"}:
            raise ValueError(f"{soc}: both default link families are required")
        result["socs"][soc] = entry
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--extractor-root", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=_ROOT / "src/helia_profiler/data/linker-windows-v1.json"
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        text = (
            json.dumps(generate(args.sdk_root, args.extractor_root), sort_keys=True, indent=2)
            + "\n"
        )
        if args.check:
            if not args.output.is_file() or args.output.read_text(encoding="utf-8") != text:
                parser.error(f"{args.output}: generated linker windows differ; regenerate")
        else:
            args.output.write_text(text, encoding="utf-8", newline="\n")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))
    return 0
