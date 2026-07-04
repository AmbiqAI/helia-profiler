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
from ...errors import EngineError
from ...placement import ArenaRole, Placement
from ...results import NsxModuleRef
from .. import EngineType
from ..base import ArenaRegion, EngineArtifacts
from .cmsis_nn import cmsis_nn_module_ref
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


class HeliaAOTAdapter:
    """Adapter for heliaAOT — Ambiq's ahead-of-time neural network compiler.

    Workflow:
    1. Validate profiler board maps to a known AOT platform.
    2. Invoke ``helia-aot convert`` on the input .tflite model (ModuleType.nsx).
    3. Validate generated memory-placement pragmas match expectations.
    4. Resolve the ns-cmsis-nn (CMSIS-NN fork) source tree.
    5. Generate an attribute header mapping AOT macros → Ambiq sections.
    6. Wrap ns-cmsis-nn as a local NSX module (AOT output is already NSX-native).
    7. Return ``EngineArtifacts`` with template vars and cmake_vars.
    """

    @property
    def name(self) -> str:
        return "heliaAOT"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.HELIA_AOT

    def supports_runtime_split(self) -> bool:
        # AOT bakes per-tensor placement into the compiled module; the
        # profiler-config split overrides cannot influence weights.
        return False

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        # AOT: keep simple — auto means weights in MRAM, arena in TCM.
        # The AOT compiler further redistributes tensors via PUT_IN_*
        # macros on the codegen side.
        del sram_cap
        arena = Placement.TCM if tcm_cap > 0 else Placement.SRAM
        return arena, Placement.MRAM

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

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        prefix = config.engine.config.get("prefix", _DEFAULT_PREFIX)
        module_name = config.engine.config.get("module_name", _DEFAULT_MODULE_NAME)

        # 0. Verify installed helia-aot satisfies the floor.
        _check_helia_aot_version()

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
            manifest_path.write_text(json.dumps(op_manifest, indent=2))
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

        # 5. Resolve the ns-cmsis-nn NSX module (registry by default, or a
        #    vendored local module when a custom path is provided).
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

        # Forward CMSIS-NN build options from engine config
        cmsis_nn_cmake: dict[str, str] = {}
        if config.engine.config.get("cmsis_nn_requantize_inline_asm", True):
            cmsis_nn_cmake["NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM"] = "ON"
        linker_profile = config.engine.config.get("linker_profile")
        if linker_profile:
            cmsis_nn_cmake["NSX_LINKER_PROFILE"] = str(linker_profile)

        # Build a MemoryPlan from the AOT codegen context so the
        # plan_memory stage can validate placement against the SoC's
        # physical memory layout.
        memory_plan = _extract_memory_plan(codegen_ctx)

        # Extract arena binding info for external-arena mode
        allocate_arenas = (
            config.engine.config.get("aot_args", {}).get("memory", {}).get("allocate_arenas", True)
        )
        arena_regions = _extract_arena_regions(codegen_ctx, prefix)

        return EngineArtifacts(
            engine_type=EngineType.HELIA_AOT,
            extra_modules=[
                cmsis_nn_ref,
                NsxModuleRef(name=module_name, path=aot_module_dir),
            ],
            cmake_vars={
                attr_var: str(attr_header),
                **cmsis_nn_cmake,
            },
            engine_header=f"{prefix}_model.h",
            aot_prefix=prefix,
            aot_module_name=module_name,
            aot_cmake_target=f"nsx::{cmake_name}",
            aot_allocate_arenas=allocate_arenas,
            aot_arena_regions=arena_regions,
            aot_op_manifest=op_manifest or None,
            memory_plan=memory_plan,
        )
