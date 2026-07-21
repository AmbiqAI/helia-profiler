"""heliaRT engine adapter.

Resolves a heliaRT distribution and installs it as a local NSX module for
the profiler firmware build. See :mod:`.artifacts` for distribution
resolution/version-pinning and :mod:`.nsx_module` for the generated NSX
wrapper.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from ...config import ProfileConfig
from ...errors import EngineError
from ...placement import Placement
from ...results import NsxModuleRef
from .. import EngineType, TFLM_ENGINE_HEADER
from ..base import ArenaRegion, EngineArtifacts
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


class HeliaRTAdapter:
    """Adapter for heliaRT — Ambiq's optimized TFLM fork.

    Resolves a heliaRT distribution via three modes (local path, GitHub
    source, or default pinned version), validates version compatibility,
    then installs a local NSX module at ``work_dir/modules/nsx-helia-rt/``.

    The module uses heliaRT's native ``nsx/`` CMake integration when the
    distribution includes it.  Otherwise, embedded static module files
    (based on the native module) are used.
    """

    @property
    def name(self) -> str:
        return "heliaRT"

    @property
    def engine_type(self) -> EngineType:
        return EngineType.HELIA_RT

    def default_auto_placement(
        self, *, tcm_cap: int, sram_cap: int
    ) -> tuple[Placement, Placement] | None:
        # Fall through to the shared greedy fastest-fit policy.
        del tcm_cap, sram_cap
        return None

    def apply_arena_placement_override(
        self, regions: list[ArenaRegion], target: Placement
    ) -> list[ArenaRegion]:
        # heliaRT owns a single TFLM-style arena; no engine-side override.
        del target
        return regions

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        backend = config.engine.backend or "helia"
        variant = config.engine.config.get("variant", "release-with-logs")
        core_override = config.engine.config.get("core_override")

        # Validate variant
        valid_variants = ("debug", "release-with-logs", "release")
        if variant not in valid_variants:
            raise EngineError(
                f"Invalid heliaRT variant '{variant}'",
                hint=f"Valid variants: {', '.join(valid_variants)}",
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

        extra_modules: list[NsxModuleRef] = []
        cmake_vars: dict[str, str] = {}

        if not use_local:
            # --- Default: resolve nsx-helia-rt from the NSX registry ---
            # This clones AmbiqAI/helia-rt from GitHub at the pinned tag and
            # builds it from source via NSX (the registry module's own
            # manifest resolves its own nsx-cmsis-nn dependency, so we don't
            # need to add it to extra_modules here as the source_path branch
            # below does for a locally-vendored checkout). Because this is a
            # source build, the CMSIS-NN inline-asm requantize flag still
            # needs to be forwarded — it is not baked in the way it would be
            # for a genuinely prebuilt archive (see the `else` branch below).
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
                NsxModuleRef(
                    name=HELIART_MODULE,
                    path=Path(),
                    version=version,
                    local=False,
                    project=HELIART_PROJECT,
                    ref=HELIART_RELEASE_TAG,
                )
            )
            if config.engine.config.get("cmsis_nn_requantize_inline_asm", True):
                cmake_vars["NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM"] = "ON"
            return EngineArtifacts(
                engine_type=EngineType.HELIA_RT,
                extra_modules=extra_modules,
                cmake_vars=cmake_vars,
                engine_header=TFLM_ENGINE_HEADER,
                engine_backend=backend,
                heliart_version=version,
                heliart_variant=variant,
                heliart_toolchain_tag=toolchain_tag,
            )

        # --- Local / custom heliaRT module ---
        # Vendor under the registry-derived project directory
        # (modules/helia-rt) so NSX's registry-aware lock resolves it.
        module_dir = work_dir / "modules" / HELIART_PROJECT
        module_dir.mkdir(parents=True, exist_ok=True)

        if source_path is not None:
            # --- Source build ---
            resolved_version = _detect_version(source_path)
            _check_version_compatibility(source_path, resolved_version)
            version = resolved_version or HELIART_VERSION

            if core_override:
                log.warning(
                    "heliaRT source build ignores core_override=%s "
                    "(SoC family drives kernel selection)",
                    core_override,
                )

            # Source-built heliaRT depends on the nsx-cmsis-nn module
            # being present in the build (the prebuilt static lib had
            # CMSIS-NN baked in; the source build does not). Resolve it
            # via the shared helper (NSX registry by default).
            from ..helia_aot import cmsis_nn_module_ref

            extra_modules.append(cmsis_nn_module_ref(config, work_dir))

            # Forward CMSIS-NN inline-asm requantize flag (defaults ON to
            # match the prebuilt heliaRT build).
            if config.engine.config.get("cmsis_nn_requantize_inline_asm", True):
                cmake_vars["NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM"] = "ON"

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
            # --- Prebuilt distribution (explicit dist_path or custom
            #     GitHub release) ---
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

        return EngineArtifacts(
            engine_type=EngineType.HELIA_RT,
            extra_modules=extra_modules,
            cmake_vars=cmake_vars,
            engine_header=TFLM_ENGINE_HEADER,
            engine_backend=backend,
            heliart_version=version,
            heliart_variant=variant,
            heliart_toolchain_tag=toolchain_tag,
        )
