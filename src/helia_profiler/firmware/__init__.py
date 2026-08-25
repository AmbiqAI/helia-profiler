"""Firmware generation — NSX app scaffolding for the profiler.

This module provides the interface between the pipeline stages and the
low-level firmware template rendering + NSX build system.  Each function
receives a ``PipelineContext`` and operates on the fields set by prior stages.
"""

from __future__ import annotations

# ``glob`` and ``shutil`` stay imported as modules even though the code that
# uses them moved to .build / .launcher / .segger: tests monkeypatch
# ``helia_profiler.firmware.glob.glob`` and
# ``helia_profiler.firmware.shutil.which``, so the package must keep these
# module attributes (the split modules import the same module objects, so the
# patches keep landing where the code reads them at call time).
import glob
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .. import nsx as nsx_cli
from ..compatibility import ENGINE_OWNED_MODULE_NAMES
from ..config import PowerFirmware, Transport, WindowMode
from ..engines import EngineType
from ..engines.base import ArenaRegion, HeliaAotArtifacts
from ..errors import ConfigError
from ..errors import FirmwareError
from ..placement import Placement
from ..platform import get_soc_for_board
from .context import FirmwareRenderContext, _resolve_pmu_passes
# NB: measured_power_fingerprint and _resolve_module_list below look unused
# in this module but are LIVE re-export surface — report/manifest.py,
# report/summary.py, and tests import them from the package root. Do not
# remove in a dead-import cleanup (#194 review).
from .fingerprint import measured_power_fingerprint
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

# The compiler-launcher, SEGGER RTT vendoring, generated-C-header, and NSX
# build/flash invocation APIs live in dedicated modules (extracted at the
# module size ceiling — the elf_inventory precedent, see toolchain_probe);
# re-exported here so callers keep one import surface.
from .build import (
    _DEFAULT_RTT_BUFFER_SIZE_UP,
    _find_target_binary,
    _nsx_toolchain,
    _rtt_buffer_size_up,
    build_app,
    flash_app,
)
from .headers import _blob_to_header, _model_to_header
from .launcher import (
    _AUTO_COMPILER_LAUNCHERS,
    _DISABLED_LAUNCHER_VALUES,
    _LAUNCHER_UNSUPPORTED_TOOLCHAINS,
    _launcher_basename,
    _launcher_supports_toolchain,
    _resolve_compiler_launcher,
)
from .segger import (
    _bundled_segger_rtt_dir,
    _copy_segger_rtt,
    _find_segger_rtt_dir,
    _is_segger_rtt_root,
)

if TYPE_CHECKING:
    from ..config import ProfileConfig
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_app(ctx: PipelineContext) -> Path:
    """Render firmware templates into an NSX-compatible profiler app.

    Returns the path to the generated app directory inside ``ctx.work_dir``.
    """
    soc = ctx.resolved_soc
    board = ctx.resolved_board
    artifacts = ctx.prepared_artifacts

    from ..dependencies import create_workspace

    workspace = ctx.dependency_workspace or create_workspace(ctx)
    ctx.dependency_workspace = workspace
    app_dir = workspace.root / "profiler_app"
    app_dir.mkdir(parents=True, exist_ok=True)

    config = ctx.config
    weights_region = ctx.weights_region or Placement.MRAM
    arena_region = ctx.arena_region or Placement.TCM
    power_sync_enabled = config.power.gated_external_capture
    # Dedicated power binary (hpx_profiler_power): rendered/built only when
    # power capture is actually requested AND the dedicated firmware mode is
    # selected, so non-power runs (and "shared"-mode power runs, which reuse
    # the transport binary and never touch hpx_profiler_power) keep an
    # unchanged CMakeLists.txt / firmware-render digest (see AGENTS.md WP2).
    power_binary_enabled = config.power.enabled and config.power.firmware is PowerFirmware.DEDICATED
    aot_arena_regions = _resolved_aot_arena_regions(ctx)

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

    # On-target INA228 power monitor: the dedicated power binary reads the
    # monitor over I2C, so pull in the transport and driver modules.
    # nsx-sensors' own closure (nsx-core/nsx-spi) resolves transitively
    # during nsx lock. monitor_selected is the same predicate
    # PowerMonitorContext.from_config gates on — single-sourced so the two
    # gates cannot diverge.
    if power_binary_enabled and config.power.monitor_selected:
        module_names = {m.name for m in module_specs}
        for name in ("nsx-i2c", "nsx-sensors"):
            if name not in module_names:
                module_specs.append(NsxModuleSpec(name, _module_project(name, profile)))
                module_names.add(name)

    # BLE-controller-reset GPIO drive (Blue-variant boards, dedicated power
    # binary only — see _ble_reset.j2) needs nsx-gpio even when power_sync
    # itself is off (e.g. power.mode == "internal"). The header include and
    # the CMake link line both gate on render_context.power_binary_needs_gpio,
    # which is this same condition — the CMake side used to check only
    # power_sync_enabled, so an internal-mode run on a Blue board selected
    # the module here, emitted the include, and then failed to compile.
    if power_binary_enabled and board.ble_reset_gpio_pin is not None:
        module_names = {m.name for m in module_specs}
        if "nsx-gpio" not in module_names:
            module_specs.append(NsxModuleSpec("nsx-gpio", _module_project("nsx-gpio", profile)))
            module_names.add("nsx-gpio")

    # Build module descriptors (name + local flag + optional overrides)
    nsx_overrides = config.build.nsx_modules
    board_mod = _board_module_name(board.name)
    compatibility = config.compatibility
    if compatibility is None:
        raise ConfigError(
            "Compatibility baseline was not resolved before firmware generation",
            hint="Construct ProfileConfig through load_config().",
        )
    project_overrides = _resolve_project_overrides(
        module_specs,
        nsx_overrides,
        compatibility.baseline,
    )
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

    # Warn about overrides that didn't match any module in the build. Modules
    # that engine adapters resolve themselves (nsx-helia-rt, nsx-cmsis-nn) are
    # configured via `engine.config` (dist_path / source_path / source /
    # cmsis_nn_path — see compatibility._ENGINE_SOURCE_OVERRIDE_KEYS), not
    # `build.nsx_modules` — call that out explicitly. Other extra modules
    # (e.g. TFLM's nsx-tflite-micro / arm-cmsis-nn) have no engine.config
    # equivalent, so they fall back to the generic "unrecognized name" hint.
    unmatched = set(nsx_overrides.keys()) - matched_overrides
    for name in sorted(unmatched):
        if name in ENGINE_OWNED_MODULE_NAMES:
            log.warning(
                "build.nsx_modules override '%s' targets an engine-provided module "
                "and was not applied — configure it via engine.config "
                "(dist_path / source_path / source / cmsis_nn_path) instead.",
                name,
            )
        else:
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
            # Engine-provided modules are configured through `engine.config`
            # (dist_path / source_path / source / cmsis_nn_path), not
            # `build.nsx_modules` — an override here is already reported by
            # the "unmatched override" warning above. Fall back to the
            # baseline's qualified ref instead of a hard-coded default.
            if extra_mod.ref:
                entry["ref"] = extra_mod.ref
            elif any(module.name == extra_mod.name for module in compatibility.baseline.modules):
                entry["ref"] = compatibility.baseline.module(extra_mod.name).ref
            modules.append(entry)

    log.info("NSX modules: %s", ", ".join(str(m["name"]) for m in modules))

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
            module_registry_yaml=_render_module_registry(
                profile,
                project_overrides,
                {
                    module.name: (module.project or module.name, module.ref)
                    for module in artifacts.extra_modules
                    if not module.local and module.ref
                },
                app_modules={spec.name: spec.project for spec in module_specs},
            ),
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
        _copy_segger_rtt(src_dir, config.target.segger_rtt_path)

    extreme_mode_safe = arena_region is Placement.TCM and weights_region is Placement.TCM
    if config.profiling.extreme_mode and not extreme_mode_safe:
        log.warning(
            "profiling.extreme_mode=true ignored: requires arena+weights in TCM "
            "(current: arena=%s, weights=%s). SSRAM/NVM power-down would corrupt "
            "model storage.",
            arena_region,
            weights_region,
        )

    if engine_type is EngineType.EXECUTORCH:
        # No ExecuTorch-specific artifact field is read here — the PTE runtime
        # contract reaches the template through render_context.engine, which
        # narrowed once in FirmwareRenderContext.from_pipeline_context.
        if weights_region != "psram":
            _write_text(
                src_dir / "model_data.h",
                _model_to_header(config.model.path, weights_region),
            )
        _write_text(
            src_dir / "main.cc",
            _jinja_env.get_template("main_executorch.cc.j2").render(**template_vars),
        )
    elif engine_type is EngineType.HELIA_AOT:
        # --- AOT engine: use AOT-specific main template, no model embedding ---
        # The heliaAOT adapter is the only producer of this engine_type, and
        # HeliaAotArtifacts pins the pairing, so the narrowing is total — but
        # stated as a raise, not an assert: this was the last place a stage
        # product's narrowing rode on an -O-strippable assert.
        if not isinstance(artifacts, HeliaAotArtifacts):
            raise FirmwareError(
                f"engine_type is helia-aot but the prepared artifacts are "
                f"{type(artifacts).__name__} — adapter/artifact pairing broke. "
                "This is a bug in heliaPROFILER — please file an issue."
            )

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
            _jinja_env.get_template("main_aot.cc.j2").render(**template_vars),
        )
        if power_binary_enabled:
            # Same template, power_only=True: no transport init, no per-layer
            # PMU passes -- see main_aot.cc.j2's power_only branches (WP1).
            _write_text(
                src_dir / "main_power.cc",
                _jinja_env.get_template("main_aot.cc.j2").render(
                    **render_context.to_template_vars(power_only=True),
                ),
            )
    else:
        # --- TFLM / heliaRT: embed model as byte array, use TFLM profiler ---
        if weights_region != "psram":
            model_header = _model_to_header(config.model.path, weights_region)
            _write_text(src_dir / "model_data.h", model_header)

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
                    **render_context.to_template_vars(power_only=True),
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


def _resolved_aot_arena_regions(ctx: PipelineContext) -> list[ArenaRegion]:
    """Return the same effective AOT arena placement for every render pass."""
    artifacts = ctx.prepared_artifacts
    if not isinstance(artifacts, HeliaAotArtifacts):
        return []
    adapter = ctx.prepared_adapter
    has_custom_aot_memory = ctx.config.engine.config_path is not None or bool(
        ctx.config.engine.config.get("aot_args", {}).get("memory", {}).get("tensors")
    )
    if has_custom_aot_memory:
        return list(artifacts.aot_arena_regions)
    return adapter.apply_arena_placement_override(
        list(artifacts.aot_arena_regions),
        ctx.arena_region or Placement.TCM,
    )


def render_power_source(ctx: PipelineContext, *, inference_count: int) -> Path:
    """Rewrite only the dedicated power source with a host-selected fixed N."""
    if inference_count < 1:
        raise FirmwareError("Power inference count must be at least 1.")
    if ctx.firmware_dir is None or ctx.soc is None or ctx.engine_artifacts is None:
        raise FirmwareError(
            "Cannot render power firmware before application generation and engine preparation."
        )

    render_context = FirmwareRenderContext.from_pipeline_context(
        ctx,
        arena_regions=_resolved_aot_arena_regions(ctx),
    )
    template_vars = render_context.to_template_vars(power_only=True)
    template_vars.update(
        window_mode=WindowMode.FIXED,
        clean_iters=inference_count,
    )
    template_name = (
        "main_aot.cc.j2"
        if ctx.engine_artifacts.engine_type is EngineType.HELIA_AOT
        else (
            "main_executorch.cc.j2"
            if ctx.engine_artifacts.engine_type is EngineType.EXECUTORCH
            else "main.cc.j2"
        )
    )
    destination = ctx.firmware_dir / "src" / "main_power.cc"
    _write_text(
        destination,
        _jinja_env.get_template(template_name).render(**template_vars),
    )
    log.info("Rendered fixed-N power source: %s (N=%d)", destination, inference_count)
    return destination
