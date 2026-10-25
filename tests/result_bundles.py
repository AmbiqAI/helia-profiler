"""Publish a test's hand-written profile output as a result bundle, as ``hpx profile`` does."""

from __future__ import annotations

import hashlib
from pathlib import Path

from helia_profiler.results import ResultArtifact, ResultManifest, ResultValidity, RunStatus

MANIFEST = "result_manifest.json"


def publish_manifest(path: Path) -> Path:
    """Write a complete, valid manifest declaring every file directly in *path*."""
    artifacts = tuple(
        ResultArtifact(
            path=item.name,
            media_type="application/json" if item.suffix == ".json" else "text/csv",
            size_bytes=item.stat().st_size,
            sha256=hashlib.sha256(item.read_bytes()).hexdigest(),
        )
        for item in sorted(path.iterdir())
        if item.is_file() and item.name != MANIFEST
    )
    return ResultManifest(
        schema="hpx.result-manifest",
        schema_version=1,
        run_id=path.name,
        timestamp="2026-07-18T00:00:00+00:00",
        hpx_version="0.1.0",
        status=RunStatus.COMPLETE,
        validity=ResultValidity.VALID,
        issues=(),
        provenance={},
        comparability={},
        artifacts=artifacts,
    ).write(path / MANIFEST)


def published(*paths: Path) -> None:
    """Publish each of *paths* that has no manifest yet."""
    for path in paths:
        if not (path / MANIFEST).exists():
            publish_manifest(path)
