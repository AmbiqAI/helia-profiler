"""Firmware generation — NSX app scaffolding for the profiler.

This module provides the interface between the pipeline stages and the
low-level firmware template rendering + NSX build system.  Each function
receives a ``PipelineContext`` and operates on the fields set by prior stages.
"""

from __future__ import annotations

import glob
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import jinja2

from .. import nsx as nsx_cli
from ..counters import (
    CounterPass,
    plan_passes,
    resolve_counters,
    resolve_legacy_presets,
)
from ..errors import BuildError, FirmwareError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# SDK tier → module set mapping
# ---------------------------------------------------------------------------
_SDK_MODULES: dict[str, list[str]] = {
    "r3": [
        "nsx-ambiqsuite-r3",
        "nsx-ambiq-hal-r3",
        "nsx-ambiq-bsp-r3",
    ],
    "r4": [
        "nsx-ambiqsuite-r4",
        "nsx-ambiq-hal-r4",
        "nsx-ambiq-bsp-r4",
    ],
    "r5": [
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
    # Insert board + common modules after SDK modules
    for mod in _COMMON_MODULES:
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


def _model_to_header(model_path: Path) -> str:
    """Convert a .tflite model to a C header (xxd-style byte array)."""
    data = model_path.read_bytes()
    lines = [
        "// Auto-generated by heliaPROFILER — do not edit.",
        f"// Source: {model_path.name}",
        "alignas(16) static const unsigned char model_data[] = {",
    ]
    for i in range(0, len(data), 12):
        chunk = data[i : i + 12]
        hex_vals = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_vals},")
    lines.append("};")
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

    # --- Resolve module list ---
    mod_names = _resolve_module_list(board.name, soc.sdk_tier)

    # Build module descriptors (name + local flag)
    modules: list[dict[str, object]] = [{"name": m, "local": False} for m in mod_names]

    # Append engine-provided modules (e.g. nsx-heliart) as local
    for extra_mod in artifacts.extra_modules:
        if extra_mod.name not in mod_names:
            modules.append({"name": extra_mod.name, "local": True})

    log.info("NSX modules: %s", ", ".join(m["name"] for m in modules))  # type: ignore[arg-type]

    engine_type = tvars.get("engine_type", "tflm")

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
        )
    )

    # --- Source files ---
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

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

    if engine_type == "helia_aot":
        # --- AOT engine: use AOT-specific main template, no model embedding ---
        aot_prefix = tvars["aot_prefix"]
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
            )
        )
    else:
        # --- TFLM / heliaRT: embed model as byte array, use TFLM profiler ---
        model_header = _model_to_header(config.model.path)
        (src_dir / "model_data.h").write_text(model_header)

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
            )
        )

        # PMU profiler (TFLM-specific C++ class)
        (src_dir / "hpx_pmu_profiler.h").write_text(
            _jinja_env.get_template("hpx_pmu_profiler.h.j2").render()
        )
        (src_dir / "hpx_pmu_profiler.cc").write_text(
            _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render()
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

    nsx_cli.configure(app_dir)
    nsx_cli.build(app_dir)

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
    nsx_cli.flash(
        ctx.firmware_dir,
        jlink_serial=ctx.config.target.jlink_serial,
    )
