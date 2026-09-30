"""heliaRT GitHub release download helpers.

Split out of :mod:`.artifacts` to keep the distribution-resolution module
focused: this module only talks to the GitHub Releases API and unpacks the
downloaded archive.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
import zlib
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ...errors import EngineError
from .artifacts import _ASSET_FMT, _cache_dir, _detect_version, _is_valid_dist, _validate_dist

log = logging.getLogger("hpx")


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
    cache_dir = _cache_dir() / _cache_key(repo, ref)

    if cache_dir.is_dir() and _is_valid_dist(cache_dir):
        log.info("Cache hit: %s", cache_dir)
        return cache_dir, _detect_version(cache_dir)

    tag = _resolve_release_tag(repo, ref, api_s=api_s)
    if tag is None:
        raise EngineError(
            f"No GitHub release found for {repo}@{ref}",
            hint=(
                "Provide a valid release tag (e.g. helia-rt-v1.16.0), "
                "or set engine.config.dist_path to a local directory."
            ),
        )

    asset_url = _find_release_asset(repo, tag, api_s=api_s)
    if asset_url is None:
        raise EngineError(
            f"No downloadable asset found for {repo} release {tag}",
            hint="Set engine.config.dist_path to a local heliaRT distribution.",
        )

    log.info("Downloading heliaRT from %s ...", asset_url)
    _download_and_extract(asset_url, cache_dir, timeout_s=asset_s)
    return cache_dir, _detect_version(cache_dir)


_PLAIN_REPO = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9.-]+")
_PLAIN_REF = re.compile(r"[A-Za-z0-9._-]+")


def _cache_key(repo: str, ref: str) -> str:
    """Map *repo* and *ref* to a unique single path component under the cache root.

    Both are user config; separators or ``..`` must not steer the cache
    directory (which may be deleted when stale) outside the cache root.
    ``owner/name`` repos without ``_`` and plain refs keep the historical
    ``owner_name_ref`` key, which is unambiguous because the first two
    ``_`` split it. Anything else is sanitized and suffixed with ``+`` and
    a digest of the raw pair; ``+`` never occurs in a plain key.
    """
    if _PLAIN_REPO.fullmatch(repo) and _PLAIN_REF.fullmatch(ref):
        return f"{repo.replace('/', '_')}_{ref}"
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "_", f"{repo}_{ref}")
    digest = hashlib.sha256(json.dumps([repo, ref]).encode()).hexdigest()[:12]
    return f"{sanitized}+{digest}"


def _resolve_release_tag(repo: str, ref: str, *, api_s: float = 30) -> str | None:
    """Resolve *ref* to a GitHub release tag.

    If *ref* already looks like a release tag, verify it exists.
    Otherwise query the releases API for the latest release.
    """
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

    name = _ASSET_FMT.format(tag=tag)
    if name in asset_names:
        return asset_names[name]

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
    """Download a zip from *url* and install it as the distribution at *dest*.

    If the zip contains a single top-level directory, its contents are
    extracted directly into *dest* (strip one level). The archive is
    extracted into a temporary sibling of *dest* and validated there, so
    *dest* only ever appears as a complete distribution.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            data = resp.read()
    except (URLError, OSError, HTTPException) as exc:
        raise EngineError(
            f"Failed to download heliaRT release: {exc}",
            hint="Check your network connection or set engine.config.dist_path.",
        ) from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{dest.name}.", dir=dest.parent))
    try:
        _extract_zip(data, staging, source=url)
        _validate_dist(staging)
        _install_dir(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    log.info("Extracted heliaRT distribution to %s", dest)


def _extract_zip(data: bytes, dest: Path, *, source: str) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise EngineError(
            f"Downloaded heliaRT release is not a valid zip archive: {source}",
            hint="Retry the download or set engine.config.dist_path.",
        ) from exc

    root = dest.resolve()
    with zf:
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
            out = (root / name).resolve()
            if out == root or not out.is_relative_to(root):
                raise EngineError(
                    f"heliaRT release archive entry escapes the extraction "
                    f"directory: {member.filename!r} ({source})",
                    hint="The archive is malformed or malicious; check engine.config.source.repo.",
                )
            out.parent.mkdir(parents=True, exist_ok=True)
            try:
                out.write_bytes(zf.read(member))
            except (zipfile.BadZipFile, EOFError, zlib.error) as exc:
                raise EngineError(
                    f"Corrupt entry {member.filename!r} in heliaRT release archive: {source}",
                    hint="Retry the download or set engine.config.dist_path.",
                ) from exc


def _install_dir(staging: Path, dest: Path) -> None:
    """Move *staging* to *dest*, replacing a stale (invalid) *dest*.

    A valid *dest* that appears concurrently wins; *staging* is discarded.
    """
    if dest.exists():
        if _is_valid_dist(dest):
            return
        # Rename aside so rmtree only touches this snapshot; a concurrent
        # replacer that got there first surfaces as FileNotFoundError.
        stale = dest.with_name(f"{staging.name}.stale")
        try:
            dest.rename(stale)
        except FileNotFoundError:
            pass
        else:
            shutil.rmtree(stale, ignore_errors=True)
    try:
        staging.rename(dest)
    except OSError:
        # Windows refuses to rename onto an existing path; POSIX refuses a
        # non-empty one. Either way another process installed it first.
        if not _is_valid_dist(dest):
            raise
