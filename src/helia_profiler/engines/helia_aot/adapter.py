"""heliaAOT engine adapter.

Invokes the heliaAOT compiler to produce an NSX module from a .tflite model,
generates a memory-placement attribute header, and declares the ns-cmsis-nn
module it links against. See :mod:`.compile` for platform mapping / AOT
compiler invocation, :mod:`.manifest` for operator manifest and memory-plan
extraction, and :mod:`helia_profiler.engines.cmsis_nn` for ns-cmsis-nn
resolution.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace as _dc_replace
from pathlib import Path
from typing import Any

from ...config import InputFill, ProfileConfig
from ...errors import ConfigError, EngineError
from ...identity import Unknown
from ...lifecycle import InstanceBoundary, Lifecycle
from ...modelcost import analyze_aot_operators, analyze_model
from ...placement import ArenaRole, Placement
from ...results import NsxModuleRef
from ...results.dependencies import installed_aot_source
from .. import EngineType
from ..base import (
    ArenaRegion,
    EngineSource,
    FixtureModelPreparation,
    HeliaAotArtifacts,
    PsramWeightsSource,
)
from ..cmsis_nn import cmsis_nn_cmake_vars, cmsis_nn_module_ref
from ..ethos_u import nsx_npu_module_ref
from .compile import (
    _DEFAULT_MODULE_NAME,
    _DEFAULT_PREFIX,
    _check_helia_aot_version,
    _merged_aot_args,
    _resolve_aot_platform,
    _run_aot_compiler,
    _validate_pragmas,
    _write_attributes_header,
    effective_aot_args,
    effective_config_digest,
    effective_config_pairing_digest,
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


def _user_tensor_rules(aot_args: dict[str, Any]) -> list[Any]:
    return aot_args["memory"].get("tensors") or []


def _psram_requested(config: ProfileConfig, aot_args: dict[str, Any]) -> bool:
    """True when any part of this config steers tensors into PSRAM.

    Two routes exist: the coarse split fields, and per-tensor rules in
    ``aot_args.memory.tensors`` (``memory: psram`` or a staged
    ``constant_destination_memory: psram``).
    """
    if Placement.PSRAM in (config.model.arena_location, config.model.weights_location):
        return True
    for rule in _user_tensor_rules(aot_args):
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


def _external_arena_mode(aot_args: dict[str, Any]) -> bool:
    """True when arena buffers are host-app allocated and bound at runtime.

    heliaAOT's entire PSRAM path — sidecar constant blobs, ``nsx_psram_init``,
    ``nsx_psram_write``, ``bind_arena`` — renders only in this mode
    (``main_aot.cc.j2`` gates the whole region on ``not allocate_arenas``).
    Single source of truth for that flag: ``prepare()`` and
    :meth:`HeliaAOTAdapter.check_psram_placement` must agree on it, or the
    memory plan and the generated firmware can disagree about where the
    tensors live (#219).
    """
    return not aot_args["memory"].get("allocate_arenas", True)


def _check_pool_bound(regions: list[ArenaRegion], *, allocate_arenas: bool) -> None:
    """Refuse a stream pool the firmware would leave unbound.

    heliaAOT expects the application to bind its stream pool in both arena
    modes, and ``model_init`` returns its arena-unbound status (10) until it
    is. hpx's firmware binds regions only in external-arena mode.
    """
    if not allocate_arenas:
        return
    pools = [region.region_id for region in regions if region.role is ArenaRole.POOL]
    if pools:
        raise EngineError(
            f"heliaAOT emitted a stream pool (arena {pools[0]}) that hpx binds only in "
            "external-arena mode, and this conversion allocates its own arenas.",
            hint="Set engine.config.aot_args.memory.allocate_arenas: false so the firmware "
            "allocates and binds every arena, the stream pool included. A fixed fixture "
            "does not take a stream pool: profile the module with hpx profile.",
        )


#: Input types whose every bit pattern is a valid data value.
_LCG_INPUT_DTYPES = frozenset({"int8", "uint8", "int16"})


def _check_input_fill(config: ProfileConfig, result: Any) -> None:
    """Refuse ``input_fill: lcg`` unless every model input is int8, uint8 or int16.

    The LCG writes raw bytes. A float input reads them as NaN, infinities and
    huge values; a 32- or 64-bit integer input holds indices or lengths in
    practice, and not every kernel bounds those.
    """
    if config.profiling.input_fill is not InputFill.LCG:
        return
    other = [
        f"{tensor.name or tensor.id} ({tensor.dtype})"
        for tensor in result.tensors
        if tensor.input_index is not None and str(tensor.dtype) not in _LCG_INPUT_DTYPES
    ]
    if other:
        raise EngineError(
            "profiling.input_fill: lcg fills inputs with raw bytes, so it needs int8, "
            f"uint8 or int16 inputs; this model has {', '.join(other)}.",
            hint="Use profiling.input_fill: zero for this model.",
        )


def _report_names(prefix: str) -> tuple[str, str]:
    return f"{prefix}_plan.json", f"{prefix}_report.json"


def _clear_reports(output_dir: Path, prefix: str) -> None:
    """Drop plan and report files an earlier conversion left, so a build pins only its own."""
    for name in _report_names(prefix):
        for stale in output_dir.rglob(name):
            stale.unlink()


def _report_files(output_dir: Path, prefix: str) -> tuple[Path, ...]:
    """The plan and report JSON this conversion wrote, when the installed heliaAOT writes them."""
    found: list[Path] = []
    for name in _report_names(prefix):
        matches = sorted(output_dir.rglob(name))
        if len(matches) > 1:
            raise EngineError(f"More than one heliaAOT {name} in the work directory")
        found += matches
    return tuple(found)


def _transfer_cmake_target(module_dir: Path, prefix: str, cmake_name: str) -> str | None:
    """The provider target heliaAOT declares beside a module that emitted its header.

    The name is checked against the module's own CMakeLists.txt, so a module
    whose emission names it differently fails here rather than at configure.
    """
    header = module_dir / "includes-api" / f"{prefix}_nsx_transfer.h"
    if not header.is_file():
        return None
    target = f"nsx::{cmake_name}_nsx_transfer"
    cmake = module_dir / "CMakeLists.txt"
    declared = cmake.read_text(encoding="utf-8") if cmake.is_file() else ""
    if f"add_library({target} ALIAS " not in declared:
        raise EngineError(
            f"heliaAOT emitted {header.name}, but {cmake} declares no {target} target",
            hint="Convert with a heliaAOT whose nsx module builds its transfer provider "
            f"as {target}, or with module.transfer_provider none.",
        )
    return target


def _installed_source() -> EngineSource | None:
    source = installed_aot_source()
    return EngineSource(**source) if source is not None else None


def _build_extra_modules(
    config: ProfileConfig,
    cmsis_nn_ref: NsxModuleRef,
    module_name: str,
    aot_module_dir: Path,
) -> list[NsxModuleRef]:
    """Assemble the NSX module list the firmware project must vendor.

    With ``engine.backend: ethos_u`` the generated module contains Ethos-U
    kernels that call the Ethos-U core driver; the nsx-npu registry module
    vendors that driver plus the ``nsx_npu_init()`` bring-up helper. It must
    precede the AOT module so its ``nsx::npu`` target exists when the AOT
    module's CMakeLists resolves the driver dependency.
    """
    extra_modules = [cmsis_nn_ref]
    if config.engine.backend == "ethos_u":
        extra_modules.append(nsx_npu_module_ref())
    extra_modules.append(NsxModuleRef(name=module_name, path=aot_module_dir))
    return extra_modules


class HeliaAOTAdapter:
    """Adapter for heliaAOT — Ambiq's ahead-of-time neural network compiler.

    Workflow:
    1. Validate profiler board maps to a known AOT platform.
    2. Invoke ``helia-aot convert`` on the input .tflite model as an NSX module.
    3. Validate generated memory-placement pragmas match expectations.
    4. Resolve the ns-cmsis-nn NSX module (baseline ref, or a user override).
    5. Generate an attribute header mapping AOT macros → Ambiq sections.
    6. Register the AOT output as an NSX module (it is already NSX-native).
    7. Return ``HeliaAotArtifacts`` with template vars and cmake_vars.
    """

    @property
    def name(self) -> str:
        return "heliaAOT"

    def prepare_fixture_model(self, path: Path, *, single_io: bool) -> FixtureModelPreparation:
        from ...firmware.op_resolver import ResolverPlan
        from ...fixtures.analysis import prepare_litert_fixture

        return prepare_litert_fixture(
            path,
            single_io=single_io,
            resolver=lambda analysis, facts: ResolverPlan(mode="engine", registrations=()),
        )

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

    @property
    def lifecycles(self) -> frozenset[Lifecycle]:
        return frozenset({Lifecycle.CARRY, Lifecycle.SINGLE})

    def lifecycle_refusal(self, lifecycle: Lifecycle) -> str:
        return f"{self.name} firmware does not implement the {lifecycle.value} lifecycle"

    @property
    def instance_boundary(self) -> InstanceBoundary:
        return InstanceBoundary.BOOT

    def first_invoke_initializer(self, model_initializer: bool | Unknown) -> bool | Unknown:
        # heliaAOT sets a CALL_ONCE's constant initial values in the operator
        # init hooks that model_init() runs, before the first model_run().
        return False

    def placement_config(self, config: ProfileConfig) -> ProfileConfig:
        # PSRAM constants render only in external-arena mode (check_psram_placement
        # refuses anything else), so PSRAM weights imply that converter setting.
        if config.model.weights_location is not Placement.PSRAM:
            return config
        aot_args = dict(config.engine.config.get("aot_args", {}))
        memory = aot_args.get("memory")
        aot_args["memory"] = {**({} if memory is None else dict(memory)), "allocate_arenas": False}
        return _dc_replace(
            config,
            engine=_dc_replace(
                config.engine, config={**config.engine.config, "aot_args": aot_args}
            ),
        )

    def check_psram_placement(self, config: ProfileConfig) -> None:
        aot_args = _merged_aot_args(config)
        if _external_arena_mode(aot_args) or not _psram_requested(config, aot_args):
            return
        # Under the default allocate_arenas=True, main_aot.cc.j2 renders
        # ZERO PSRAM code while plan_memory happily reports tensors placed
        # there — the run then hangs at the host's PSRAM handshake with
        # nothing to blame but the hardware.  Refuse in stage 0 instead.
        raise ConfigError(
            "helia-aot PSRAM placement requires external-arena mode, "
            "which is disabled (aot_args.memory.allocate_arenas defaults to true).",
            hint=(
                "Set memory.allocate_arenas: false in engine.config.aot_args "
                "or the engine.config_path file — "
                "heliaAOT then writes its sidecar constant blobs into PSRAM "
                "itself at boot. Without it the generated firmware contains "
                "no PSRAM code at all, while the memory plan claims tensors "
                "live there."
            ),
        )

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        # When the user pins the arena to a specific region, move
        # *scratch* arenas there.  Persistent/constant regions stay
        # where the AOT planner placed them — those typically hold
        # weights/state and have separate placement controls.
        return [
            _dc_replace(r, placement=target) if r.role is ArenaRole.SCRATCH else r for r in regions
        ]

    def prepare(self, config: ProfileConfig, work_dir: Path) -> HeliaAotArtifacts:
        prefix = config.engine.config.get("prefix", _DEFAULT_PREFIX)
        module_name = config.engine.config.get("module_name", _DEFAULT_MODULE_NAME)

        aot_version = _check_helia_aot_version(
            allow_unsupported_version=config.engine.allow_unsupported_version
        )
        aot_platform = _resolve_aot_platform(config)

        aot_args = _merged_aot_args(config)
        memory_intent_json = json.dumps(aot_args["memory"], sort_keys=True)
        compiler_config = _dc_replace(
            config,
            engine=_dc_replace(
                config.engine,
                config_path=None,
                config={**config.engine.config, "aot_args": aot_args},
            ),
        )
        allocate_arenas = not _external_arena_mode(aot_args)
        user_memory_config = config.engine.config_path is not None or bool(
            _user_tensor_rules(aot_args)
        )

        compiler_args = effective_aot_args(compiler_config)
        effective_config_sha256, effective_config_keys = effective_config_digest(
            compiler_args,
            module_name=module_name,
            prefix=prefix,
            aot_platform=aot_platform,
        )
        effective_config_pairing_sha256, _ = effective_config_pairing_digest(
            compiler_args,
            module_name=module_name,
            prefix=prefix,
            aot_platform=aot_platform,
        )

        aot_output_dir = work_dir / "aot_output"
        aot_module_dir = aot_output_dir / module_name
        _clear_reports(aot_output_dir, prefix)
        result = _run_aot_compiler(
            compiler_config,
            aot_output_dir,
            module_name,
            prefix,
            aot_platform,
            compiler_args,
        )

        _check_input_fill(config, result)
        # heliaAOT transforms/fuses ops, so the generated graph may differ
        # from the .tflite; the views describe what it actually emits.
        compiled_analysis = analyze_aot_operators(
            result.operators, result.tensors, analyze_model(config.model.path)
        )
        op_manifest = _extract_operator_manifest(result, prefix)
        manifest_path = work_dir / "aot_operator_manifest.json"
        manifest_path.write_text(json.dumps(op_manifest, indent=2), encoding="utf-8")
        log.info("Extracted %d AOT operators", len(op_manifest))

        _validate_pragmas(aot_module_dir, prefix)
        cmsis_nn_ref = cmsis_nn_module_ref(config, work_dir)

        # AOT output is already a valid NSX module, so no
        # wrapper is generated here — just the attribute header.
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

        # The plan_memory stage validates this plan against the SoC's
        # physical memory layout.
        memory_plan = _extract_memory_plan(result, allocate_arenas=allocate_arenas)
        arena_regions = _extract_arena_regions(result, prefix)
        _check_pool_bound(arena_regions, allocate_arenas=allocate_arenas)

        extra_modules = _build_extra_modules(config, cmsis_nn_ref, module_name, aot_module_dir)

        return HeliaAotArtifacts(
            engine_type=EngineType.HELIA_AOT,
            engine_backend=config.engine.backend,
            extra_modules=extra_modules,
            cmake_vars={
                attr_var: str(attr_header),
                **engine_cmake_vars,
            },
            engine_header=f"{prefix}_model.h",
            aot_prefix=prefix,
            aot_module_name=module_name,
            aot_cmake_target=f"nsx::{cmake_name}",
            aot_transfer_cmake_target=_transfer_cmake_target(aot_module_dir, prefix, cmake_name),
            helia_aot_version=aot_version,
            aot_allocate_arenas=allocate_arenas,
            aot_user_memory_config=user_memory_config,
            aot_effective_config_sha256=effective_config_sha256,
            aot_effective_config_keys=effective_config_keys,
            aot_effective_config_pairing_sha256=effective_config_pairing_sha256,
            aot_memory_intent_json=memory_intent_json,
            aot_arena_regions=arena_regions,
            aot_op_manifest=op_manifest or None,
            compiled_analysis=compiled_analysis,
            memory_plan=memory_plan,
            source=_installed_source(),
            report_files=_report_files(aot_output_dir, prefix),
        )
