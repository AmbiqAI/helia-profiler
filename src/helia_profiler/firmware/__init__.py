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
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jinja2
import yaml

from .. import nsx as nsx_cli
from ..config import DEFAULT_ARENA_SIZE_BYTES
from ..counters import (
    CounterPass,
    plan_passes,
    resolve_counters,
    resolve_legacy_presets,
    supported_groups_for_domains,
    validate_group_selection,
)
from ..engines import EngineType
from ..errors import ConfigError
from ..errors import BuildError, FirmwareError
from ..placement import Placement
from ..platform import get_soc_for_board
from .op_resolver import build_resolver_plan

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


@dataclass(frozen=True)
class NsxModuleSpec:
    """Resolved NSX module identity for firmware generation.

    ``name`` is the module name used in ``NSX_APP_MODULES``. ``project`` is the
    owning NSX project/repository used for lock resolution. Keeping them
    separate avoids assuming that ``project == module`` for monorepo-backed SDK
    modules.
    """

    name: str
    project: str


def _nsx_toolchain(toolchain: str) -> str | None:
    """Convert a config toolchain name to the ``nsx --toolchain`` value.

    Returns *None* for the default (GCC) so the flag is omitted.
    """
    nsx_tc = _TOOLCHAIN_MAP.get(toolchain, toolchain)
    return nsx_tc if nsx_tc != "gcc" else None


# ---------------------------------------------------------------------------
# SDK tier → module set mapping
#
# hpx owns the *selection* of modules a profiler app needs (a deliberately lean
# set — e.g. it uses nsx-core's runtime helpers directly rather than the legacy
# nsx-harness / nsx-utils modules). The *ownership* of each module (which NSX
# project vendors it) is NOT hand-maintained here: it is derived from the NSX
# starter profile for the target board so it always tracks the upstream
# registry (which repoints migrated Ambiq modules onto the unified
# nsx-ambiq-sdk project). See ``_module_project``.
# ---------------------------------------------------------------------------


def _soc_has_backend(soc: Any, backend: str) -> bool:
    return backend in getattr(soc, "profiling_backends", ())


_POWER_SYNC_MODULE_NAMES: tuple[str, ...] = (
    "nsx-interrupt",
    "nsx-gpio",
)


def _board_module_name(board: str) -> str:
    """Derive the NSX board module name from a board name."""
    return f"nsx-board-{board.replace('_', '-')}"


def _starter_profile_module_names(profile: dict[str, Any]) -> list[str]:
    """Return the starter profile's authoritative direct module list.

    hpx trusts the profile as the source of truth for the board/provider stack
    and direct runtime consumers.
    """
    modules = profile.get("modules") or []
    if not isinstance(modules, list) or not all(isinstance(name, str) for name in modules):
        raise FirmwareError(
            "NSX starter profile is missing a valid module list",
            hint="Update neuralspotx so the board starter profile declares its modules.",
        )
    # hpx intentionally does not consume these legacy helpers directly.
    return [name for name in modules if name not in {"nsx-harness", "nsx-utils"}]


def _get_starter_profile(board: str, *, profile_board: str | None = None) -> dict[str, Any]:
    """Return the NSX starter profile for *board*.

    The profile is the single source of truth for module/project ownership.
    hpx follows the installed NSX starter profile's module and project map
    rather than maintaining a parallel SDK-tier table.
    """
    lookup_board = profile_board or board
    profile = nsx_cli.starter_profile(lookup_board)
    if profile is None:
        raise FirmwareError(
            f"No NSX starter profile for board '{lookup_board}'",
            hint=(
                "The board must be registered in the NSX registry "
                "(registry.lock.yaml ships with neuralspotx). Check the board "
                "name or update neuralspotx."
            ),
        )
    return profile


def _needs_armv8m_pmu_module(board: str, *, profile_board: str | None = None) -> bool:
    """Return whether this board needs the standalone Armv8-M PMU module.

    Some installed NSX starter profiles still omit ``nsx-pmu-armv8m`` for AP5
    boards even though hpx's generated firmware links ``nsx::pmu_armv8m``.
    Keep a narrow compatibility fallback until those profiles are updated.
    """
    for candidate in (board, profile_board):
        if candidate is None:
            continue
        try:
            soc = get_soc_for_board(candidate)
        except ValueError:
            continue
        return _soc_has_backend(soc, "armv8m-pmu")
    return False


def _module_project(name: str, profile: dict[str, Any]) -> str:
    """Resolve the owning NSX project for a module name.

    Resolution order, mirroring ``nsx``'s own manifest generation:

    0. The starter profile's ``module_overrides`` — authoritative repoint of a
       module onto the SDK monorepo for tiers that have migrated.
    1. The base registry entry (standalone project) for everything else.
    2. The module name itself as a last resort (opaque / local modules).
    """
    override = profile.get("module_overrides", {}).get(name)
    if isinstance(override, dict) and override.get("project"):
        return override["project"]
    return nsx_cli.registry_module_project(name) or name


def _render_module_registry(profile: dict[str, Any]) -> str:
    """Render the ``module_registry`` block for nsx.yml from the profile.

    Emitting the profile's full ``project_overrides`` / ``module_overrides``
    makes the app's effective registry agree with the per-module project pins
    in the manifest (so ``nsx`` alignment passes) and ensures transitive
    dependencies pulled in during closure resolution resolve to the same SDK
    monorepo as the explicitly listed modules.
    """
    project_overrides = profile.get("project_overrides") or {}
    module_overrides = dict(profile.get("module_overrides") or {})
    if not project_overrides and not module_overrides:
        return ""
    registry: dict[str, Any] = {}
    if project_overrides:
        registry["projects"] = dict(project_overrides)
    if module_overrides:
        registry["modules"] = dict(module_overrides)
    return yaml.safe_dump({"module_registry": registry}, sort_keys=False, default_flow_style=False)


def _default_nsx_channel(board_channel: str, configured_channel: str | None) -> str:
    """Resolve the NSX channel for a generated app.

    An explicit config override wins. Otherwise we use the board's registered
    default so preview-only boards naturally follow the matching NSX channel.
    """
    if configured_channel is not None:
        return configured_channel
    return board_channel


def _resolve_module_specs(board: str, *, profile_board: str | None = None) -> list[NsxModuleSpec]:
    """Build the ordered typed module list for a profiler app.

    Module selection and ownership are both derived from the board's NSX
    starter profile.
    """
    profile = _get_starter_profile(board, profile_board=profile_board)

    ordered_names: list[str] = _starter_profile_module_names(profile)
    if (
        _needs_armv8m_pmu_module(board, profile_board=profile_board)
        and "nsx-pmu-armv8m" not in ordered_names
    ):
        ordered_names.append("nsx-pmu-armv8m")

    return [NsxModuleSpec(name, _module_project(name, profile)) for name in ordered_names]


def _resolve_module_list(board: str, *, profile_board: str | None = None) -> list[str]:
    """Backward-compatible wrapper returning only module names."""
    return [spec.name for spec in _resolve_module_specs(board, profile_board=profile_board)]


def _resolve_project_overrides(
    module_specs: list[NsxModuleSpec],
    nsx_overrides: dict[str, Any],
) -> dict[str, tuple[str, str]]:
    """Collapse module-targeted ref/version overrides onto owning projects.

    Monorepo-backed SDK modules share a project, so ref/version overrides must
    resolve coherently at the project level. ``path`` overrides remain module-
    local because they install a concrete local module directory.
    """
    by_name = {spec.name: spec for spec in module_specs}
    project_overrides: dict[str, tuple[str, str]] = {}
    for name, override in nsx_overrides.items():
        spec = by_name.get(name)
        if spec is None or override.path is not None:
            continue
        mode = "ref" if override.ref is not None else "version"
        value = override.ref if override.ref is not None else override.version
        if value is None:
            continue
        existing = project_overrides.get(spec.project)
        if existing is not None and existing != (mode, value):
            raise ConfigError(
                f"Conflicting build.nsx_modules overrides for project '{spec.project}'",
                hint=(
                    "Modules from the same NSX project must use the same ref/version. "
                    f"Conflicting module names: '{name}' and another module in '{spec.project}'."
                ),
            )
        project_overrides[spec.project] = (mode, value)
    return project_overrides


def _module_names_by_project(module_specs: list[NsxModuleSpec]) -> dict[str, set[str]]:
    """Index generated module names by owning NSX project."""

    names_by_project: dict[str, set[str]] = {}
    for spec in module_specs:
        names_by_project.setdefault(spec.project, set()).add(spec.name)
    return names_by_project


def _install_local_module_override(dest: Path, source: Path) -> None:
    """Copy a local NSX module directory into the app's ``modules/`` tree.

    Validates that the source contains an ``nsx-module.yaml`` (required by
    NSX for any local module).
    """
    source = source.expanduser().resolve()
    if not source.is_dir():
        raise FirmwareError(
            f"NSX module override path is not a directory: {source}",
            hint="Provide a directory containing nsx-module.yaml.",
        )
    if not (source / "nsx-module.yaml").is_file():
        raise FirmwareError(
            f"NSX module override at {source} is missing nsx-module.yaml",
            hint="A valid NSX module must contain nsx-module.yaml at its root.",
        )
    if dest.is_dir():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)
    log.info("Installed local module override: %s → %s", source, dest)


# ---------------------------------------------------------------------------
# PMU preset mapping (legacy — used only for backward-compat Init() path)
# ---------------------------------------------------------------------------
_PMU_PRESET_MAP: dict[str, str] = {
    "basic_cpu": "NSX_PMU_PRESET_BASIC_CPU",
    "memory": "NSX_PMU_PRESET_MEMORY",
    "mve": "NSX_PMU_PRESET_MVE",
    "ml_default": "NSX_PMU_PRESET_ML_DEFAULT",
}


def _resolve_pmu_passes(config: Any, soc: Any | None = None) -> list[dict[str, Any]]:
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
    if soc is not None:
        supported_groups = supported_groups_for_domains(soc.profiling_domains)
        try:
            if profiling.pmu_counters is not None:
                validate_group_selection(
                    profiling.pmu_counters,
                    supported_groups=supported_groups,
                )
            else:
                validate_group_selection(
                    resolve_legacy_presets(profiling.pmu_presets),
                    supported_groups=supported_groups,
                )
        except ValueError as exc:
            raise FirmwareError(
                str(exc),
                hint=(
                    f"Target '{soc.name}' supports PMU groups: "
                    f"{', '.join(supported_groups) if supported_groups else 'none'}."
                ),
            ) from exc

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
        c_enum = _PMU_PRESET_MAP.get(preset_name, "NSX_PMU_PRESET_ML_DEFAULT")
        result.append(
            {
                "name": preset_name,
                "custom": False,
                "event_ids": [],
                "num_counters": 4,
                "c_enum": c_enum,
                "group": preset_name,
            }
        )
    if not result:
        result = [
            {
                "name": "ml_default",
                "custom": False,
                "event_ids": [],
                "num_counters": 4,
                "c_enum": "NSX_PMU_PRESET_ML_DEFAULT",
                "group": "ml_default",
            }
        ]
    return result


# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _write_text(path: Path, text: str) -> None:
    """Write generated source text with deterministic cross-platform encoding."""
    path.write_text(text, encoding="utf-8")


def _find_segger_rtt_dir() -> Path:
    """Locate the SEGGER RTT source directory.

    The ``SEGGER_RTT_PATH`` environment variable must point to the root
    directory of a SEGGER RTT source checkout (the folder containing
    ``RTT/`` and ``Config/`` subdirs).

    Returns the validated path.
    """
    env_path = os.environ.get("SEGGER_RTT_PATH")
    if env_path:
        p = Path(env_path)
        if (p / "RTT" / "SEGGER_RTT.c").exists():
            return p
        raise FirmwareError(
            f"SEGGER_RTT_PATH={env_path} does not contain RTT/SEGGER_RTT.c",
            hint="Set SEGGER_RTT_PATH to the root dir containing RTT/ and Config/ subdirs.",
        )

    raise FirmwareError(
        "SEGGER RTT source files not found — SEGGER_RTT_PATH is not set.",
        hint=(
            "Clone the SEGGER RTT sources and set the environment variable:\n"
            "  git clone https://github.com/SEGGERMicro/RTT.git /path/to/segger-rtt\n"
            "  export SEGGER_RTT_PATH=/path/to/segger-rtt"
        ),
    )


def _copy_segger_rtt(dest_dir: Path) -> None:
    """Copy SEGGER RTT source files into *dest_dir*/rtt/."""
    rtt_root = _find_segger_rtt_dir()
    rtt_dest = dest_dir / "rtt"
    rtt_dest.mkdir(parents=True, exist_ok=True)

    # RTT source + headers. SEGGER_RTT.h includes SEGGER_RTT_ConfDefaults.h,
    # which in turn includes Config/SEGGER_RTT_Conf.h.
    for name in ("SEGGER_RTT.c", "SEGGER_RTT.h", "SEGGER_RTT_ConfDefaults.h"):
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


def _blob_to_header(blob_path: Path, symbol_name: str) -> str:
    """Convert a sidecar constant blob to a C header (xxd-style byte array).

    The blob is placed in ``.rodata`` (const, stays in flash/MRAM) so it
    can be memcpy'd into the runtime arena buffer at boot.
    """
    data = blob_path.read_bytes()
    lines = [
        "// Auto-generated by heliaPROFILER — do not edit.",
        f"// Constant arena sidecar blob: {blob_path.name}",
        "#pragma once",
        "#include <stddef.h>",
        f"alignas(16) static const unsigned char {symbol_name}[] = {{",
    ]
    for i in range(0, len(data), 12):
        chunk = data[i : i + 12]
        hex_vals = ", ".join(f"0x{b:02x}" for b in chunk)
        lines.append(f"    {hex_vals},")
    lines.append("};")
    lines.append(f"static const size_t {symbol_name}_len = sizeof({symbol_name});")
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
    weights_region = ctx.weights_region or Placement.MRAM
    arena_region = ctx.arena_region or Placement.TCM
    power_sync_enabled = config.power.enabled and config.power.mode == "external"
    aot_arena_regions = []
    if artifacts.engine_type is EngineType.HELIA_AOT:
        adapter = ctx.engine_adapter
        assert adapter is not None  # set by stage 2 before firmware
        aot_arena_regions = adapter.apply_arena_placement_override(
            list(artifacts.aot_arena_regions),
            arena_region,
        )

    # --- Resolve module list ---
    profile_board = getattr(board, "profile_source_board", board.name)
    module_specs = _resolve_module_specs(board.name, profile_board=profile_board)
    profile = _get_starter_profile(board.name, profile_board=profile_board)

    # Add nsx-usb module when using USB CDC transport
    transport = config.target.transport
    if transport == "usb_cdc" and "nsx-usb" not in {m.name for m in module_specs}:
        module_specs.append(NsxModuleSpec("nsx-usb", _module_project("nsx-usb", profile)))

    # Add nsx-psram when using PSRAM (for weights or arena)
    psram_needed = (
        arena_region is Placement.PSRAM
        or weights_region is Placement.PSRAM
        or any(region.placement is Placement.PSRAM for region in aot_arena_regions)
    )
    if psram_needed:
        module_names = {m.name for m in module_specs}
        if "nsx-interrupt" not in module_names:
            module_specs.append(
                NsxModuleSpec("nsx-interrupt", _module_project("nsx-interrupt", profile))
            )
            module_names.add("nsx-interrupt")
        if "nsx-psram" not in module_names:
            module_specs.append(NsxModuleSpec("nsx-psram", _module_project("nsx-psram", profile)))

    if power_sync_enabled:
        module_names = {m.name for m in module_specs}
        for name in _POWER_SYNC_MODULE_NAMES:
            if name not in module_names:
                module_specs.append(NsxModuleSpec(name, _module_project(name, profile)))
                module_names.add(name)

    # Build module descriptors (name + local flag + optional overrides)
    nsx_overrides = config.build.nsx_modules
    board_mod = _board_module_name(board.name)
    project_overrides = _resolve_project_overrides(module_specs, nsx_overrides)
    module_names_by_project = _module_names_by_project(module_specs)
    modules: list[dict[str, object]] = []
    matched_overrides: set[str] = set()
    for spec in module_specs:
        override = nsx_overrides.get(spec.name)
        project_override = project_overrides.get(spec.project)
        if override and override.path and spec.name == board_mod:
            # NSX treats board modules specially: local board sources live under
            # boards/<board>, while regular modules live under modules/<name>.
            matched_overrides.add(spec.name)
            local_board_dir = app_dir / "boards" / board.name
            _install_local_module_override(local_board_dir, override.path)
            modules.append({"name": spec.name, "project": spec.project, "local": True})
        elif override and override.path:
            # Local path override — install into app modules/ and mark local
            matched_overrides.add(spec.name)
            local_mod_dir = app_dir / "modules" / spec.name
            _install_local_module_override(local_mod_dir, override.path)
            modules.append({"name": spec.name, "project": spec.project, "local": True})
        elif project_override is not None:
            matched_overrides.update(
                name
                for name, override_spec in nsx_overrides.items()
                if override_spec.path is None and name in module_names_by_project[spec.project]
            )
            mode, value = project_override
            modules.append(
                {"name": spec.name, "project": spec.project, "local": False, mode: value}
            )
        else:
            modules.append({"name": spec.name, "project": spec.project, "local": False})

    # Warn about overrides that didn't match any module in the build
    unmatched = set(nsx_overrides.keys()) - matched_overrides
    for name in sorted(unmatched):
        log.warning(
            "build.nsx_modules override '%s' did not match any module in this "
            "build — check the module name (available: %s)",
            name,
            ", ".join(spec.name for spec in module_specs),
        )

    # Append engine-provided modules (e.g. nsx-helia-rt). Each is either a
    # registry module (NSX clones it from GitHub during `nsx sync`) or a
    # locally vendored module installed under its registry-derived project
    # directory.
    spec_names = {spec.name for spec in module_specs}
    for extra_mod in artifacts.extra_modules:
        if extra_mod.name in spec_names:
            continue
        project = extra_mod.project or extra_mod.name
        if extra_mod.local:
            modules.append({"name": extra_mod.name, "project": project, "local": True})
        else:
            entry: dict[str, object] = {
                "name": extra_mod.name,
                "project": project,
                "local": False,
            }
            if extra_mod.ref:
                entry["ref"] = extra_mod.ref
            modules.append(entry)

    log.info("NSX modules: %s", ", ".join(m["name"] for m in modules))  # type: ignore[arg-type]

    # Engine identity flows through the typed EngineArtifacts field.
    # Templates receive the canonical hyphen-form string (StrEnum value).
    engine_type = artifacts.engine_type
    profiling_backends = list(soc.profiling_backends)
    has_armv8m_pmu = _soc_has_backend(soc, "armv8m-pmu")

    # --- nsx.yml ---
    _write_text(
        app_dir / "nsx.yml",
        _jinja_env.get_template("nsx.yml.j2").render(
            board=board.name,
            soc=soc.name,
            toolchain=config.target.toolchain,
            channel=_default_nsx_channel(board.channel, config.build.channel),
            modules=modules,
            module_registry_yaml=_render_module_registry(profile),
        ),
    )

    # --- cmake/nsx/modules.cmake ---
    cmake_nsx_dir = app_dir / "cmake" / "nsx"
    cmake_nsx_dir.mkdir(parents=True, exist_ok=True)
    _write_text(
        cmake_nsx_dir / "modules.cmake",
        _jinja_env.get_template("modules.cmake.j2").render(modules=modules),
    )

    # --- CMakeLists.txt (engine-aware) ---
    _write_text(
        app_dir / "CMakeLists.txt",
        _jinja_env.get_template("CMakeLists.txt.j2").render(
            board=board.name,
            engine_type=engine_type,
            cmake_vars=artifacts.cmake_vars,
            aot_cmake_target=artifacts.aot_cmake_target or "",
            transport=transport,
            model_location=config.model.model_location,
            arena_region=arena_region,
            weights_region=weights_region,
            profiling_backends=profiling_backends,
            has_armv8m_pmu=has_armv8m_pmu,
            power_sync_enabled=power_sync_enabled,
            arena_regions=aot_arena_regions,
        ),
    )

    # --- Source files ---
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # --- Copy SEGGER RTT source when using RTT transport ---
    if transport == "rtt":
        _copy_segger_rtt(src_dir)

    # PMU preset
    first_preset = config.profiling.pmu_presets[0] if config.profiling.pmu_presets else "ml_default"
    pmu_preset_c = _PMU_PRESET_MAP.get(first_preset, "NSX_PMU_PRESET_ML_DEFAULT")

    # Build pass list for multi-pass firmware loop
    pmu_passes = _resolve_pmu_passes(config, soc)

    # Arena size: use configured value or default 256KB
    arena_size = config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES
    resolver_plan = build_resolver_plan(
        engine_type=engine_type,
        engine_config=config.engine.config,
        model_analysis=ctx.model_analysis,
    )
    clock = ctx.run_metadata.platform
    perf_mode_symbol = clock.cpu_perf_tier
    perf_mode_mhz = clock.cpu_clock_mhz
    resource_variable_count = sum(
        1
        for layer in (ctx.model_analysis.layers if ctx.model_analysis else ())
        if layer.op == "VAR_HANDLE"
    )

    # External power sync
    sync_gpio_pin = config.power.sync_gpio_pin
    cmsis_device_header = soc.cmsis_header

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
        aot_prefix = artifacts.aot_prefix
        assert aot_prefix is not None  # heliaAOT adapter always sets this

        # Generate C headers for constant arena sidecar blobs.
        # In external-arena mode the AOT compiler emits constant data as
        # binary sidecar files rather than C arrays.  The profiler app
        # must embed these blobs into flash (MRAM) and memcpy them into
        # the bound arena buffer at boot.
        aot_module_name = artifacts.aot_module_name
        if not artifacts.aot_allocate_arenas and aot_module_name:
            # Find the AOT module source path (before copytree)
            aot_mod_path: Path | None = None
            for m in artifacts.extra_modules:
                if m.name == aot_module_name:
                    aot_mod_path = m.path
                    break
            for region in aot_arena_regions:
                if region.blob_filename and aot_mod_path:
                    blob_path = aot_mod_path / region.blob_filename
                    if blob_path.exists():
                        header_name = f"hpx_const_blob_{region.region_id}.h"
                        symbol = f"hpx_const_blob_{region.region_id}"
                        _write_text(src_dir / header_name, _blob_to_header(blob_path, symbol))
                        log.info(
                            "Embedded constant blob %s (%d bytes) → %s",
                            region.blob_filename,
                            blob_path.stat().st_size,
                            header_name,
                        )
                    else:
                        log.warning(
                            "Constant arena %d references blob %s but file not found at %s",
                            region.region_id,
                            region.blob_filename,
                            blob_path,
                        )

        _write_text(
            src_dir / "main.cc",
            _jinja_env.get_template("main_aot.cc.j2").render(
                aot_prefix=aot_prefix,
                aot_op_manifest=ctx.engine_artifacts.aot_op_manifest or [],
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                pmu_passes=pmu_passes,
                pmu_pass_names=[p["name"] for p in pmu_passes],
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=sync_gpio_pin,
                cmsis_device_header=cmsis_device_header,
                transport=transport,
                printf_linkage="static ",
                extreme_mode=config.profiling.extreme_mode,
                model_location=config.model.model_location,
                arena_region=arena_region,
                weights_region=weights_region,
                profiling_backends=profiling_backends,
                has_armv8m_pmu=has_armv8m_pmu,
                allocate_arenas=artifacts.aot_allocate_arenas,
                arena_regions=aot_arena_regions,
                perf_mode_symbol=perf_mode_symbol,
                perf_mode_mhz=perf_mode_mhz,
                **heartbeat_vars,
            ),
        )
    else:
        # --- TFLM / heliaRT: embed model as byte array, use TFLM profiler ---
        model_location = config.model.model_location

        if weights_region != "psram":
            model_header = _model_to_header(config.model.path, weights_region)
            _write_text(src_dir / "model_data.h", model_header)

        model_size = config.model.path.stat().st_size

        engine_header = artifacts.engine_header
        _write_text(
            src_dir / "main.cc",
            _jinja_env.get_template("main.cc.j2").render(
                engine_header=engine_header,
                arena_size=arena_size,
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                pmu_passes=pmu_passes,
                pmu_pass_names=[p["name"] for p in pmu_passes],
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=sync_gpio_pin,
                cmsis_device_header=cmsis_device_header,
                transport=transport,
                model_location=model_location,
                arena_region=arena_region,
                weights_region=weights_region,
                model_size=model_size,
                resolver_mode=resolver_plan.mode,
                resolver_max_ops=resolver_plan.max_ops,
                resolver_registrations=resolver_plan.registrations,
                resource_variable_count=resource_variable_count,
                printf_linkage="",
                extreme_mode=config.profiling.extreme_mode,
                profiling_backends=profiling_backends,
                has_armv8m_pmu=has_armv8m_pmu,
                perf_mode_symbol=perf_mode_symbol,
                perf_mode_mhz=perf_mode_mhz,
                **heartbeat_vars,
            ),
        )

        # PMU profiler (TFLM-specific C++ class)
        _write_text(
            src_dir / "hpx_pmu_profiler.h",
            _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
                cmsis_device_header=cmsis_device_header,
                profiling_backends=profiling_backends,
                has_armv8m_pmu=has_armv8m_pmu,
            ),
        )
        _write_text(
            src_dir / "hpx_pmu_profiler.cc",
            _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
                profiling_backends=profiling_backends,
                has_armv8m_pmu=has_armv8m_pmu,
            ),
        )

    # --- Engine modules ---
    # Local modules are vendored into the app under their registry-derived
    # project directory (so NSX's registry-aware lock finds them). Registry
    # modules are cloned by NSX during `nsx sync`; nothing to copy here.
    for extra_mod in artifacts.extra_modules:
        if not extra_mod.local:
            target = extra_mod.project or extra_mod.name
            ref_note = f" @ {extra_mod.ref}" if extra_mod.ref else ""
            log.info(
                "Engine module: %s → NSX registry (%s%s)",
                extra_mod.name,
                target,
                ref_note,
            )
            continue
        mod_src = extra_mod.path
        mod_dst = app_dir / "modules" / (extra_mod.project or extra_mod.name)
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
    verbose = ctx.config.verbose

    # Map config toolchain names to nsx CLI values
    nsx_tc = _nsx_toolchain(toolchain)

    # Lock-aware flow: refresh nsx.lock for normal runs, then materialise
    # modules/ from it before invoking the toolchain. When frozen, skip
    # resolution entirely and require the existing lock/modules state to be
    # reused as-is.
    modules_dir = app_dir / "modules"
    if ctx.config.frozen:
        nsx_cli.sync(app_dir, frozen=True, timeout_s=timeouts.configure_s, verbose=verbose)
    else:
        nsx_cli.lock(app_dir, update=True, timeout_s=timeouts.configure_s, verbose=verbose)
        try:
            nsx_cli.sync(app_dir, timeout_s=timeouts.configure_s, verbose=verbose)
        except Exception:
            # Remove partially-materialised modules so next attempt starts clean.
            if modules_dir.exists():
                shutil.rmtree(modules_dir, ignore_errors=True)
            raise

    nsx_cli.configure(app_dir, toolchain=nsx_tc, timeout_s=timeouts.configure_s, verbose=verbose)
    nsx_cli.build(app_dir, toolchain=nsx_tc, timeout_s=timeouts.build_s, verbose=verbose)

    # Locate build output. Prefer the ELF-form executable because later
    # reporting stages run size tools against it to capture text/data/bss.
    build_dir = app_dir / "build" / board
    artifact_patterns = [
        str(build_dir / "hpx_profiler"),
        str(build_dir / "**" / "hpx_profiler"),
        str(build_dir / "**" / "hpx_profiler.axf"),
        str(build_dir / "**" / "hpx_profiler.elf"),
        str(build_dir / "hpx_profiler.bin"),
        str(build_dir / "**" / "hpx_profiler.bin"),
    ]
    binary_path = None
    for pattern in artifact_patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            binary_path = Path(matches[0])
            break

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
        jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
        timeout_s=ctx.config.timeouts.flash_s,
        verbose=ctx.config.verbose,
    )
