"""heliaRT engine adapter.

Resolves a heliaRT distribution and installs it as a local NSX module for
the profiler firmware build. See :mod:`.artifacts` for distribution
resolution/version-pinning and :mod:`.nsx_module` for the generated NSX
wrapper.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from ...config import ProfileConfig
from ...errors import EngineError
from ...identity import Unknown
from ...lifecycle import InstanceBoundary, Lifecycle
from ...results import NsxModuleRef
from .. import TFLM_ENGINE_HEADER, EngineType
from ..base import (
    FixtureModelPreparation,
    HeliaRtArtifacts,
    PsramWeightsSource,
    SingleArenaPlacementMixin,
)
from ..cmsis_nn import (
    CMSIS_NN_MODULE,
    cmsis_nn_cmake_vars,
    cmsis_nn_module_ref,
    resolve_cmsis_nn_selector,
)
from ..ethos_u import nsx_npu_module_ref
from .artifacts import (
    HELIART_MODULE,
    HELIART_PROJECT,
    HELIART_RELEASE_TAG,
    HELIART_VERSION,
    _check_version_compatibility,
    _detect_version,
    _resolve_distribution,
    _resolve_source_path,
    _toolchain_tag,
    _verify_prebuilt_archive,
)
from .nsx_module import _install_nsx_module, _install_nsx_module_source

log = logging.getLogger("hpx")

# Ethos-U NPU support: the nsx-npu registry module (identity shared with
# heliaAOT via ..ethos_u) vendors the Ethos-U core driver and the
# nsx_npu_init() bring-up helper; the CMake flag compiles heliaRT's ethos-u
# custom-op kernel against that real driver instead of its host stub.
# Mirrors the hardware-validated NSX npu-tflm app wiring.
_ETHOSU_CMAKE_FLAG = "NSX_HELIA_RT_ENABLE_ETHOSU"
# Older heliaRT trees have Ethos-U only in the root build: their NSX wrapper
# does not declare the flag and silently ignores it.
_ETHOSU_NSX_OPTION = re.compile(rf"^[ \t]*option\s*\(\s*{_ETHOSU_CMAKE_FLAG}\b", re.MULTILINE)


def _add_ethos_u_artifacts(extra_modules: list[NsxModuleRef], cmake_vars: dict[str, str]) -> None:
    extra_modules.append(nsx_npu_module_ref())
    cmake_vars[_ETHOSU_CMAKE_FLAG] = "ON"


def _require_ethos_u_source_support(source_path: Path) -> None:
    """Refuse an Ethos-U build from a source tree whose NSX wrapper lacks the flag."""
    try:
        nsx_cmake = (source_path / "nsx" / "CMakeLists.txt").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError as exc:
        problem = f"its nsx/CMakeLists.txt could not be read ({exc.strerror or exc})"
    else:
        if _ETHOSU_NSX_OPTION.search(nsx_cmake):
            return
        problem = f"its nsx/CMakeLists.txt does not declare {_ETHOSU_CMAKE_FLAG}"
    raise EngineError(
        f"heliaRT source at {source_path} (version {_detect_version(source_path) or 'unknown'}) "
        f"cannot build backend 'ethos_u': {problem}.",
        hint=(
            "Use a heliaRT source tree at helia-rt-v1.18.0 or later, or build the "
            "pinned release by removing engine.config.source_path and unsetting "
            "HELIART_SOURCE_PATH."
        ),
    )


def _reject_config_path(config: ProfileConfig) -> None:
    if config.engine.config_path is not None:
        raise EngineError(
            "heliaRT does not read engine.config_path",
            hint="Set supported heliaRT options inline in engine.config instead.",
        )


def _reject_prebuilt_cmsis_nn_override(config: ProfileConfig) -> None:
    selected = resolve_cmsis_nn_selector(config, CMSIS_NN_MODULE)
    if selected is not None:
        raise EngineError(
            f"heliaRT prebuilt distributions cannot apply {selected.selector}; "
            "CMSIS-NN is already compiled into the archive.",
            hint="Remove the CMSIS-NN override or use a registry/source build.",
        )


class HeliaRTAdapter(SingleArenaPlacementMixin):
    """Adapter for heliaRT — Ambiq's optimized TFLM fork.

    Resolves a heliaRT distribution via three modes (local path, GitHub
    source, or default pinned version), validates version compatibility,
    then installs a local NSX module at ``work_dir/modules/nsx-helia-rt/``.

    The module uses heliaRT's native ``nsx/`` CMake integration when the
    distribution includes it.  Otherwise, embedded static module files
    (based on the native module) are used.
    """

    prepared_runtime_contract = ("helia-rt", "helia")

    def prepare_runtime(self, config: ProfileConfig, work_dir: Path, runtime) -> HeliaRtArtifacts:
        from ...fixture_runtime import prepare_runtime_module

        stack, backend = self.prepared_runtime_contract
        if runtime.record.stack != stack or config.engine.backend != backend:
            raise EngineError(f"Prepared heliaRT requires {stack}/{backend}")
        _reject_config_path(config)
        _reject_prebuilt_cmsis_nn_override(config)
        for option in ("variant", "core_override", "cmsis_nn_requantize_inline_asm"):
            if option in config.engine.config:
                raise EngineError(
                    f"Prepared heliaRT cannot apply engine.config.{option}; "
                    "the runtime archive is already compiled.",
                    hint=(
                        f"Remove engine.config.{option} to use the supplied runtime, "
                        "or prepare a runtime with the desired build settings."
                    ),
                )
        revision = next(p.revision for p in runtime.record.providers if p.name == "helia-rt")
        return HeliaRtArtifacts(
            extra_modules=[prepare_runtime_module(runtime, work_dir)],
            engine_header=TFLM_ENGINE_HEADER,
            engine_backend=backend,
            heliart_version=f"prepared:{revision}",
            heliart_variant="prepared",
            heliart_toolchain_tag=runtime.record.abi.toolchain,
        )

    @property
    def name(self) -> str:
        return "heliaRT"

    def prepare_fixture_model(self, path: Path, *, single_io: bool) -> FixtureModelPreparation:
        from ...fixture_analysis import litert_fixture_resolver, prepare_litert_fixture

        return prepare_litert_fixture(path, single_io=single_io, resolver=litert_fixture_resolver)

    @property
    def engine_type(self) -> EngineType:
        return EngineType.HELIA_RT

    @property
    def psram_weights_source(self) -> PsramWeightsSource:
        # Same host-upload flow as stock TFLM: the flatbuffer is pushed
        # over J-Link after the firmware's HPX_PSRAM_READY.
        return PsramWeightsSource.HOST_UPLOAD

    @property
    def lifecycles(self) -> frozenset[Lifecycle]:
        return frozenset({Lifecycle.CARRY, Lifecycle.SINGLE})

    def lifecycle_refusal(self, lifecycle: Lifecycle) -> str:
        return f"{self.name} firmware does not implement the {lifecycle.value} lifecycle"

    @property
    def instance_boundary(self) -> InstanceBoundary:
        return InstanceBoundary.BOOT

    def first_invoke_initializer(self, model_initializer: bool | Unknown) -> bool | Unknown:
        # heliaRT's MicroInterpreter runs a CALL_ONCE subgraph inside the
        # first Invoke(), as TFLM does.
        return model_initializer

    def prepare(self, config: ProfileConfig, work_dir: Path) -> HeliaRtArtifacts:
        _reject_config_path(config)
        backend = config.engine.backend or "helia"
        variant = config.engine.config.get("variant", "release-with-logs")
        core_override = config.engine.config.get("core_override")

        # heliaRT treats unknown backend strings as a passthrough for its own
        # runtime selection — only "ethos_u" changes hpx behavior here.
        ethos_u = backend == "ethos_u"

        valid_variants = ("debug", "release-with-logs", "release")
        if variant not in valid_variants:
            raise EngineError(
                f"Invalid heliaRT variant '{variant}'",
                hint=f"Valid variants: {', '.join(valid_variants)}",
            )
        if variant == "release":
            # TF_LITE_STRIP_ERROR_STRINGS compiles out ScopedMicroProfiler.
            raise EngineError(
                "heliaRT variant 'release' strips per-op profiling",
                hint="Use variant 'release-with-logs' (default) or 'debug'.",
            )

        toolchain_tag = _toolchain_tag(config.target.toolchain)

        # Source-build mode: opt-in via engine.config.source_path or
        # HELIART_SOURCE_PATH env. Compiles heliaRT from a local source
        # tree instead of consuming a prebuilt static-lib release.
        source_path = _resolve_source_path(config)
        dist_path_cfg = config.engine.config.get("dist_path") or os.environ.get("HELIART_DIST_PATH")
        source_cfg = config.engine.config.get("source")

        # Whether the user requested a locally vendored heliaRT module
        # (source build, explicit prebuilt dist, or a custom GitHub
        # release). When none of these are set, hpx defaults to resolving
        # nsx-helia-rt from the NSX registry (NSX clones it from GitHub).
        use_local = source_path is not None or bool(dist_path_cfg) or bool(source_cfg)

        if use_local and source_path is None:
            _reject_prebuilt_cmsis_nn_override(config)

        extra_modules: list[NsxModuleRef] = []
        cmake_vars: dict[str, str] = {}

        if core_override is not None and (source_path is not None or not use_local):
            raise EngineError(
                "heliaRT core_override requires a prebuilt distribution",
                hint=(
                    "Use engine.config.dist_path or engine.config.source for a prebuilt "
                    "release, or remove core_override for a registry/source build."
                ),
            )

        if not use_local:
            version = HELIART_VERSION
            log.info(
                "heliaRT %s — resolving %s from NSX registry "
                "(project=%s @ %s, toolchain=%s, variant=%s)",
                version,
                HELIART_MODULE,
                HELIART_PROJECT,
                HELIART_RELEASE_TAG,
                toolchain_tag,
                variant,
            )
            extra_modules.append(
                NsxModuleRef.registry(
                    HELIART_MODULE, HELIART_PROJECT, ref=HELIART_RELEASE_TAG, version=version
                )
            )
            extra_modules.append(cmsis_nn_module_ref(config, work_dir))
            cmake_vars.update(cmsis_nn_cmake_vars(config))
            cmake_vars["HELIA_RT_VARIANT"] = variant
            if ethos_u:
                _add_ethos_u_artifacts(extra_modules, cmake_vars)
            return HeliaRtArtifacts(
                engine_type=EngineType.HELIA_RT,
                extra_modules=extra_modules,
                cmake_vars=cmake_vars,
                engine_header=TFLM_ENGINE_HEADER,
                engine_backend=backend,
                heliart_version=version,
                heliart_variant=variant,
                heliart_toolchain_tag=toolchain_tag,
            )

        # Vendor under the registry-derived project directory
        # (modules/helia-rt) so NSX's registry-aware lock resolves it.
        module_dir = work_dir / "modules" / HELIART_PROJECT
        module_dir.mkdir(parents=True, exist_ok=True)

        if source_path is not None:
            resolved_version = _detect_version(source_path)
            _check_version_compatibility(source_path, resolved_version)
            version = resolved_version or HELIART_VERSION
            if ethos_u:
                _require_ethos_u_source_support(source_path)

            # A source build needs the core module and its kernel options;
            # a prebuilt archive has both baked in.
            extra_modules.append(cmsis_nn_module_ref(config, work_dir))
            cmake_vars.update(cmsis_nn_cmake_vars(config))

            _install_nsx_module_source(
                module_dir,
                source_path,
                variant=variant,
            )

            log.info(
                "heliaRT %s (toolchain=%s, variant=%s, source=%s)",
                version,
                toolchain_tag,
                variant,
                source_path,
            )
        else:
            if ethos_u:
                raise EngineError(
                    "backend 'ethos_u' requires a heliaRT source build — "
                    "prebuilt heliaRT distributions do not ship the Ethos-U "
                    "custom-op kernel.",
                    hint=(
                        "Remove engine.config.dist_path/source (registry "
                        "default builds from source), or set "
                        "engine.config.source_path to a heliaRT checkout."
                    ),
                )
            dist_path, resolved_version = _resolve_distribution(config)
            _check_version_compatibility(dist_path, resolved_version)
            version = resolved_version or HELIART_VERSION

            _verify_prebuilt_archive(
                dist_path,
                board=config.target.board,
                registry=config.platform_registry,
                toolchain_tag=toolchain_tag,
                variant=variant,
                core_override=core_override,
            )
            _install_nsx_module(module_dir, dist_path, variant=variant, core_override=core_override)

            if core_override:
                log.warning(
                    "heliaRT: core_override=%s — using %s library on %s board",
                    core_override,
                    core_override,
                    config.target.board,
                )

            log.info(
                "heliaRT %s (toolchain=%s, variant=%s, dist=%s)",
                version,
                toolchain_tag,
                variant,
                dist_path,
            )

        extra_modules.append(
            NsxModuleRef(
                name=HELIART_MODULE,
                path=module_dir,
                version=version,
                local=True,
                project=HELIART_PROJECT,
            ),
        )
        if ethos_u:
            _add_ethos_u_artifacts(extra_modules, cmake_vars)

        return HeliaRtArtifacts(
            engine_type=EngineType.HELIA_RT,
            extra_modules=extra_modules,
            cmake_vars=cmake_vars,
            engine_header=TFLM_ENGINE_HEADER,
            engine_backend=backend,
            heliart_version=version,
            heliart_variant=variant,
            heliart_toolchain_tag=toolchain_tag,
            source_roots=((source_path, "helia-rt-source"),) if source_path is not None else (),
        )
