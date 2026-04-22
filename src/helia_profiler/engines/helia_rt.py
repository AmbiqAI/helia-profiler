"""heliaRT engine adapter.

Resolves a heliaRT distribution (prebuilt ``.a`` + TFLM headers) and wraps
it as a local NSX module for the profiler firmware build.

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
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import jinja2

from ..config import ProfileConfig
from ..errors import EngineError
from ..platform import CoreArch, get_soc
from ..results import NsxModuleRef
from .base import EngineArtifacts

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# Pinned heliaRT release — bump this when a new release is adopted.
# ---------------------------------------------------------------------------
HELIART_VERSION = "1.7.0"
HELIART_GH_REPO = "AmbiqAI/helia-rt"
HELIART_RELEASE_TAG = f"HeliaRT-v{HELIART_VERSION}"

# Cache directory for downloaded distributions
_CACHE_DIR = Path.home() / ".cache" / "helia-profiler" / "heliart"

# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_jinja_env = jinja2.Environment(
    loader=jinja2.PackageLoader("helia_profiler.engines", "templates"),
    keep_trailing_newline=True,
    undefined=jinja2.StrictUndefined,
)


def _core_tag(board: str) -> str:
    """Map a board name to the heliaRT library core tag (cm4 or cm55)."""
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
    then generates a local NSX wrapper module at
    ``work_dir/modules/nsx-heliart/``.

    The wrapper compiles platform glue (``micro_log.cc``) because current
    prebuilt ``.a`` files leave ``MicroPrintf`` undefined.  Once a future
    release bundles it, switch to the native ``nsx/`` module.
    """

    @property
    def name(self) -> str:
        return "heliaRT"

    def prepare(self, config: ProfileConfig, work_dir: Path) -> EngineArtifacts:
        backend = config.engine.backend or "helia"
        variant = config.engine.config.get("variant", "release-with-logs")

        # Validate variant
        valid_variants = ("debug", "release-with-logs", "release")
        if variant not in valid_variants:
            raise EngineError(
                f"Invalid heliaRT variant '{variant}'",
                hint=f"Valid variants: {', '.join(valid_variants)}",
            )

        # Resolve the heliaRT distribution
        dist_path, resolved_version = _resolve_distribution(config)

        # Version compatibility check
        _check_version_compatibility(dist_path, resolved_version)

        # Generate the NSX wrapper module
        module_dir = work_dir / "modules" / "nsx-heliart"
        module_dir.mkdir(parents=True, exist_ok=True)

        version = resolved_version or HELIART_VERSION
        _write_wrapper(module_dir, variant=variant, version=version)
        _link_distribution(module_dir, dist_path)

        log.info(
            "heliaRT %s (variant=%s, dist=%s)",
            version,
            variant,
            dist_path,
        )

        return EngineArtifacts(
            extra_modules=[
                NsxModuleRef(
                    name="nsx-heliart",
                    path=module_dir,
                    version=version,
                ),
            ],
            template_vars={
                "engine_type": "helia_rt",
                "engine_backend": backend,
                "engine_header": "tensorflow/lite/micro/micro_interpreter.h",
                "heliart_version": version,
                "heliart_variant": variant,
            },
        )


def _write_wrapper(module_dir: Path, *, variant: str, version: str) -> None:
    """Write the NSX wrapper files into *module_dir*."""
    (module_dir / "nsx-module.yaml").write_text(
        _jinja_env.get_template("heliart_nsx_module.yaml.j2").render(
            version=version,
        ),
    )
    (module_dir / "CMakeLists.txt").write_text(
        _jinja_env.get_template("heliart_CMakeLists.txt.j2").render(
            version=version,
            variant=variant,
        ),
    )


# ---------------------------------------------------------------------------
# heliaRT distribution resolution (multi-mode)
# ---------------------------------------------------------------------------

# Directories required in a valid heliaRT distribution.
_DIST_DIRS = ("lib", "tensorflow", "third_party", "signal")

# GitHub release asset naming conventions.
# Two bundles exist per release:
#   neuralspot-helios-rt-{TAG}.zip  — legacy neuralSPOT bundle
#   nsx-heliart-{TAG}.zip           — NSX module bundle (preferred)
_NSX_ASSET_FMT = "nsx-heliart-{tag}.zip"
_LEGACY_ASSET_FMT = "neuralspot-helios-rt-{tag}.zip"


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
    if source and isinstance(source, dict):
        repo = source.get("repo", HELIART_GH_REPO)
        ref = source.get("ref", HELIART_RELEASE_TAG)
        return _fetch_github_release(repo, ref)

    # --- 4. Default: pinned version from default repo ---
    log.info(
        "No dist_path or source configured — "
        "fetching heliaRT %s from %s",
        HELIART_RELEASE_TAG,
        HELIART_GH_REPO,
    )
    return _fetch_github_release(HELIART_GH_REPO, HELIART_RELEASE_TAG)


# ---------------------------------------------------------------------------
# GitHub release download
# ---------------------------------------------------------------------------


def _fetch_github_release(repo: str, ref: str) -> tuple[Path, str | None]:
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
    tag = _resolve_release_tag(repo, ref)
    if tag is None:
        raise EngineError(
            f"No GitHub release found for {repo}@{ref}",
            hint=(
                "Provide a valid release tag (e.g. HeliaRT-v1.7.0), "
                "or set engine.config.dist_path to a local directory."
            ),
        )

    # Try downloading: NSX bundle first, legacy bundle as fallback
    asset_url = _find_release_asset(repo, tag)
    if asset_url is None:
        raise EngineError(
            f"No downloadable asset found for {repo} release {tag}",
            hint="Set engine.config.dist_path to a local heliaRT distribution.",
        )

    log.info("Downloading heliaRT from %s ...", asset_url)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _download_and_extract(asset_url, cache_dir)

    _validate_dist(cache_dir)
    return cache_dir, _detect_version(cache_dir)


def _resolve_release_tag(repo: str, ref: str) -> str | None:
    """Resolve *ref* to a GitHub release tag.

    If *ref* already looks like a release tag, verify it exists.
    Otherwise query the releases API for the latest release.
    """
    # Direct tag reference — verify it exists
    api = f"https://api.github.com/repos/{repo}/releases/tags/{ref}"
    data = _github_api_get(api)
    if data is not None:
        return ref

    # Maybe ref is just a version like "1.7.0" — try common tag formats
    for fmt in (f"HeliaRT-v{ref}", f"v{ref}"):
        api = f"https://api.github.com/repos/{repo}/releases/tags/{fmt}"
        data = _github_api_get(api)
        if data is not None:
            return fmt

    # Branch or other ref — try latest release from the repo
    api = f"https://api.github.com/repos/{repo}/releases/latest"
    data = _github_api_get(api)
    if data is not None:
        tag = data.get("tag_name")
        log.warning(
            "ref '%s' is not a release tag — falling back to latest release: %s",
            ref,
            tag,
        )
        return tag

    return None


def _find_release_asset(repo: str, tag: str) -> str | None:
    """Find the download URL for the best release asset."""
    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    data = _github_api_get(api)
    if data is None:
        return None

    assets = data.get("assets", [])
    asset_names = {a["name"]: a["browser_download_url"] for a in assets}

    # Prefer NSX bundle, fall back to legacy neuralSPOT bundle
    for fmt in (_NSX_ASSET_FMT, _LEGACY_ASSET_FMT):
        name = fmt.format(tag=tag)
        if name in asset_names:
            return asset_names[name]

    # If exact naming doesn't match, try partial matching
    for name, url in asset_names.items():
        if name.endswith(".zip"):
            log.info("Using release asset: %s", name)
            return url

    return None


def _github_api_get(url: str) -> dict | None:
    """Make a GET request to the GitHub API.  Returns None on 404."""
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        if exc.code == 404:
            return None
        log.warning("GitHub API error %s for %s", exc.code, url)
        return None
    except (URLError, OSError) as exc:
        log.warning("GitHub API request failed: %s", exc)
        return None


def _download_and_extract(url: str, dest: Path) -> None:
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
        with urlopen(req, timeout=300) as resp:
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
    """Warn or error on version mismatches between dist and adapter."""
    if detected_version is None:
        log.warning(
            "Could not detect heliaRT version from distribution at %s — "
            "skipping compatibility check",
            dist,
        )
        return

    expected = _parse_semver(HELIART_VERSION)
    actual = _parse_semver(detected_version)

    if actual == expected:
        return

    if actual[0] != expected[0]:
        raise EngineError(
            f"heliaRT major version mismatch: "
            f"distribution is v{detected_version}, adapter expects v{HELIART_VERSION}",
            hint=(
                "Major version changes may have breaking API differences. "
                "Update HELIART_VERSION in the adapter or provide a compatible distribution."
            ),
        )

    if actual[:2] != expected[:2]:
        log.warning(
            "heliaRT minor version mismatch: "
            "distribution is v%s, adapter expects v%s — "
            "proceed with caution",
            detected_version,
            HELIART_VERSION,
        )
    else:
        log.info(
            "heliaRT patch version differs: v%s (dist) vs v%s (expected)",
            detected_version,
            HELIART_VERSION,
        )


def _link_distribution(module_dir: Path, dist_path: Path) -> None:
    """Copy heliaRT distribution directories into the wrapper module.

    Copies lib/, tensorflow/, and third_party/ so the CMake build can find
    them via ``CMAKE_CURRENT_LIST_DIR``.  Uses copies instead of symlinks
    for Windows compatibility.
    """
    import shutil

    for d in _DIST_DIRS:
        target = module_dir / d
        source = dist_path / d
        if target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        log.debug("Copied %s → %s", target, source)
