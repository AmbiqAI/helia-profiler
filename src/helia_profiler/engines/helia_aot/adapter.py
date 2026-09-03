"""heliaAOT engine adapter.

Invokes the heliaAOT compiler to produce an NSX module from a .tflite model,
generates a memory-placement attribute header, and wraps ns-cmsis-nn as a
local NSX module for the profiler firmware build. See :mod:`.compile` for
platform mapping / AOT compiler invocation, :mod:`.manifest` for operator
manifest and memory-plan extraction, and :mod:`.cmsis_nn` for ns-cmsis-nn
resolution and NSX module wrapping.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from dataclasses import replace as _dc_replace

from ...config import ProfileConfig
from ...errors import ConfigError, EngineError
from ...placement import ArenaRole, Placement
from ...results import NsxModuleRef
from .. import EngineType
from ..base import ArenaRegion, HeliaAotArtifacts, PsramWeightsSource
from ..cmsis_nn import cmsis_nn_cmake_vars, cmsis_nn_module_ref
from .compile import (
    _DEFAULT_MODULE_NAME,
    _DEFAULT_PREFIX,
    _check_helia_aot_version,
    _resolve_aot_platform,
    _run_aot_compiler,
    _validate_pragmas,
    _write_attributes_header,
)
from .manifest import _extract_arena_regions, _extract_memory_plan, _extract_operator_manifest

log = logging.getLogger("hpx")


def _engine_cmake_vars(config: ProfileConfig) -> dict[str, str]:
    """The ns-cmsis-nn kernel switches plus the NSX linker profile, if set."""
    cmake_vars = cmsis_nn_cmake_vars(config)
    linker_profile = config.engine.config.get("linker_profile")
    if linker_profile:
        cmake_vars["NSX_LINKER_PROFILE"] = str(linker_profile)
    return cmake_vars


def _psram_requested(config: ProfileConfig) -> bool:
    """True when any part of this config steers tensors into PSRAM.

    Two routes exist: the coarse split fields, and per-tensor rules in
    ``aot_args.memory.tensors`` (``memory: psram`` or a staged
    ``constant_destination_memory: psram``).  Detection is best-effort on
    the raw dicts — malformed rules are ``EngineError``s for
    ``_prepare_aot_memory_config`` later, not this check's concern.
    """
    if Placement.PSRAM in (config.model.arena_location, config.model.weights_location):
        return True
    tensors = config.engine.config.get("aot_args", {}).get("memory", {}).get("tensors", [])
    if not isinstance(tensors, list):
        return False
    for rule in tensors:
        if not isinstance(rule, dict):
            continue
        attributes = rule.get("attributes")
        if not isinstance(attributes, dict):
            continue
        if "psram" in (
            attributes.get("memory"),
            attributes.get("constant_destination_memory"),
        ):
            return True
    return False


def _external_arena_mode(config: ProfileConfig) -> bool:
    """True when arena buffers are host-app allocated and bound at runtime.

    heliaAOT's entire PSRAM path — sidecar constant blobs, ``nsx_psram_init``,
    ``nsx_psram_write``, ``bind_arena`` — renders only in this mode
    (``main_aot.cc.j2`` gates the whole region on ``not allocate_arenas``).
    Single source of truth for that flag: ``prepare()`` and
    :meth:`HeliaAOTAdapter.check_psram_placement` must agree on it, or the
    memory plan and the generated firmware can disagree about where the
    tensors live (#219).
    """
    return (
        not config.engine.config.get("aot_args", {}).get("memory", {}).get("allocate_arenas", True)
    )


class HeliaAOTAdapter:
    """Adapter for heliaAOT — Ambiq's ahead-of-time neural network compiler.

    Workflow:
    1. Validate profiler board maps to a known AOT platform.
    2. Invoke ``helia-aot convert`` on the input .tflite model (ModuleType.nsx).
    3. Validate generated memory-placement pragmas match expectations.
    4. Resolve the ns-cmsis-nn NSX module (baseline ref, or a user override).
    5. Generate an attribute header mapping AOT macros → Ambiq sections.
    6. Register the AOT output as an NSX module (it is already NSX-native).
    7. Return ``HeliaAotArtifacts`` with template vars and cmake_vars.
    """

    @property
    def name(self) -> str:
        return "heliaAOT"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.HELIA_AOT

    @property
    def psram_weights_source(self) -> PsramWeightsSource:
        # Constants ship as flash-resident sidecar blobs that the firmware
        # writes into PSRAM itself; there is no HPX_PSRAM_READY handshake
        # and no host upload.  check_psram_placement() guards the config
        # that actually renders that machinery.
        return PsramWeightsSource.SELF_CONTAINED

    def check_psram_placement(self, config: ProfileConfig) -> None:
        if _external_arena_mode(config) or not _psram_requested(config):
            return
        # Under the default allocate_arenas=True, main_aot.cc.j2 renders
        # ZERO PSRAM code while plan_memory happily reports tensors placed
        # there — the run then hangs at the host's PSRAM handshake with
        # nothing to blame but the hardware.  Refuse in stage 0 instead.
        raise ConfigError(
            "helia-aot PSRAM placement requires external-arena mode, "
            "which is disabled (aot_args.memory.allocate_arenas defaults to true).",
            hint=(
                "Set engine.config.aot_args.memory.allocate_arenas: false — "
                "heliaAOT then writes its sidecar constant blobs into PSRAM "
                "itself at boot. Without it the generated firmware contains "
                "no PSRAM code at all, while the memory plan claims tensors "
                "live there."
            ),
        )

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        del tcm_cap, sram_cap
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        # When the user pins the arena to a specific region, move
        # *scratch* arenas there.  Persistent/constant regions stay
        # where the AOT planner placed them — those typically hold
        # weights/state and have separate placement controls.
        if target not in (Placement.PSRAM, Placement.TCM, Placement.SRAM, Placement.MRAM):
            return regions

        return [
            _dc_replace(r, placement=target) if r.role is ArenaRole.SCRATCH else r for r in regions
        ]

    def prepare(self, config: ProfileConfig, work_dir: Path) -> HeliaAotArtifacts:
        prefix = config.engine.config.get("prefix", _DEFAULT_PREFIX)
        module_name = config.engine.config.get("module_name", _DEFAULT_MODULE_NAME)

        # 0. Verify installed helia-aot satisfies the floor.
        aot_version = _check_helia_aot_version(config)

        # 1. Resolve AOT platform from profiler board
        aot_platform = _resolve_aot_platform(config)

        # 2. Run AOT compilation (programmatic API → CodeGenContext)
        aot_output_dir = work_dir / "aot_output"
        aot_module_dir = aot_output_dir / module_name
        codegen_ctx = _run_aot_compiler(
            config,
            aot_output_dir,
            module_name,
            prefix,
            aot_platform,
        )

        # 3. Extract operator manifest from the CodeGenContext.
        #    heliaAOT transforms/fuses ops — the AIR graph may differ
        #    significantly from the original TFLite flatbuffer.  The
        #    manifest captures what the AOT compiler *actually* emits.
        op_manifest = _extract_operator_manifest(codegen_ctx)
        if op_manifest:
            manifest_path = work_dir / "aot_operator_manifest.json"
            manifest_path.write_text(json.dumps(op_manifest, indent=2), encoding="utf-8")
            log.info(
                "Extracted %d AOT operators from CodeGenContext",
                len(op_manifest),
            )
        else:
            log.warning(
                "Could not extract operator manifest from AOT — "
                "per-layer names will fall back to op_N."
            )

        # 4. Validate memory-placement pragmas in generated code
        _validate_pragmas(aot_module_dir, prefix)

        # 5. Resolve the ns-cmsis-nn NSX module (declared at the baseline's
        #    qualified ref by default; a user ref or vendored path overrides).
        cmsis_nn_ref = cmsis_nn_module_ref(config, work_dir)

        # 6. AOT output is already a valid NSX module (ModuleType.nsx).
        # Just generate the memory-placement attribute header and tell
        # the AOT module's CMakeLists.txt where to find it.
        attr_header = _write_attributes_header(aot_module_dir, prefix)
        cmake_name = module_name.replace("-", "_")
        attr_var = f"{cmake_name.upper()}_ATTRIBUTES_HEADER"

        log.info(
            "AOT compiled %s → %s (prefix=%s, platform=%s)",
            config.model.path.name,
            aot_module_dir,
            prefix,
            aot_platform,
        )

        engine_cmake_vars = _engine_cmake_vars(config)

        # Build a MemoryPlan from the AOT codegen context so the
        # plan_memory stage can validate placement against the SoC's
        # physical memory layout.
        # Extract arena binding info for external-arena mode — resolved
        # BEFORE plan extraction, which needs it to hint the symbols the
        # templates actually emit in each mode (#179 review M-4).
        allocate_arenas = not _external_arena_mode(config)
        memory_plan = _extract_memory_plan(codegen_ctx, prefix, allocate_arenas=allocate_arenas)
        arena_regions = _extract_arena_regions(codegen_ctx, prefix)

        return HeliaAotArtifacts(
            engine_type=EngineType.HELIA_AOT,
            extra_modules=[
                cmsis_nn_ref,
                NsxModuleRef(name=module_name, path=aot_module_dir),
            ],
            cmake_vars={
                attr_var: str(attr_header),
                **engine_cmake_vars,
            },
            engine_header=f"{prefix}_model.h",
            aot_prefix=prefix,
            aot_module_name=module_name,
            aot_cmake_target=f"nsx::{cmake_name}",
            helia_aot_version=aot_version,
            aot_allocate_arenas=allocate_arenas,
            aot_arena_regions=arena_regions,
            aot_op_manifest=op_manifest or None,
            memory_plan=memory_plan,
        )
