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
_CACHE_ROOT = Path.home() / ".cache" / "helia-profiler" / "models"


def tiny_cnn() -> Path:
    """Materialize the packaged tiny CNN and return its stable cache path."""
    return _materialize_model("tiny-cnn")


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

    destination = _CACHE_ROOT / name / expected_digest[:12] / filename
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected_digest:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return destination