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

import yaml

from .. import nsx as nsx_cli
from ..config import Transport
from ..engines import EngineType
from ..errors import ConfigError
from ..errors import BuildError, FirmwareError
from ..placement import Placement
from ..platform import get_soc_for_board
from .context import FirmwareRenderContext, _PMU_PRESET_MAP, _resolve_pmu_passes
from .project import (
    NsxModuleSpec,
    ProjectRenderContext,
    _board_module_name,
    _copy_local_engine_module,
    _default_nsx_channel,
    _get_starter_profile,
    _install_local_module_override,
    _module_names_by_project,
    _module_project,
    _POWER_SYNC_MODULE_NAMES,
    _render_module_registry,
    _resolve_module_list,
    _resolve_module_specs,
    _resolve_project_overrides,
    _soc_has_backend,
    _usb_provider_module_names,
    render_project_files,
)
from .render import _jinja_env, _write_text

if TYPE_CHECKING:
    from ..config import ProfileConfig
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

_DEFAULT_RTT_BUFFER_SIZE_UP = 32768
_ATFE_RTT_BUFFER_SIZE_UP = 12288


def _nsx_toolchain(toolchain: str) -> str | None:
    """Convert a config toolchain name to the ``nsx --toolchain`` value.

    Returns *None* for the default (GCC) so the flag is omitted.
    """
    nsx_tc = _TOOLCHAIN_MAP.get(toolchain, toolchain)
    return nsx_tc if nsx_tc != "gcc" else None


def _rtt_buffer_size_up(toolchain: str, transport: Transport, configured_size: int | None) -> int:
    """Return the compile-time SEGGER RTT up-buffer size for generated apps."""
    if configured_size is not None:
        return configured_size
    if transport == Transport.RTT and toolchain == "atfe":
        return _ATFE_RTT_BUFFER_SIZE_UP
    return _DEFAULT_RTT_BUFFER_SIZE_UP


# Compiler launchers tried, in order, when ``build.compiler_launcher`` is
# ``"auto"``.  sccache is preferred (better cross-platform + CI story); ccache
# is the common local fallback.
_AUTO_COMPILER_LAUNCHERS: tuple[str, ...] = ("sccache", "ccache")
_DISABLED_LAUNCHER_VALUES = frozenset({"", "none", "off", "false", "disabled", "0"})

# Compiler launchers that do not understand a given toolchain's compiler driver.
# sccache rejects armclang outright ("Compiler not supported"), and because it
# wraps the driver it also drops ``--target``, which surfaces as the misleading
# ``armclang: fatal error: no target architecture given``.  Auto-detect must
# therefore treat sccache as unavailable for these toolchains rather than
# silently breaking the build.
_LAUNCHER_UNSUPPORTED_TOOLCHAINS: dict[str, frozenset[str]] = {
    "sccache": frozenset({"armclang"}),
}


def _launcher_basename(launcher: str) -> str:
    """Return the bare tool name for a launcher path or command."""
    return Path(launcher).name.lower()


def _launcher_supports_toolchain(launcher: str, toolchain: str) -> bool:
    """Whether ``launcher`` can wrap ``toolchain``'s compiler driver."""
    unsupported = _LAUNCHER_UNSUPPORTED_TOOLCHAINS.get(_launcher_basename(launcher))
    return not (unsupported and toolchain in unsupported)


def _resolve_compiler_launcher(config: "ProfileConfig") -> str | None:
    """Resolve the CMake compiler launcher executable for this build.

    Precedence: the ``HPX_COMPILER_LAUNCHER`` environment variable overrides
    ``build.compiler_launcher``.  Returns an absolute path to the launcher, or
    ``None`` when caching is disabled or no launcher is available.

    * ``"auto"`` — use the first of :data:`_AUTO_COMPILER_LAUNCHERS` found on
      ``PATH`` that supports the active toolchain; do nothing if none are
      installed (installing the binary is the opt-in).
    * disabled values (``none``/``off``/``false``/empty) — ``None``.
    * an explicit tool name or path — required: raises if it cannot be found.
      If the named launcher cannot wrap the active toolchain (e.g. sccache with
      armclang) it is skipped with a warning rather than breaking the build.
    """
    toolchain = config.target.toolchain
    setting = os.environ.get("HPX_COMPILER_LAUNCHER")
    source = "HPX_COMPILER_LAUNCHER"
    if setting is None:
        setting = config.build.compiler_launcher
        source = "build.compiler_launcher"
    setting = setting.strip()

    if setting.lower() in _DISABLED_LAUNCHER_VALUES:
        return None

    if setting.lower() == "auto":
        for name in _AUTO_COMPILER_LAUNCHERS:
            found = shutil.which(name)
            if not found:
                continue
            if not _launcher_supports_toolchain(name, toolchain):
                log.debug(
                    "Skipping compiler launcher %s: unsupported for toolchain %s",
                    name,
                    toolchain,
                )
                continue
            log.info("Using compiler launcher: %s (auto-detected)", found)
            return found
        return None

    found = shutil.which(setting)
    if found is None and Path(setting).is_file() and os.access(setting, os.X_OK):
        found = str(Path(setting).resolve())
    if found is None:
        raise FirmwareError(
            f"Compiler launcher {setting!r} (from {source}) was not found on PATH.",
            hint=(
                "Install it, use the full path, or set the launcher to 'auto'/'none'. "
                "For sccache: https://github.com/mozilla/sccache."
            ),
        )
    if not _launcher_supports_toolchain(setting, toolchain):
        log.warning(
            "Compiler launcher %r (from %s) does not support the %s toolchain; "
            "disabling it for this build.",
            setting,
            source,
            toolchain,
        )
        return None
    log.info("Using compiler launcher: %s (from %s)", found, source)
    return found


def _find_segger_rtt_dir() -> Path:
    """Locate the SEGGER RTT source directory.

    ``SEGGER_RTT_PATH`` takes precedence when set. Otherwise, hpx checks a
    small set of common local checkout locations. The path must point to the
    root directory of a SEGGER RTT source checkout (the folder containing
    ``RTT/`` and ``Config/`` subdirs).

    Returns the validated path.
    """
    env_path = os.environ.get("SEGGER_RTT_PATH")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if _is_segger_rtt_root(p):
            return p
        raise FirmwareError(
            f"SEGGER_RTT_PATH={env_path} does not contain RTT/SEGGER_RTT.c",
            hint="Set SEGGER_RTT_PATH to the root dir containing RTT/ and Config/ subdirs.",
        )

    for candidate in _segger_rtt_candidates():
        if _is_segger_rtt_root(candidate):
            resolved = candidate.expanduser().resolve()
            log.info("Auto-detected SEGGER RTT source at %s", resolved)
            return resolved

    raise FirmwareError(
        "SEGGER RTT source files not found.",
        hint=(
            "Clone the SEGGER RTT sources into a common local path, or set "
            "SEGGER_RTT_PATH explicitly:\n"
            "  git clone https://github.com/SEGGERMicro/RTT.git ~/src/segger-rtt\n"
            "  export SEGGER_RTT_PATH=~/src/segger-rtt"
        ),
    )


def _segger_rtt_candidates() -> tuple[Path, ...]:
    """Return deterministic local paths that may contain SEGGER RTT sources."""
    repo_root = Path(__file__).resolve().parents[3]
    cwd = Path.cwd()
    home = Path.home()
    return (
        repo_root / "RTT",
        repo_root / "segger-rtt",
        repo_root / "examples" / "quickstart" / "RTT",
        cwd / "RTT",
        cwd / "segger-rtt",
        cwd / "examples" / "quickstart" / "RTT",
        home / "src" / "segger-rtt",
        home / "src" / "RTT",
        home / "SEGGER" / "RTT",
    )


def _is_segger_rtt_root(path: Path) -> bool:
    root = path.expanduser()
    return (
        (root / "RTT" / "SEGGER_RTT.c").is_file()
        and (root / "RTT" / "SEGGER_RTT.h").is_file()
        and (root / "Config" / "SEGGER_RTT_Conf.h").is_file()
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

    # RTT buffer placement is cache-coherency sensitive on the Cortex-M55 parts.
    #
    # SEGGER RTT supports relocation via its SEGGER_RTT_SECTION hook: when
    # SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 (the default on Apollo parts) the
    # control block and buffers are declared through SEGGER_RTT_PUT_CB_SECTION /
    # SEGGER_RTT_PUT_BUFFER_SECTION, which emit
    # ``__attribute__((section(SEGGER_RTT_SECTION)))`` for GCC/clang.
    #
    # On the cacheless Cortex-M4 parts (Apollo3/4) there is no coherency hazard,
    # so we point that section at the NSX ``.sram_bss`` input section (collected
    # into SHARED_SRAM by the linker scripts) to keep SEGGER's large staging
    # buffers out of scarce MCU_TCM .bss.
    #
    # On the cache-coherent Cortex-M55 parts (Apollo5 / Apollo510 family) shared
    # SRAM is *cached*, and SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 tells RTT there is
    # no cache to work around. That combination is incoherent with J-Link's
    # asynchronous SWD reads/writes of the ring: the host can observe a stale
    # ring (old bytes published before the new payload reaches SRAM) or have its
    # up-buffer RdOff clobbered by the CPU's whole-cache clean, corrupting the
    # stream. We therefore keep the buffers in *non-cached* TCM (the default .bss
    # region) on these parts so SWD reads stay coherent with zero cache
    # maintenance — the configuration SEGGER RTT actually assumes.
    #
    # NOTE: do *not* try to rewrite the ``#if SEGGER_RTT_CPU_CACHE_LINE_SIZE``
    # aligned declarations — that branch is dead code here (the macro is 0), so
    # patching it has no effect on the compiled object.
    rtt_c = rtt_dest / "SEGGER_RTT.c"
    if rtt_c.exists():
        text = rtt_c.read_text(encoding="utf-8")
        if (
            "SEGGER_RTT_PUT_CB_SECTION(" not in text
            or "SEGGER_RTT_PUT_BUFFER_SECTION(" not in text
        ):
            raise FirmwareError(
                "Failed to patch SEGGER_RTT.c for SRAM placement",
                hint=(
                    "SEGGER_RTT.c does not use SEGGER_RTT_PUT_CB_SECTION / "
                    "SEGGER_RTT_PUT_BUFFER_SECTION; cannot place the RTT control "
                    "block and buffers in shared SRAM. Update the RTT patch "
                    "logic for this SEGGER RTT release."
                ),
            )

    # Config header — nested in Config/ subdir. Append the SEGGER_RTT_SECTION
    # definition so the buffers land in shared SRAM on parts that have a
    # dedicated .sram_bss region (NSX_MEM__HAS_SRAM_BSS); on simpler parts the
    # macro stays undefined and SEGGER falls back to the default .bss region.
    config_dest = rtt_dest / "Config"
    config_dest.mkdir(parents=True, exist_ok=True)
    conf_dest = config_dest / "SEGGER_RTT_Conf.h"
    conf_src = rtt_root / "Config" / "SEGGER_RTT_Conf.h"
    if conf_src.exists():
        shutil.copy2(conf_src, conf_dest)

    sram_placement = (
        "\n"
        "/* heliaPROFILER: RTT control block + channel buffer placement.\n"
        " *\n"
        " * Cache-coherent Cortex-M55 parts (Apollo5 / Apollo510 family): keep the\n"
        " * buffers in NON-CACHED TCM (default .bss). Their shared SRAM is cached,\n"
        " * and SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 assumes no cache, so .sram_bss\n"
        " * placement is incoherent with J-Link's async SWD ring access (stale\n"
        " * reads / clobbered RdOff). TCM is not cached, so SWD stays coherent with\n"
        " * zero cache maintenance.\n"
        " *\n"
        " * Cacheless Cortex-M4 parts (Apollo3/4): no coherency hazard, so move the\n"
        " * large staging buffers into shared SRAM (.sram_bss) to spare MCU_TCM. */\n"
        '#include "nsx_mem.h"\n'
        "#if defined(AM_PART_APOLLO510) || defined(AM_PART_APOLLO510B) || \\\n"
        "    defined(AM_PART_APOLLO5A)  || defined(AM_PART_APOLLO5B)  || \\\n"
        "    defined(AM_PART_APOLLO510L) || defined(AM_PART_APOLLO330P)\n"
        "  /* Non-cached TCM: leave SEGGER_RTT_SECTION undefined (default .bss). */\n"
        "#elif NSX_MEM__HAS_SRAM_BSS\n"
        "  #ifndef SEGGER_RTT_SECTION\n"
        "    #define SEGGER_RTT_SECTION NSX_MEM__SEC_SRAM_BSS\n"
        "  #endif\n"
        "#endif\n"
    )
    existing_conf = conf_dest.read_text(encoding="utf-8") if conf_dest.exists() else ""
    if "SEGGER_RTT_SECTION" not in existing_conf:
        conf_dest.write_text(existing_conf + sram_placement, encoding="utf-8")

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
    # Dedicated power binary (hpx_profiler_power): rendered/built only when
    # power capture is actually requested AND the dedicated firmware mode is
    # selected, so non-power runs (and "shared"-mode power runs, which reuse
    # the transport binary and never touch hpx_profiler_power) keep an
    # unchanged CMakeLists.txt / firmware-render digest (see AGENTS.md WP2).
    power_binary_enabled = config.power.enabled and config.power.firmware == "dedicated"
    aot_arena_regions = []
    if artifacts.engine_type is EngineType.HELIA_AOT:
        adapter = ctx.engine_adapter
        assert adapter is not None  # set by stage 2 before firmware
        # heliaAOT has finer-grained per-tensor placement control than the
        # shared --arena-location/--weights-location knobs: a custom AOT
        # memory config (engine.config_path, or inline
        # engine.config.aot_args.memory.tensors) already resolves each
        # tensor's placement correctly (reflected in artifacts.aot_arena_
        # regions). Only fall back to re-pinning scratch arenas onto the
        # shared arena_region default when the user did NOT supply one of
        # those — otherwise this override would silently clobber a custom
        # yaml's placement (e.g. reporting "tcm" for a scratch arena the
        # user explicitly placed in "sram").
        has_custom_aot_memory = config.engine.config_path is not None or bool(
            config.engine.config.get("aot_args", {}).get("memory", {}).get("tensors")
        )
        if has_custom_aot_memory:
            aot_arena_regions = list(artifacts.aot_arena_regions)
        else:
            aot_arena_regions = adapter.apply_arena_placement_override(
                list(artifacts.aot_arena_regions),
                arena_region,
            )

    # --- Resolve module list ---
    profile_board = getattr(board, "profile_source_board", board.name)
    module_specs = _resolve_module_specs(
        board.name, profile_board=profile_board, registry=config.platform_registry
    )
    profile = _get_starter_profile(board.name, profile_board=profile_board)

    # Add transport modules when using USB CDC transport
    transport = config.target.transport
    if transport == Transport.USB_CDC:
        module_names = {m.name for m in module_specs}
        for name in _usb_provider_module_names(module_specs, profile):
            if name not in module_names:
                module_specs.append(NsxModuleSpec(name, _module_project(name, profile)))
                module_names.add(name)
        if "nsx-usb" not in module_names:
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

    # BLE-controller-reset GPIO drive (Blue-variant boards, dedicated power
    # binary only — see _ble_reset.j2) needs nsx-gpio even when power_sync
    # itself is off (e.g. power.mode == "internal").
    if power_binary_enabled and board.ble_reset_gpio_pin is not None:
        module_names = {m.name for m in module_specs}
        if "nsx-gpio" not in module_names:
            module_specs.append(NsxModuleSpec("nsx-gpio", _module_project("nsx-gpio", profile)))
            module_names.add("nsx-gpio")

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
    render_context = FirmwareRenderContext.from_pipeline_context(
        ctx,
        arena_regions=aot_arena_regions,
    )
    template_vars = render_context.to_template_vars()
    profiling_backends = list(render_context.pmu.profiling_backends)
    has_armv8m_pmu = render_context.pmu.has_armv8m_pmu

    compiler_launcher = _resolve_compiler_launcher(config)
    render_project_files(
        ProjectRenderContext(
            app_dir=app_dir,
            board=board,
            soc=soc,
            config=config,
            artifacts=artifacts,
            modules=modules,
            module_registry_yaml=_render_module_registry(profile, project_overrides),
            render_context=render_context,
            arena_regions=aot_arena_regions,
            compiler_launcher=compiler_launcher or "",
            channel=_default_nsx_channel(board.channel, config.build.channel),
            rtt_buffer_size_up=_rtt_buffer_size_up(
                config.target.toolchain,
                transport,
                config.target.rtt_buffer_size_up,
            ),
            power_binary_enabled=power_binary_enabled,
        )
    )

    # --- Source files ---
    src_dir = app_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)

    # --- Copy SEGGER RTT source when using RTT transport ---
    if transport == Transport.RTT:
        _copy_segger_rtt(src_dir)

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
                **template_vars,
                pmu_max_ops=soc.pmu_max_ops,
            ),
        )
        if power_binary_enabled:
            # Same template, power_only=True: no transport init, no per-layer
            # PMU passes -- see main_aot.cc.j2's power_only branches (WP1).
            _write_text(
                src_dir / "main_power.cc",
                _jinja_env.get_template("main_aot.cc.j2").render(
                    **{**template_vars, "power_only": True},
                    pmu_max_ops=soc.pmu_max_ops,
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
                **template_vars,
            ),
        )
        if power_binary_enabled:
            # Same template, power_only=True: no transport init, no per-layer
            # PMU passes -- see main.cc.j2's power_only branches (WP1).
            _write_text(
                src_dir / "main_power.cc",
                _jinja_env.get_template("main.cc.j2").render(
                    **{**template_vars, "power_only": True},
                ),
            )

        # PMU profiler (TFLM-specific C++ class)
        _write_text(
            src_dir / "hpx_pmu_profiler.h",
            _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
                cmsis_device_header=render_context.pmu.cmsis_device_header,
                profiling_backends=profiling_backends,
                has_armv8m_pmu=has_armv8m_pmu,
                pmu_max_ops=soc.pmu_max_ops,
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
    # project directory so ``nsx lock`` can resolve them. When the module
    # name differs from the project (e.g. nsx-helia-rt in project helia-rt),
    # also mirror the same content under modules/<name> because the later
    # CMake bootstrap stage resolves local module add_subdirectory() paths by
    # module name.
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
        primary_dst = app_dir / "modules" / (extra_mod.project or extra_mod.name)
        if mod_src != primary_dst:
            _copy_local_engine_module(primary_dst, mod_src)

        alias_dst = app_dir / "modules" / extra_mod.name
        if alias_dst != primary_dst:
            _copy_local_engine_module(alias_dst, mod_src)
            log.info(
                "Engine module: %s → %s (alias: %s)",
                extra_mod.name,
                primary_dst,
                alias_dst,
            )
        else:
            log.info("Engine module: %s → %s", extra_mod.name, primary_dst)

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
    build_dir = app_dir / "build" / board
    ninja_already_configured = (build_dir / "build.ninja").exists()

    # Lock-aware flow: refresh nsx.lock for normal runs, then materialise
    # modules/ from it before invoking the toolchain. When frozen, skip
    # resolution entirely and require the existing lock/modules state to be
    # reused as-is.
    modules_dir = app_dir / "modules"
    if ctx.config.frozen:
        if not ninja_already_configured:
            # First build for this board/toolchain combo: still need one
            # verify-only sync (raises loudly on drift or a missing lock —
            # see nsx_cli.sync's frozen docstring) plus a real `nsx
            # configure`, since nothing has been materialised/configured
            # here yet.
            nsx_cli.sync(app_dir, frozen=True, timeout_s=timeouts.configure_s, verbose=verbose)
            nsx_cli.configure(
                app_dir, toolchain=nsx_tc, timeout_s=timeouts.configure_s, verbose=verbose
            )
        # else: fully offline incremental rebuild. Skip nsx lock (no
        # network resolve of the "main" branch constraint), nsx sync's
        # module content-hash verification (a full tree hash over
        # modules/ — real CPU/IO cost for large vendored trees), and the
        # nsx configure() round-trip entirely. CMake's own
        # --regenerate-during-build ninja rule transparently re-runs
        # `cmake` if CMakeLists.txt (or any other tracked input,
        # including hpx's freshly re-rendered templates from
        # generate_app()) changed, with ZERO module-tree verification —
        # so a hand-patched vendored NSX module under modules/ survives
        # untouched across every subsequent build. This is the fast path
        # for "I'm iterating on a local NSX module fix and want every
        # rebuild to keep using it" (see AGENTS.md / the AP330 bring-up
        # session that motivated this).
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
    # Same gate as generate_app()'s power_binary_enabled: only build the
    # dedicated power binary when it was actually rendered into
    # CMakeLists.txt (power.enabled AND firmware == "dedicated"). "shared"
    # mode never adds the hpx_profiler_power target, so building it here
    # would fail.
    power_binary_enabled = ctx.config.power.enabled and ctx.config.power.firmware == "dedicated"
    if power_binary_enabled:
        # ``nsx build`` targets the CMake project name (hpx_profiler) by
        # default — the dedicated power binary needs an explicit second
        # `cmake --build --target hpx_profiler_power` from the SAME
        # configure/module-sync (see generate_app: it is only added to
        # CMakeLists.txt when power.enabled, so this never runs otherwise).
        nsx_cli.build(
            app_dir,
            toolchain=nsx_tc,
            target="hpx_profiler_power",
            timeout_s=timeouts.build_s,
            verbose=verbose,
        )

    # Locate build output. Prefer the ELF-form executable because later
    # reporting stages run size tools against it to capture text/data/bss.
    binary_path = _find_target_binary(build_dir, "hpx_profiler")
    if binary_path is None:
        raise BuildError(
            "Build succeeded but binary not found",
            hint=f"Searched in {build_dir}",
        )
    log.info("Binary: %s", binary_path)

    # The dedicated power binary is only ever generated (and added to
    # CMakeLists.txt) when power.enabled AND power.firmware == "dedicated" —
    # see generate_app(). Its path is stashed on ctx for later stages (WP3
    # wires it into the power-capture flash/run flow; this stage only
    # exposes it).
    if power_binary_enabled:
        power_binary_path = _find_target_binary(build_dir, "hpx_profiler_power")
        if power_binary_path is None:
            raise BuildError(
                "Build succeeded but power binary (hpx_profiler_power) not found",
                hint=f"Searched in {build_dir}",
            )
        log.info("Power binary: %s", power_binary_path)
        ctx.power_binary_path = power_binary_path

    return build_dir, binary_path


def _find_target_binary(build_dir: Path, target_name: str) -> Path | None:
    """Locate a built NSX target's executable/binary under ``build_dir``.

    Mirrors the existing hpx_profiler artifact search so hpx_profiler_power
    (or any future target) resolves the same way across toolchains/layouts.
    """
    artifact_patterns = [
        str(build_dir / target_name),
        str(build_dir / "**" / target_name),
        str(build_dir / "**" / f"{target_name}.axf"),
        str(build_dir / "**" / f"{target_name}.elf"),
        str(build_dir / f"{target_name}.bin"),
        str(build_dir / "**" / f"{target_name}.bin"),
    ]
    for pattern in artifact_patterns:
        matches = [m for m in glob.glob(pattern, recursive=True) if Path(m).is_file()]
        if matches:
            return Path(matches[0])
    return None


def flash_app(ctx: PipelineContext) -> None:
    """Invoke ``nsx flash`` to deploy the binary to the target."""
    assert ctx.firmware_dir is not None
    toolchain = ctx.config.target.toolchain
    nsx_tc = _nsx_toolchain(toolchain)
    nsx_cli.flash(
        ctx.firmware_dir,
        toolchain=nsx_tc,
        jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
        frozen=ctx.config.frozen,
        timeout_s=ctx.config.timeouts.flash_s,
        verbose=ctx.config.verbose,
    )
