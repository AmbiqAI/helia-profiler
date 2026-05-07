"""Firmware generation — NSX app scaffolding for the profiler.

This module provides the interface between the pipeline stages and the
low-level firmware template rendering + NSX build system.  Each function
receives a ``PipelineContext`` and operates on the fields set by prior stages.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2

from .. import nsx as nsx_cli
from ..counters import (
    CounterPass,
    plan_passes,
    resolve_counters,
    resolve_legacy_presets,
)
from ..engines import EngineType
from ..errors import BuildError, FirmwareError
from ..platform import PmuTier, get_soc_for_board
from ..placement import ArenaRole, Placement

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# Toolchain mapping: config names → nsx CLI --toolchain values
# ---------------------------------------------------------------------------
_TOOLCHAIN_MAP: dict[str, str] = {
    "arm-none-eabi-gcc": "gcc",
    "gcc": "gcc",
    "armclang": "armclang",
    "atfe": "atfe",
}


def _nsx_toolchain(toolchain: str) -> str | None:
    """Convert a config toolchain name to the ``nsx --toolchain`` value.

    Returns *None* for the default (GCC) so the flag is omitted.
    """
    nsx_tc = _TOOLCHAIN_MAP.get(toolchain, toolchain)
    return nsx_tc if nsx_tc != "gcc" else None


# ---------------------------------------------------------------------------
# SDK tier → module set mapping
# ---------------------------------------------------------------------------
_SDK_MODULES: dict[str, list[str]] = {
    "r3": [
        "nsx-cmsis-core",
        "nsx-ambiqsuite-r3",
        "nsx-ambiq-hal-r3",
        "nsx-ambiq-bsp-r3",
    ],
    "r4": [
        "nsx-cmsis-core",
        "nsx-ambiqsuite-r4",
        "nsx-ambiq-hal-r4",
        "nsx-ambiq-bsp-r4",
    ],
    "r5": [
        "nsx-cmsis-core",
        "nsx-ambiqsuite-r5",
        "nsx-ambiq-hal-r5",
        "nsx-ambiq-bsp-r5",
    ],
}

# Common modules every profiler app needs (order matters for CMake)
_COMMON_MODULES = [
    "nsx-soc-hal",
    "nsx-cmsis-startup",
    # board module inserted dynamically
    "nsx-core",
    "nsx-harness",
    "nsx-utils",
    "nsx-power",
    "nsx-perf",
    "nsx-pmu-armv8m",
    "nsx-tooling",
]


def _board_module_name(board: str) -> str:
    """Derive the NSX board module name from a board name."""
    return f"nsx-board-{board.replace('_', '-')}"


def _resolve_module_list(board: str, sdk_tier: str) -> list[str]:
    """Build the ordered module list for a profiler app."""
    sdk_mods = _SDK_MODULES.get(sdk_tier)
    if sdk_mods is None:
        raise FirmwareError(
            f"Unknown SDK tier '{sdk_tier}'",
            hint=f"Known tiers: {', '.join(_SDK_MODULES)}",
        )
    modules = list(sdk_mods)
    board_mod = _board_module_name(board)
    soc = get_soc_for_board(board)
    # Insert board + common modules after SDK modules
    for mod in _COMMON_MODULES:
        if mod == "nsx-pmu-armv8m" and soc.pmu_tier is not PmuTier.ARMV8M_PMU:
            continue
        if mod == "nsx-soc-hal":
            modules.append(mod)
            modules.append("nsx-cmsis-startup")
            modules.append(board_mod)
        elif mod in ("nsx-cmsis-startup",):
            continue  # already added above
        else:
            modules.append(mod)
    return modules


# ---------------------------------------------------------------------------
# PMU preset mapping (legacy — used only for backward-compat Init() path)
# ---------------------------------------------------------------------------
_PMU_PRESET_MAP: dict[str, str] = {
    "basic_cpu": "NS_PMU_PRESET_BASIC_CPU",
    "memory": "NS_PMU_PRESET_MEMORY",
    "mve": "NS_PMU_PRESET_MVE",
    "ml_default": "NS_PMU_PRESET_ML_DEFAULT",
}


def _resolve_pmu_passes(config: Any) -> list[dict[str, Any]]:
    """Resolve profiling config into firmware pass descriptors.

    If the new ``pmu_counters`` field is set, resolve and plan passes from
    the counter registry.  Otherwise fall back to legacy preset behaviour.

    Each returned dict has:
      - ``name``          — pass name for the SWO protocol
      - ``custom``        — True if using explicit event IDs
      - ``event_ids``     — list of hex-literal strings (custom only)
      - ``num_counters``  — number of counters (custom only)
      - ``c_enum``        — C preset enum name (legacy only)
      - ``group``         — compute-unit group name
    """
    profiling = config.profiling

    # --- New path: explicit counter selection ---
    if profiling.pmu_counters is not None:
        counters = resolve_counters(profiling.pmu_counters)
        passes = plan_passes(counters)
        return [
            {
                "name": p.name,
                "custom": True,
                "event_ids": [f"0x{c.event_id:04X}" for c in p.counters],
                "num_counters": len(p.counters),
                "c_enum": None,
                "group": p.group,
            }
            for p in passes
        ]

    # --- Legacy path: named presets ---
    result: list[dict[str, Any]] = []
    for preset_name in profiling.pmu_presets:
        c_enum = _PMU_PRESET_MAP.get(preset_name, "NS_PMU_PRESET_ML_DEFAULT")
        result.append({
            "name": preset_name,
            "custom": False,
            "event_ids": [],
            "num_counters": 4,
            "c_enum": c_enum,
            "group": preset_name,
        })
    if not result:
        result = [{
            "name": "ml_default",
            "custom": False,
            "event_ids": [],
            "num_counters": 4,
            "c_enum": "NS_PMU_PRESET_ML_DEFAULT",
            "group": "ml_default",
        }]
    return result


# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _find_segger_rtt_dir() -> Path:
    """Locate the SEGGER RTT source directory.

    Search order:
      1. ``SEGGER_RTT_PATH`` environment variable
      2. Known paths relative to the helia-profiler source tree

    Returns the directory containing ``RTT/`` and ``Config/`` subdirs.
    """
    # 1. Explicit environment variable
    env_path = os.environ.get("SEGGER_RTT_PATH")
    if env_path:
        p = Path(env_path)
        if (p / "RTT" / "SEGGER_RTT.c").exists():
            return p
        raise FirmwareError(
            f"SEGGER_RTT_PATH={env_path} does not contain RTT/SEGGER_RTT.c",
            hint="Set SEGGER_RTT_PATH to the root dir containing RTT/ and Config/ subdirs.",
        )

    # 2. Relative to helia-profiler source (monorepo layout)
    try:
        import helia_profiler as _hp

        pkg_file = Path(_hp.__file__).resolve()
        # src/helia_profiler/__init__.py → up 3 levels → helia-profiler/
        hp_root = pkg_file.parents[2]
        candidates = [
            # neuralspot/experiments/runtime_benchmarks/extern/SEGGER_RTT/R7.70a
            hp_root.parent / "experiments" / "runtime_benchmarks" / "extern" / "SEGGER_RTT" / "R7.70a",
            # legacy path (pre-rename)
            hp_root.parent / "benchmarks" / "runtime_benchmarks" / "extern" / "SEGGER_RTT" / "R7.70a",
            # neuralspot/nsx-modules/nsx-ambiqsuite-r4/sdk/third_party/SEGGER/SEGGER_RTT_V680a
            hp_root.parent / "nsx-modules" / "nsx-ambiqsuite-r4" / "sdk" / "third_party" / "SEGGER" / "SEGGER_RTT_V680a",
        ]
        for c in candidates:
            if (c / "RTT" / "SEGGER_RTT.c").exists():
                return c
    except Exception:
        pass

    raise FirmwareError(
        "SEGGER RTT source files not found",
        hint=(
            "Set SEGGER_RTT_PATH to the RTT source directory "
            "(the folder containing RTT/ and Config/ subdirs)."
        ),
    )


def _copy_segger_rtt(dest_dir: Path) -> None:
    """Copy SEGGER RTT source files into *dest_dir*/rtt/."""
    rtt_root = _find_segger_rtt_dir()
    rtt_dest = dest_dir / "rtt"
    rtt_dest.mkdir(parents=True, exist_ok=True)

    # RTT source + header
    for name in ("SEGGER_RTT.c", "SEGGER_RTT.h"):
        src = rtt_root / "RTT" / name
        if src.exists():
            shutil.copy2(src, rtt_dest / name)

    # Config header — nested in Config/ subdir
    config_dest = rtt_dest / "Config"
    config_dest.mkdir(parents=True, exist_ok=True)
    conf_src = rtt_root / "Config" / "SEGGER_RTT_Conf.h"
    if conf_src.exists():
        shutil.copy2(conf_src, config_dest / "SEGGER_RTT_Conf.h")

    log.info("Copied SEGGER RTT source from %s", rtt_root)


def _model_to_header(model_path: Path, weights_region: str = "mram") -> str:
    """Convert a .tflite model to a C header (xxd-style byte array).

    ``weights_region`` selects the section attribute applied to
    ``model_data[]``:

    * ``mram`` (default) — ``static const`` (rodata, stays in flash/MRAM).
    * ``tcm`` — ``NSX_MEM_FAST static`` (loaded into DTCM at boot).
    * ``sram`` — ``NSX_MEM_SRAM static`` (loaded into shared SRAM at boot).

    For TCM/SRAM placement we drop ``const`` because NSX initialises these
    sections by copying from NVM at boot, which requires writable storage.
    """
    data = model_path.read_bytes()

    if weights_region == "tcm":
        decl = "NSX_MEM_FAST alignas(16) static unsigned char model_data[] = {"
        include_nsx = True
    elif weights_region == "sram":
        decl = "NSX_MEM_SRAM alignas(16) static unsigned char model_data[] = {"
        include_nsx = True
    else:  # mram / default
        decl = "alignas(16) static const unsigned char model_data[] = {"
        include_nsx = False

    lines = [
        "// Auto-generated by heliaPROFILER — do not edit.",
        f"// Source: {model_path.name}",
        f"// Placement: {weights_region}",
    ]
    if include_nsx:
        lines.append('#include "nsx_mem.h"')
    lines.append(decl)
    for i in range(0, len(data), 12):
        chunk = data[i : i + 12]
        hex_vals = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_vals},")
    lines.append("};")
    if weights_region in ("tcm", "sram"):
        lines.append(f"static unsigned int model_data_len = {len(data)};")
    else:
        lines.append(f"static const unsigned int model_data_len = {len(data)};")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_app(ctx: PipelineContext) -> Path:
    """Render firmware templates into an NSX-compatible profiler app.

    Returns the path to the generated app directory inside ``ctx.work_dir``.
    """
    assert ctx.soc is not None
    assert ctx.board is not None
    assert ctx.engine_artifacts is not None

    app_dir = ctx.work_dir / "profiler_app"
    app_dir.mkdir(parents=True, exist_ok=True)

    config = ctx.config
    soc = ctx.soc
    board = ctx.board
    artifacts = ctx.engine_artifacts
    tvars = artifacts.template_vars
    weights_region = ctx.weights_region or Placement.MRAM
    arena_region = ctx.arena_region or Placement.TCM

    # --- Resolve module list ---
    mod_names = _resolve_module_list(board.name, soc.sdk_tier)

    # Add nsx-usb module when using USB CDC transport
    transport = config.target.transport
    if transport == "usb_cdc" and "nsx-usb" not in mod_names:
        mod_names.append("nsx-usb")

    # Add nsx-peripherals module when using PSRAM (for weights or arena)
    psram_needed = arena_region is Placement.PSRAM or weights_region is Placement.PSRAM
    if psram_needed and "nsx-peripherals" not in mod_names:
        mod_names.append("nsx-peripherals")

    # Build module descriptors (name + local flag)
    modules: list[dict[str, object]] = [{"name": m, "local": False} for m in mod_names]

    # Append engine-provided modules (e.g. nsx-heliart) as local
    for extra_mod in artifacts.extra_modules:
        if extra_mod.name not in mod_names:
            modules.append({"name": extra_mod.name, "local": True})

    log.info("NSX modules: %s", ", ".join(m["name"] for m in modules))  # type: ignore[arg-type]

    # Engine identity flows through the typed EngineArtifacts field.
    # Templates receive the canonical hyphen-form string (StrEnum value).
    engine_type = artifacts.engine_type

    # --- nsx.yml ---
    (app_dir / "nsx.yml").write_text(
        _jinja_env.get_template("nsx.yml.j2").render(
            board=board.name,
            soc=soc.name,
            toolchain=config.target.toolchain,
            modules=modules,
        )
    )

    # --- cmake/nsx/modules.cmake ---
    cmake_nsx_dir = app_dir / "cmake" / "nsx"
    cmake_nsx_dir.mkdir(parents=True, exist_ok=True)
    (cmake_nsx_dir / "modules.cmake").write_text(
        _jinja_env.get_template("modules.cmake.j2").render(modules=modules)
    )

    # --- CMakeLists.txt (engine-aware) ---
    (app_dir / "CMakeLists.txt").write_text(
        _jinja_env.get_template("CMakeLists.txt.j2").render(
            board=board.name,
            engine_type=engine_type,
            cmake_vars=artifacts.cmake_vars,
            aot_cmake_target=tvars.get("aot_cmake_target", ""),
            transport=transport,
            model_location=config.model.model_location,
            arena_region=arena_region,
            weights_region=weights_region,
            has_full_pmu=soc.has_full_pmu,
        )
    )

    # --- Source files ---
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # --- Copy SEGGER RTT source when using RTT transport ---
    if transport == "rtt":
        _copy_segger_rtt(src_dir)

    # PMU preset
    first_preset = config.profiling.pmu_presets[0] if config.profiling.pmu_presets else "ml_default"
    pmu_preset_c = _PMU_PRESET_MAP.get(first_preset, "NS_PMU_PRESET_ML_DEFAULT")

    # Build pass list for multi-pass firmware loop
    pmu_passes = _resolve_pmu_passes(config)

    # Arena size: use configured value or default 256KB
    arena_size = config.model.arena_size or (256 * 1024)

    # External power sync
    power_sync_enabled = config.power.enabled and config.power.mode == "external"
    sync_gpio_pin = config.power.sync_gpio_pin

    # --- Heartbeat template vars (shared across engines) ---
    hb = config.target.heartbeat
    heartbeat_vars = {
        "heartbeat_enabled": hb.enabled,
        "heartbeat_every_n_ops": hb.every_n_ops if hb.enabled else 0,
        "heartbeat_every_ms": hb.every_ms if hb.enabled else 0,
    }

    extreme_mode_safe = arena_region is Placement.TCM and weights_region is Placement.TCM
    if config.profiling.extreme_mode and not extreme_mode_safe:
        log.warning(
            "profiling.extreme_mode=true ignored: requires arena+weights in TCM "
            "(current: arena=%s, weights=%s). SSRAM/NVM power-down would corrupt "
            "model storage.",
            arena_region,
            weights_region,
        )

    if engine_type is EngineType.HELIA_AOT:
        # --- AOT engine: use AOT-specific main template, no model embedding ---
        aot_prefix = tvars["aot_prefix"]

        # Apply firmware-level placement overrides on AOT arena regions.
        # When the user pins the arena to a specific region, move *scratch*
        # arenas there.  Persistent/constant regions stay where AOT planned
        # them — those typically hold weights/state and have separate
        # placement controls.
        from dataclasses import replace as _dc_replace
        from ..engines.base import ArenaRegion as _ArenaRegion

        aot_arena_regions: list[_ArenaRegion] = list(tvars.get("arena_regions", []))
        if arena_region in (Placement.PSRAM, Placement.TCM, Placement.SRAM, Placement.MRAM):
            aot_arena_regions = [
                _dc_replace(r, placement=arena_region) if r.role is ArenaRole.SCRATCH else r
                for r in aot_arena_regions
            ]

        (src_dir / "main.cc").write_text(
            _jinja_env.get_template("main_aot.cc.j2").render(
                aot_prefix=aot_prefix,
                aot_op_manifest=tvars.get("aot_op_manifest", []),
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                pmu_passes=pmu_passes,
                pmu_pass_names=[p["name"] for p in pmu_passes],
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=sync_gpio_pin,
                transport=transport,
                printf_linkage="static ",
                extreme_mode=config.profiling.extreme_mode,
                model_location=config.model.model_location,
                arena_region=arena_region,
                weights_region=weights_region,
                has_full_pmu=soc.has_full_pmu,
                allocate_arenas=tvars.get("allocate_arenas", True),
                arena_regions=aot_arena_regions,
                **heartbeat_vars,
            )
        )
    else:
        # --- TFLM / heliaRT: embed model as byte array, use TFLM profiler ---
        model_location = config.model.model_location

        if weights_region != "psram":
            model_header = _model_to_header(config.model.path, weights_region)
            (src_dir / "model_data.h").write_text(model_header)

        model_size = config.model.path.stat().st_size

        engine_header = tvars.get("engine_header", "tensorflow/lite/micro/micro_interpreter.h")
        (src_dir / "main.cc").write_text(
            _jinja_env.get_template("main.cc.j2").render(
                engine_header=engine_header,
                arena_size=arena_size,
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                pmu_passes=pmu_passes,
                pmu_pass_names=[p["name"] for p in pmu_passes],
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=sync_gpio_pin,
                transport=transport,
                model_location=model_location,
                arena_region=arena_region,
                weights_region=weights_region,
                model_size=model_size,
                printf_linkage="",
                extreme_mode=config.profiling.extreme_mode,
                has_full_pmu=soc.has_full_pmu,
                **heartbeat_vars,
            )
        )

        # PMU profiler (TFLM-specific C++ class)
        (src_dir / "hpx_pmu_profiler.h").write_text(
            _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
                has_full_pmu=soc.has_full_pmu,
            )
        )
        (src_dir / "hpx_pmu_profiler.cc").write_text(
            _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
                has_full_pmu=soc.has_full_pmu,
            )
        )

    # --- Engine wrapper module ---
    for extra_mod in artifacts.extra_modules:
        mod_src = extra_mod.path
        mod_dst = app_dir / "modules" / extra_mod.name
        if mod_src != mod_dst:
            if mod_dst.exists():
                shutil.rmtree(mod_dst)
            shutil.copytree(mod_src, mod_dst)
        log.info("Engine module: %s → %s", extra_mod.name, mod_dst)

    log.info("Generated profiler app at %s", app_dir)
    return app_dir


def build_app(ctx: PipelineContext) -> tuple[Path, Path]:
    """Invoke ``nsx configure`` + ``nsx build`` on the generated app.

    Returns (build_dir, binary_path).
    """
    assert ctx.firmware_dir is not None
    assert ctx.board is not None

    app_dir = ctx.firmware_dir
    board = ctx.board.name
    timeouts = ctx.config.timeouts
    toolchain = ctx.config.target.toolchain

    # Map config toolchain names to nsx CLI values
    nsx_tc = _nsx_toolchain(toolchain)

    # Lock-aware flow: write nsx.lock once, then materialise modules/ from it
    # before invoking the toolchain. When frozen, skip resolution entirely and
    # require the existing lock/modules state to be reused as-is.
    modules_dir = app_dir / "modules"
    if ctx.config.frozen:
        nsx_cli.sync(app_dir, frozen=True, timeout_s=timeouts.configure_s)
    else:
        nsx_cli.lock(app_dir, timeout_s=timeouts.configure_s)
        try:
            nsx_cli.sync(app_dir, timeout_s=timeouts.configure_s)
        except Exception:
            # Remove partially-materialised modules so next attempt starts clean.
            if modules_dir.exists():
                shutil.rmtree(modules_dir, ignore_errors=True)
            raise

    nsx_cli.configure(app_dir, toolchain=nsx_tc, timeout_s=timeouts.configure_s)
    nsx_cli.build(app_dir, toolchain=nsx_tc, timeout_s=timeouts.build_s)

    # Locate build output
    build_dir = app_dir / "build" / board
    bin_patterns = [
        str(build_dir / "hpx_profiler.bin"),
        str(build_dir / "**" / "hpx_profiler.bin"),
    ]
    binary_path = None
    for pattern in bin_patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            binary_path = Path(matches[0])
            break

    if binary_path is None:
        # Try .axf as fallback
        axf_matches = glob.glob(str(build_dir / "**" / "hpx_profiler.axf"), recursive=True)
        if axf_matches:
            binary_path = Path(axf_matches[0])

    if binary_path is None:
        raise BuildError(
            "Build succeeded but binary not found",
            hint=f"Searched in {build_dir}",
        )

    log.info("Binary: %s", binary_path)
    return build_dir, binary_path


def flash_app(ctx: PipelineContext) -> None:
    """Invoke ``nsx flash`` to deploy the binary to the target."""
    assert ctx.firmware_dir is not None
    toolchain = ctx.config.target.toolchain
    nsx_tc = _nsx_toolchain(toolchain)
    nsx_cli.flash(
        ctx.firmware_dir,
        toolchain=nsx_tc,
        jlink_serial=ctx.config.target.jlink_serial,
        timeout_s=ctx.config.timeouts.flash_s,
    )
