"""heliaRT engine adapter.

Resolves a heliaRT distribution (prebuilt ``.a`` + TFLM headers) and
installs it as a local NSX module for the profiler firmware build.

Distribution resolution (first match wins):

1. **Local path** — ``engine.config.dist_path`` or ``HELIART_DIST_PATH``
   env var.  Points to an already-extracted release directory.
2. **GitHub source** — ``engine.config.source.repo`` +
   ``engine.config.source.ref``.  Downloads the tagged release asset from
   GitHub, caches it under ``~/.cache/helia-profiler/heliart/``.
3. **Default** — downloads from ``AmbiqAI/helia-rt`` at the adapter's
   pinned version tag.

Version compatibility is checked by parsing ``helia_rt_version.h`` from the
resolved distribution and comparing against the adapter's expected version.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..config import ProfileConfig
from ..errors import EngineError
from ..placement import Placement
from ..platform import CoreArch, PlatformRegistry, get_board, get_soc
from ..results import NsxModuleRef
from . import EngineType, TFLM_ENGINE_HEADER
from .base import ArenaRegion, EngineArtifacts

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# heliaRT version policy.
#
# - HELIART_VERSION     : pinned default. Used when the user provides no
#                         override. Bump when a new release is adopted.
# - HELIART_MIN_VERSION : minimum-supported version. Any resolved
#                         distribution (default download, custom GitHub
#                         ref, or local dist_path) must be >= this.
#                         Bump only on incompatible API changes.
# ---------------------------------------------------------------------------
HELIART_VERSION = "1.16.0"
HELIART_MIN_VERSION = "1.16.0"
HELIART_GH_REPO = "AmbiqAI/helia-rt"
# NB: v1.16.0+ uses "helia-rt-v..." tag format (previously "heliaRT-v...").
HELIART_RELEASE_TAG = f"helia-rt-v{HELIART_VERSION}"

# NSX registry identity for heliaRT. By default hpx declares this module and
# lets NSX clone it from the registered GitHub upstream; a user-provided
# local path (source_path / dist_path / source) vendors it instead.
HELIART_PROJECT = "helia-rt"  # registry project (path: modules/helia-rt)
HELIART_MODULE = "nsx-helia-rt"  # registry module name

# Cache directory for downloaded distributions
_CACHE_DIR = Path.home() / ".cache" / "helia-profiler" / "heliart"


def _core_tag(
    board: str,
    *,
    registry: PlatformRegistry | None = None,
    override: str | None = None,
) -> str:
    """Map a board name to the heliaRT library core tag (cm4 or cm55)."""
    if override:
        tag = override.lower()
        if tag not in ("cm4", "cm55"):
            raise EngineError(
                f"Invalid core_override '{override}'",
                hint="Valid values: cm4, cm55",
            )
        return tag
    soc = get_soc(_board_to_soc(board, registry=registry), registry=registry)
    if soc.core is CoreArch.CORTEX_M55:
        return "cm55"
    return "cm4"


def _board_to_soc(board: str, *, registry: PlatformRegistry | None = None) -> str:
    """Resolve board name to SoC name via the platform registry."""
    return get_board(board, registry=registry).soc


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

    def supports_runtime_split(self) -> bool:
        return True

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
            from .helia_aot import cmsis_nn_module_ref

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


def _toolchain_tag(toolchain: str) -> str:
    """Map a profiler ``target.toolchain`` to a heliaRT archive tag.

    heliaRT release artifacts are named ``...-<gcc|armclang>-<variant>.a``.
    """
    tc = (toolchain or "").lower()
    if tc in ("armclang",):
        return "armclang"
    if tc in ("atfe",):
        return "atfe"
    if tc in ("arm-none-eabi-gcc", "gcc"):
        return "gcc"
    log.warning(
        "heliaRT: no prebuilt archive for toolchain '%s'; falling back to gcc variant",
        toolchain,
    )
    return "gcc"


def _verify_prebuilt_archive(
    dist_path: Path,
    *,
    board: str,
    registry: PlatformRegistry | None = None,
    toolchain_tag: str,
    variant: str,
    core_override: str | None = None,
) -> None:
    """Fail fast if the required ``.a`` is missing from the distribution."""
    core = _core_tag(board, registry=registry, override=core_override)
    name = f"libhelia-rt-{core}-{toolchain_tag}-{variant}.a"
    if not (dist_path / "lib" / name).is_file():
        available = sorted(p.name for p in (dist_path / "lib").glob("*.a"))
        raise EngineError(
            f"heliaRT: required prebuilt archive not found: {name}",
            hint=(
                f"Looked in {dist_path / 'lib'}.  Available archives: "
                f"{', '.join(available) if available else '(none)'}"
            ),
        )


def _install_nsx_module(
    module_dir: Path,
    dist_path: Path,
    *,
    variant: str,
    core_override: str | None = None,
) -> None:
    """Install the NSX module files and distribution content into *module_dir*.

    Requires the distribution to ship ``nsx/nsx-module.yaml``
    (heliaRT >= 1.16.0).  The upstream ``nsx/CMakeLists.txt`` is a
    source-build module and cannot be used with the prebuilt dist alone
    (it requires the full repo with ``cmake/helia_rt_sources.cmake``).
    HPX generates a minimal prebuilt-wrapper CMakeLists.txt instead.
    """
    nsx_dir = dist_path / "nsx"
    src_yaml = nsx_dir / "nsx-module.yaml"
    if not src_yaml.is_file():
        raise EngineError(
            f"heliaRT distribution at {dist_path} is missing nsx/nsx-module.yaml",
            hint=(f"Expected nsx/nsx-module.yaml. Use heliaRT >= v{HELIART_MIN_VERSION}."),
        )

    shutil.copy2(src_yaml, module_dir / "nsx-module.yaml")

    # --- Generate a prebuilt-wrapper CMakeLists.txt ---
    # v1.16.0's nsx/CMakeLists.txt is a source-build module that requires
    # the full heliaRT repo.  For the prebuilt dist, we generate a simpler
    # wrapper that links the static library directly.
    core_override_block = ""
    if core_override:
        tag = core_override.lower()
        core_override_block = (
            f'\n# core_override: force {tag} library\nset(_HELIA_RT_CORE "{tag}")\n'
        )

    cmake_text = _PREBUILT_CMAKE_TEMPLATE.format(
        variant=variant,
        core_override_block=core_override_block,
    )
    (module_dir / "CMakeLists.txt").write_text(cmake_text)

    # --- Copy distribution content (lib/, tensorflow/, third_party/, …) ---
    for d in _DIST_DIRS:
        target = module_dir / d
        source = dist_path / d
        if target.is_dir():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target)
            log.debug("Copied %s → %s", source, target)


# Prebuilt wrapper template for heliaRT >= v1.16.0 distributions.
# The dist's own nsx/CMakeLists.txt is source-build-only; this provides a
# minimal prebuilt-style module that links the static .a library.
_PREBUILT_CMAKE_TEMPLATE = """\
# Auto-generated by hpx HeliaRTAdapter (prebuilt mode).
# Do not edit — regenerated on every hpx run.
cmake_minimum_required(VERSION 3.21)

if(NOT DEFINED NSX_BOARD_FLAGS_TARGET)
    message(FATAL_ERROR
        "nsx-helia-rt: NSX_BOARD_FLAGS_TARGET must be defined.")
endif()

if(NOT DEFINED NSX_SOC_FAMILY)
    message(FATAL_ERROR
        "nsx-helia-rt: NSX_SOC_FAMILY must be defined.")
endif()

# --- Resolve Cortex-M core tag from SoC family ---
set(_HELIA_RT_CORE "")
if(NSX_SOC_FAMILY MATCHES "^apollo5|^apollo330|^apollo510|^atomiq")
    set(_HELIA_RT_CORE "cm55")
elseif(NSX_SOC_FAMILY MATCHES "^apollo4|^apollo3")
    set(_HELIA_RT_CORE "cm4")
else()
    message(FATAL_ERROR
        "nsx-helia-rt: unsupported NSX_SOC_FAMILY '${{NSX_SOC_FAMILY}}'")
endif()
{core_override_block}
# --- Resolve toolchain tag ---
set(_HELIA_RT_TOOLCHAIN "gcc")
if(CMAKE_C_COMPILER_ID STREQUAL "ARMClang" OR
   CMAKE_C_COMPILER MATCHES "armclang")
    set(_HELIA_RT_TOOLCHAIN "armclang")
elseif(CMAKE_C_COMPILER MATCHES "atfe")
    set(_HELIA_RT_TOOLCHAIN "atfe")
endif()

# --- Build variant ---
set(HELIA_RT_VARIANT "{variant}" CACHE STRING
    "heliaRT build variant" FORCE)

# --- Locate the prebuilt static library ---
set(_HELIA_RT_LIB_NAME
    "libhelia-rt-${{_HELIA_RT_CORE}}-${{_HELIA_RT_TOOLCHAIN}}-${{HELIA_RT_VARIANT}}.a")
set(_HELIA_RT_LIB_PATH
    "${{CMAKE_CURRENT_LIST_DIR}}/lib/${{_HELIA_RT_LIB_NAME}}")

if(NOT EXISTS "${{_HELIA_RT_LIB_PATH}}")
    message(FATAL_ERROR
        "nsx-helia-rt: prebuilt library not found:\\n"
        "  ${{_HELIA_RT_LIB_PATH}}\\n"
        "Check HELIA_RT_VARIANT (${{HELIA_RT_VARIANT}}) and "
        "toolchain (${{_HELIA_RT_TOOLCHAIN}}).")
endif()

message(STATUS "nsx-helia-rt: using ${{_HELIA_RT_LIB_NAME}}")

# --- Import the prebuilt static library ---
add_library(nsx_helia_rt_prebuilt STATIC IMPORTED GLOBAL)
set_target_properties(nsx_helia_rt_prebuilt PROPERTIES
    IMPORTED_LOCATION "${{_HELIA_RT_LIB_PATH}}"
)

# --- Platform glue (MicroPrintf + debug_log) ---
add_library(nsx_helia_rt STATIC
    ${{CMAKE_CURRENT_LIST_DIR}}/tensorflow/lite/micro/micro_log.cc
    ${{CMAKE_CURRENT_LIST_DIR}}/tensorflow/lite/micro/cortex_m_generic/debug_log.cc
)
set_target_properties(nsx_helia_rt PROPERTIES EXPORT_NAME helia_rt)
add_library(nsx::helia_rt ALIAS nsx_helia_rt)

target_link_libraries(nsx_helia_rt
    PUBLIC
        ${{NSX_BOARD_FLAGS_TARGET}}
        nsx_helia_rt_prebuilt
    PRIVATE
        nsx_soc_hal
        nsx_core
)

target_include_directories(nsx_helia_rt
    PUBLIC
        $<BUILD_INTERFACE:${{CMAKE_CURRENT_LIST_DIR}}>
        $<BUILD_INTERFACE:${{CMAKE_CURRENT_LIST_DIR}}/third_party>
        $<BUILD_INTERFACE:${{CMAKE_CURRENT_LIST_DIR}}/third_party/flatbuffers/include>
        $<BUILD_INTERFACE:${{CMAKE_CURRENT_LIST_DIR}}/third_party/gemmlowp>
)

target_compile_definitions(nsx_helia_rt
    PUBLIC
        TF_LITE_STATIC_MEMORY
        NS_TFSTRUCTURE_RECENT
        NS_TFLM_NEW_MICRO_PROFILER
)
"""


# ---------------------------------------------------------------------------
# heliaRT source-build mode
# ---------------------------------------------------------------------------
#
# Opt-in by setting ``engine.config.source_path`` or ``HELIART_SOURCE_PATH``
# to a heliaRT source-repo root.  The repo must ship the source-build NSX
# module (heliaRT >= v1.16.0).
#
# The adapter writes a thin wrapper ``CMakeLists.txt`` at
# ``<work_dir>/modules/nsx-helia-rt/`` that sets the variant and includes
# the source tree's own ``nsx/CMakeLists.txt``.  heliaRT self-resolves its
# repo root from ``CMAKE_CURRENT_LIST_DIR/..`` inside the included file.
# No source files are copied — the source tree is referenced by absolute
# path.

# Files that must exist in the source tree to qualify as a heliaRT source build.
_SOURCE_REQUIRED_FILES = (
    "nsx/CMakeLists.txt",
    "nsx/nsx-module.yaml",
    "cmake/helia_rt_sources.cmake",
    "tensorflow/lite/micro/helia_rt_version.h",
)


def _resolve_source_path(config: ProfileConfig) -> Path | None:
    """Resolve a heliaRT source-tree path, if source-build is requested.

    Source-build is opt-in via:
    1. ``engine.config.source_path`` (config / CLI), or
    2. ``HELIART_SOURCE_PATH`` environment variable.

    Returns the absolute, validated source-tree path, or ``None`` if the
    user did not opt in.  Raises ``EngineError`` if the user opted in but
    the path is invalid.
    """
    raw = config.engine.config.get("source_path")
    if not raw:
        raw = os.environ.get("HELIART_SOURCE_PATH")
    if not raw:
        return None

    p = Path(str(raw)).expanduser().resolve()
    if not p.is_dir():
        raise EngineError(
            f"heliaRT source_path '{p}' is not a directory",
            hint="Point engine.config.source_path at a heliaRT source-repo root.",
        )
    missing = [rel for rel in _SOURCE_REQUIRED_FILES if not (p / rel).is_file()]
    if missing:
        raise EngineError(
            f"heliaRT source tree at {p} is missing required files: {', '.join(missing)}",
            hint=(
                "Source-build requires a heliaRT repo with the source-build "
                "NSX module (>= v1.16.0). The released "
                "release zip ships the prebuilt-style nsx/CMakeLists.txt and "
                "is not compatible with source_path."
            ),
        )
    return p


def _install_nsx_module_source(
    module_dir: Path,
    source_path: Path,
    *,
    variant: str,
) -> None:
    """Install a source-build NSX module wrapper at *module_dir*.

    Writes a minimal ``CMakeLists.txt`` that includes the heliaRT source
    tree's own ``nsx/CMakeLists.txt``.  heliaRT self-resolves its repo
    root from ``CMAKE_CURRENT_LIST_DIR/..`` inside the included file, so
    no explicit ``HELIART_TFLM_ROOT`` override is needed.

    No source files are copied — the source tree is referenced by absolute
    path so an incremental build can reuse its build artifacts across hpx
    runs.
    """
    # Wipe any prior install (e.g. switching modes inside one work_dir).
    for d in (*_DIST_DIRS, "lib", "nsx"):
        tgt = module_dir / d
        if tgt.is_dir():
            shutil.rmtree(tgt)

    nsx_cmake = source_path / "nsx" / "CMakeLists.txt"

    cmake_text = (
        "# Auto-generated by hpx HeliaRTAdapter (source-build mode).\n"
        "# Do not edit — regenerated on every hpx run.\n"
        "cmake_minimum_required(VERSION 3.21)\n"
        "\n"
        f'set(HELIA_RT_VARIANT "{variant}" '
        'CACHE STRING "heliaRT build variant" FORCE)\n'
        "\n"
        "# Delegate to the source-build NSX module that ships with the\n"
        "# heliaRT repo.  include() sets CMAKE_CURRENT_LIST_DIR to the\n"
        "# source nsx/ directory, so heliaRT self-resolves its repo root.\n"
        f'include("{nsx_cmake.as_posix()}")\n'
    )
    (module_dir / "CMakeLists.txt").write_text(cmake_text)

    shutil.copy2(
        source_path / "nsx" / "nsx-module.yaml",
        module_dir / "nsx-module.yaml",
    )


# ---------------------------------------------------------------------------
# heliaRT distribution resolution (multi-mode)
# ---------------------------------------------------------------------------

# Directories required in a valid heliaRT distribution.
_DIST_DIRS = ("lib", "tensorflow", "third_party", "signal")

# GitHub release asset naming: helia-rt-{TAG}.zip
_ASSET_FMT = "helia-rt-{tag}.zip"


def _resolve_distribution(config: ProfileConfig) -> tuple[Path, str | None]:
    """Resolve the heliaRT distribution directory.

    Returns ``(dist_path, detected_version)``.  *detected_version* may be
    ``None`` if the distribution doesn't contain a parseable version header.

    Resolution order:
    1. ``engine.config.dist_path`` — local filesystem path.
    2. ``HELIART_DIST_PATH`` environment variable — local filesystem path.
    3. ``engine.config.source`` — download from GitHub release.
    4. Default — download from ``AmbiqAI/helia-rt`` at the pinned version.
    """

    # --- 1. Explicit local path ---
    raw = config.engine.config.get("dist_path")
    if raw:
        p = Path(raw).expanduser().resolve()
        _validate_dist(p)
        return p, _detect_version(p)

    # --- 2. Environment variable ---
    env = os.environ.get("HELIART_DIST_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        _validate_dist(p)
        return p, _detect_version(p)

    # --- 3. Source config (repo + ref) ---
    source = config.engine.config.get("source")
    api_s = config.timeouts.download_api_s
    asset_s = config.timeouts.download_asset_s
    if source and isinstance(source, dict):
        repo = source.get("repo", HELIART_GH_REPO)
        ref = source.get("ref", HELIART_RELEASE_TAG)
        return _fetch_github_release(repo, ref, api_s=api_s, asset_s=asset_s)

    # --- 4. Default: pinned version from default repo ---
    log.info(
        "No dist_path or source configured — fetching heliaRT %s from %s",
        HELIART_RELEASE_TAG,
        HELIART_GH_REPO,
    )
    return _fetch_github_release(
        HELIART_GH_REPO,
        HELIART_RELEASE_TAG,
        api_s=api_s,
        asset_s=asset_s,
    )


# ---------------------------------------------------------------------------
# GitHub release download
# ---------------------------------------------------------------------------


def _fetch_github_release(
    repo: str,
    ref: str,
    *,
    api_s: float = 30,
    asset_s: float = 300,
) -> tuple[Path, str | None]:
    """Download a heliaRT release from GitHub.

    Checks the local cache first.  On a cache miss, queries the GitHub
    Releases API, downloads the NSX bundle (preferred) or the legacy
    neuralSPOT bundle, and extracts it into the cache directory.

    Returns ``(dist_path, detected_version)``.
    """
    cache_key = f"{repo.replace('/', '_')}_{ref}"
    cache_dir = _CACHE_DIR / cache_key

    # Cache hit — validate and return
    if cache_dir.is_dir() and _is_valid_dist(cache_dir):
        log.info("Cache hit: %s", cache_dir)
        return cache_dir, _detect_version(cache_dir)

    # Resolve the release tag.
    # If ref looks like a tag (HeliaRT-v*, v*), use it directly.
    # Otherwise treat it as a branch and find the latest release.
    tag = _resolve_release_tag(repo, ref, api_s=api_s)
    if tag is None:
        raise EngineError(
            f"No GitHub release found for {repo}@{ref}",
            hint=(
                "Provide a valid release tag (e.g. helia-rt-v1.16.0), "
                "or set engine.config.dist_path to a local directory."
            ),
        )

    # Try downloading: NSX bundle first, legacy bundle as fallback
    asset_url = _find_release_asset(repo, tag, api_s=api_s)
    if asset_url is None:
        raise EngineError(
            f"No downloadable asset found for {repo} release {tag}",
            hint="Set engine.config.dist_path to a local heliaRT distribution.",
        )

    log.info("Downloading heliaRT from %s ...", asset_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _download_and_extract(asset_url, cache_dir, timeout_s=asset_s)

    _validate_dist(cache_dir)
    return cache_dir, _detect_version(cache_dir)


def _resolve_release_tag(repo: str, ref: str, *, api_s: float = 30) -> str | None:
    """Resolve *ref* to a GitHub release tag.

    If *ref* already looks like a release tag, verify it exists.
    Otherwise query the releases API for the latest release.
    """
    # Direct tag reference — verify it exists
    api = f"https://api.github.com/repos/{repo}/releases/tags/{ref}"
    data = _github_api_get(api, timeout_s=api_s)
    if data is not None:
        return ref

    # Maybe ref is just a version like "1.16.0" — try common tag formats
    for fmt in (f"helia-rt-v{ref}", f"heliaRT-v{ref}", f"v{ref}"):
        api = f"https://api.github.com/repos/{repo}/releases/tags/{fmt}"
        data = _github_api_get(api, timeout_s=api_s)
        if data is not None:
            return fmt

    # Branch or other ref — try latest release from the repo
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    data = _github_api_get(api, timeout_s=api_s)
    if data is not None:
        tag = data.get("tag_name")
        log.warning(
            "ref '%s' is not a release tag — falling back to latest release: %s",
            ref,
            tag,
        )
        return tag

    return None


def _find_release_asset(repo: str, tag: str, *, api_s: float = 30) -> str | None:
    """Find the download URL for the heliaRT release zip.

    Matches ``helia-rt-{tag}.zip`` exactly first; otherwise accepts any
    asset whose name matches ``helia-rt-*.zip`` (and warns if more than
    one such asset exists — picks the first deterministically).
    """
    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    data = _github_api_get(api, timeout_s=api_s)
    if data is None:
        return None

    assets = data.get("assets", [])
    asset_names = {a["name"]: a["browser_download_url"] for a in assets}

    # Exact match first.
    name = _ASSET_FMT.format(tag=tag)
    if name in asset_names:
        return asset_names[name]

    # Tighter glob fallback: helia-rt-*.zip only.
    candidates = sorted(n for n in asset_names if n.startswith("helia-rt-") and n.endswith(".zip"))
    if not candidates:
        return None
    if len(candidates) > 1:
        log.warning(
            "Multiple heliaRT release assets matched 'helia-rt-*.zip' for %s @ %s; "
            "picking %s. Candidates: %s",
            repo,
            tag,
            candidates[0],
            candidates,
        )
    log.info("Using release asset: %s", candidates[0])
    return asset_names[candidates[0]]


def _github_api_get(url: str, *, timeout_s: float = 30) -> dict | None:
    """Make a GET request to the GitHub API.  Returns None on 404."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return None
        log.warning("GitHub API error %s for %s", exc.code, url)
        return None
    except (URLError, OSError) as exc:
        log.warning("GitHub API request failed: %s", exc)
        return None


def _download_and_extract(url: str, dest: Path, *, timeout_s: float = 300) -> None:
    """Download a zip from *url* and extract into *dest*.

    If the zip contains a single top-level directory, its contents are
    extracted directly into *dest* (strip one level).
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            data = resp.read()
    except (URLError, OSError) as exc:
        raise EngineError(
            f"Failed to download heliaRT release: {exc}",
            hint="Check your network connection or set engine.config.dist_path.",
        ) from exc

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        # Detect single top-level directory (common in GitHub release zips)
        top_dirs = {n.split("/")[0] for n in zf.namelist() if "/" in n}
        strip_prefix = ""
        if len(top_dirs) == 1:
            strip_prefix = top_dirs.pop() + "/"

        for member in zf.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if strip_prefix and name.startswith(strip_prefix):
                name = name[len(strip_prefix) :]
            if not name:
                continue
            out = dest / name
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(member))

    log.info("Extracted heliaRT distribution to %s", dest)


# ---------------------------------------------------------------------------
# Distribution validation
# ---------------------------------------------------------------------------


def _validate_dist(dist: Path) -> None:
    """Verify that *dist* looks like a heliaRT release directory."""
    if not dist.is_dir():
        raise EngineError(
            f"heliaRT dist path does not exist: {dist}",
            hint="Provide a valid directory containing the heliaRT release.",
        )
    for d in _DIST_DIRS:
        if not (dist / d).is_dir():
            raise EngineError(
                f"heliaRT dist missing '{d}/' directory: {dist}",
                hint=f"Expected: {', '.join(d + '/' for d in _DIST_DIRS)}",
            )


def _is_valid_dist(dist: Path) -> bool:
    """Return True if *dist* has the required directories."""
    return all((dist / d).is_dir() for d in _DIST_DIRS)


# ---------------------------------------------------------------------------
# Version detection and compatibility
# ---------------------------------------------------------------------------


def _detect_version(dist: Path) -> str | None:
    """Parse the heliaRT version from the distribution.

    Checks (in order):
    1. ``helia_rt_version.h`` — ``#define HELIA_RT_VERSION "v1.16.0"``
       (falls back to legacy ``heliart_version.h`` / ``HELIART_VERSION``)
    2. ``MANIFEST.txt`` — ``neuralspot-helios-rt HeliaRT-v1.7.0``
    """
    # 1. Version header (v1.16.0+ naming)
    version_h = dist / "tensorflow" / "lite" / "micro" / "helia_rt_version.h"
    if version_h.is_file():
        text = version_h.read_text(errors="replace")
        m = re.search(r'#define\s+HELIA_RT_VERSION\s+"v?([^"]+)"', text)
        if m:
            return m.group(1)

    # 1b. Legacy header (pre-v1.16.0)
    legacy_h = dist / "tensorflow" / "lite" / "micro" / "heliart_version.h"
    if legacy_h.is_file():
        text = legacy_h.read_text(errors="replace")
        m = re.search(r'#define\s+HELIART_VERSION\s+"v?([^"]+)"', text)
        if m:
            return m.group(1)

    # 2. MANIFEST.txt
    manifest = dist / "MANIFEST.txt"
    if manifest.is_file():
        first_line = manifest.read_text(errors="replace").split("\n")[0]
        # v1.16.0+: "helia-rt helia-rt-v1.16.0"
        m = re.search(r"helia-rt-v(\S+)", first_line)
        if m:
            return m.group(1)
        # Legacy: "neuralspot-helios-rt HeliaRT-v1.7.0"
        m = re.search(r"HeliaRT-v(\S+)", first_line)
        if m:
            return m.group(1)

    return None


def _parse_semver(version: str) -> tuple[int, int, int]:
    """Parse a semver-ish string into (major, minor, patch)."""
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return (0, 0, 0)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _check_version_compatibility(
    dist: Path,
    detected_version: str | None,
) -> None:
    """Enforce minimum-supported heliaRT version on a resolved distribution.

    Policy:
    * If the version cannot be parsed from the dist, warn (don't fail) —
      a sanity check on directory layout already ran in ``_validate_dist``.
    * If the version is below ``HELIART_MIN_VERSION``, raise.
    * If the version is above ``HELIART_VERSION`` (the pinned default),
      log an informational message — a newer-than-pinned release is fine
      so long as it's >= the floor.
    """
    if detected_version is None:
        log.warning(
            "Could not detect heliaRT version from distribution at %s — "
            "skipping version-floor check (min supported: v%s)",
            dist,
            HELIART_MIN_VERSION,
        )
        return

    actual = _parse_semver(detected_version)
    minimum = _parse_semver(HELIART_MIN_VERSION)
    pinned = _parse_semver(HELIART_VERSION)

    if actual < minimum:
        raise EngineError(
            f"heliaRT v{detected_version} is below the minimum supported "
            f"version (v{HELIART_MIN_VERSION})",
            hint=(
                f"Use heliaRT >= v{HELIART_MIN_VERSION} (default pinned: "
                f"v{HELIART_VERSION}). Update engine.config.source.ref "
                "or engine.config.dist_path to a newer release."
            ),
        )

    if actual > pinned:
        log.info(
            "heliaRT v%s is newer than the pinned default v%s — proceeding (>= min v%s).",
            detected_version,
            HELIART_VERSION,
            HELIART_MIN_VERSION,
        )
    elif actual != pinned:
        log.debug(
            "heliaRT v%s differs from pinned v%s (>= min v%s).",
            detected_version,
            HELIART_VERSION,
            HELIART_MIN_VERSION,
        )
