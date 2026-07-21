"""heliaRT distribution acquisition, version pinning, and compatibility checks.

Resolves a heliaRT distribution (prebuilt ``.a`` + TFLM headers) from one of
three modes (first match wins):

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

import logging
import os
import re
from pathlib import Path

from ...config import ProfileConfig
from ...errors import EngineError
from ...platform import CoreArch, PlatformRegistry, get_board, get_soc

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

# Directories required in a valid heliaRT distribution.
_DIST_DIRS = ("lib", "tensorflow", "third_party", "signal")

# GitHub release asset naming: helia-rt-{TAG}.zip
_ASSET_FMT = "helia-rt-{tag}.zip"

# Files that must exist in the source tree to qualify as a heliaRT source build.
_SOURCE_REQUIRED_FILES = (
    "CMakeLists.txt",
    "nsx/CMakeLists.txt",
    "nsx/nsx-module.yaml",
    "cmake/helia_rt_sources.cmake",
    "tensorflow/lite/micro/helia_rt_version.h",
)


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


def _toolchain_tag(toolchain: str) -> str:
    """Map a profiler ``target.toolchain`` to a heliaRT archive tag.

    heliaRT release artifacts are named ``...-<gcc|armclang>-<variant>.a``.
    """
    from ...toolchains import get_toolchain_spec

    return get_toolchain_spec(toolchain).heliart_tag


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


# ---------------------------------------------------------------------------
# heliaRT source-build mode
# ---------------------------------------------------------------------------
#
# Opt-in by setting ``engine.config.source_path`` or ``HELIART_SOURCE_PATH``
# to a heliaRT source-repo root.  The repo must ship the source-build NSX
# module (heliaRT >= v1.16.0).


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


# ---------------------------------------------------------------------------
# heliaRT distribution resolution (multi-mode)
# ---------------------------------------------------------------------------


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
    # Local import: download.py imports resolution/validation helpers from
    # this module, so importing it back at module scope would be circular.
    from .download import _fetch_github_release

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
