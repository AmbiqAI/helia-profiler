"""Packaged assets for examples, smoke tests, and tutorials."""

from __future__ import annotations

import hashlib
import importlib.resources as resources
import json
from pathlib import Path

from .errors import ConfigError

_MODELS = {
    "tiny-cnn": ("tiny_cnn.tflite", "tiny_cnn.json"),
}


def _cache_root() -> Path:
    from .hostenv.cache_dirs import hpx_cache_root

    return hpx_cache_root() / "models"


def tiny_cnn() -> Path:
    """Materialize the packaged tiny CNN and return its stable cache path."""
    return _materialize_model("tiny-cnn")


def ambiq_vela_ini() -> Path:
    """Materialize the packaged Ambiq Vela system configuration (.ini)."""
    data = resources.files("helia_profiler").joinpath("data", "vela", "ambiq_vela.ini").read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    destination = _cache_root().parent / "vela" / digest[:12] / "ambiq_vela.ini"
    return _write_if_missing(destination, data, digest)


def _materialize_model(name: str) -> Path:
    filename, manifest_name = _MODELS[name]
    package_root = resources.files("helia_profiler").joinpath("data", "models")
    manifest = json.loads(package_root.joinpath(manifest_name).read_text(encoding="utf-8"))
    expected_digest = manifest["sha256"]
    data = package_root.joinpath(filename).read_bytes()
    actual_digest = hashlib.sha256(data).hexdigest()
    if actual_digest != expected_digest:
        raise ConfigError(
            f"Packaged example model '{name}' failed its integrity check",
            hint="Reinstall helia-profiler.",
        )

    destination = _cache_root() / name / expected_digest[:12] / filename
    return _write_if_missing(destination, data, expected_digest)


def _write_if_missing(destination: Path, data: bytes, digest: str) -> Path:
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination
