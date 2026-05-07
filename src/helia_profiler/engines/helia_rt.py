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

Version compatibility is checked by parsing ``heliart_version.h`` from the
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
from ..platform import CoreArch, get_soc
from ..results import NsxModuleRef
from . import EngineType
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
HELIART_VERSION = "1.12.2"
HELIART_MIN_VERSION = "1.12.2"
HELIART_GH_REPO = "AmbiqAI/helia-rt"
# NB: the GitHub tag uses lowercase "heliaRT-v..." (case-sensitive on the API).
HELIART_RELEASE_TAG = f"heliaRT-v{HELIART_VERSION}"

# Cache directory for downloaded distributions
_CACHE_DIR = Path.home() / ".cache" / "helia-profiler" / "heliart"


def _core_tag(board: str, *, override: str | None = None) -> str:
    """Map a board name to the heliaRT library core tag (cm4 or cm55)."""
    if override:
        tag = override.lower()
        if tag not in ("cm4", "cm55"):
            raise EngineError(
                f"Invalid core_override '{override}'",
                hint="Valid values: cm4, cm55",
            )
        return tag
    soc = get_soc(_board_to_soc(board))
    if soc.core is CoreArch.CORTEX_M55:
        return "cm55"
    return "cm4"


def _board_to_soc(board: str) -> str:
    """Resolve board name to SoC name via the platform registry."""
    from ..platform import get_board

    return get_board(board).soc


class HeliaRTAdapter:
    """Adapter for heliaRT — Ambiq's optimized TFLM fork.

    Resolves a heliaRT distribution via three modes (local path, GitHub
    source, or default pinned version), validates version compatibility,
    then installs a local NSX module at ``work_dir/modules/nsx-heliart/``.

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

        # Resolve the heliaRT distribution
        dist_path, resolved_version = _resolve_distribution(config)

        # Version compatibility check
        _check_version_compatibility(dist_path, resolved_version)

        # Set up the NSX module directory
        module_dir = work_dir / "modules" / "nsx-heliart"
        module_dir.mkdir(parents=True, exist_ok=True)

        version = resolved_version or HELIART_VERSION

        # Verify the archive exists in the distribution.
        _verify_prebuilt_archive(
            dist_path,
            board=config.target.board,
            toolchain_tag=toolchain_tag,
            variant=variant,
            core_override=core_override,
        )

        # Install NSX module files + distribution content
        _install_nsx_module(module_dir, dist_path, variant=variant,
                           core_override=core_override)

        if core_override:
            log.warning(
                "heliaRT: core_override=%s — using %s library on %s board",
                core_override, core_override, config.target.board,
            )

        log.info(
            "heliaRT %s (toolchain=%s, variant=%s, dist=%s)",
            version,
            toolchain_tag,
            variant,
            dist_path,
        )

        return EngineArtifacts(
            engine_type=EngineType.HELIA_RT,
            extra_modules=[
                NsxModuleRef(
                    name="nsx-heliart",
                    path=module_dir,
                    version=version,
                ),
            ],
            template_vars={
                "engine_backend": backend,
                "engine_header": "tensorflow/lite/micro/micro_interpreter.h",
                "heliart_version": version,
                "heliart_variant": variant,
                "heliart_toolchain_tag": toolchain_tag,
            },
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
    dist_path: Path, *, board: str, toolchain_tag: str, variant: str,
    core_override: str | None = None,
) -> None:
    """Fail fast if the required ``.a`` is missing from the distribution."""
    core = _core_tag(board, override=core_override)
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
    module_dir: Path, dist_path: Path, *, variant: str,
    core_override: str | None = None,
) -> None:
    """Install the NSX module files and distribution content into *module_dir*.

    Requires the distribution to ship a native ``nsx/`` module
    (heliaRT >= 1.12.2). The ``HELIART_VARIANT`` default is patched to
    match the user's requested *variant*.
    """
    nsx_dir = dist_path / "nsx"
    src_cmake = nsx_dir / "CMakeLists.txt"
    src_yaml = nsx_dir / "nsx-module.yaml"
    if not src_cmake.is_file() or not src_yaml.is_file():
        raise EngineError(
            f"heliaRT distribution at {dist_path} is missing nsx/ module files",
            hint=(
                f"Expected nsx/CMakeLists.txt and nsx/nsx-module.yaml. "
                f"Use heliaRT >= v{HELIART_MIN_VERSION}."
            ),
        )

    shutil.copy2(src_yaml, module_dir / "nsx-module.yaml")

    cmake_text = src_cmake.read_text()
    if variant != "release-with-logs":
        cmake_text = cmake_text.replace(
            'set(HELIART_VARIANT "release-with-logs"',
            f'set(HELIART_VARIANT "{variant}"',
        )
    if core_override:
        # Hack: override the auto-detected core tag so we can force
        # e.g. the cm4 (non-MVE) library on an M55 board.
        # Inject a forced set() after the core-detection block by finding
        # the "# --- Resolve toolchain tag ---" marker.
        tag = core_override.lower()
        cmake_text = cmake_text.replace(
            '# --- Resolve toolchain tag ---',
            f'# core_override: force {tag} library on this board\n'
            f'set(_HELIART_CORE "{tag}")\n\n'
            f'# --- Resolve toolchain tag ---',
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
        "No dist_path or source configured — "
        "fetching heliaRT %s from %s",
        HELIART_RELEASE_TAG,
        HELIART_GH_REPO,
    )
    return _fetch_github_release(
        HELIART_GH_REPO, HELIART_RELEASE_TAG, api_s=api_s, asset_s=asset_s,
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
                "Provide a valid release tag (e.g. heliaRT-v1.12.2), "
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

    # Maybe ref is just a version like "1.7.0" — try common tag formats
    for fmt in (f"heliaRT-v{ref}", f"HeliaRT-v{ref}", f"v{ref}"):
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
    candidates = sorted(
        n for n in asset_names
        if n.startswith("helia-rt-") and n.endswith(".zip")
    )
    if not candidates:
        return None
    if len(candidates) > 1:
        log.warning(
            "Multiple heliaRT release assets matched 'helia-rt-*.zip' for %s @ %s; "
            "picking %s. Candidates: %s",
            repo, tag, candidates[0], candidates,
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
                name = name[len(strip_prefix):]
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
    1. ``heliart_version.h`` — ``#define HELIART_VERSION "v1.7.0"``
    2. ``MANIFEST.txt`` — ``neuralspot-helios-rt HeliaRT-v1.7.0``
    """
    # 1. Version header
    version_h = dist / "tensorflow" / "lite" / "micro" / "heliart_version.h"
    if version_h.is_file():
        text = version_h.read_text(errors="replace")
        m = re.search(r'#define\s+HELIART_VERSION\s+"v?([^"]+)"', text)
        if m:
            return m.group(1)

    # 2. MANIFEST.txt
    manifest = dist / "MANIFEST.txt"
    if manifest.is_file():
        first_line = manifest.read_text(errors="replace").split("\n")[0]
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
    dist: Path, detected_version: str | None,
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
            "heliaRT v%s is newer than the pinned default v%s — "
            "proceeding (>= min v%s).",
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
