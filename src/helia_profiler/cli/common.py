"""Shared helpers used across ``hpx`` CLI command modules."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from ..engines import EngineType


class EngineName(StrEnum):
    """``--engine`` choices: the canonical engine names, with ``tflm`` an alias of ``litert``."""

    LITERT = "litert"
    HELIA_RT = "helia-rt"
    HELIA_AOT = "helia-aot"
    EXECUTORCH = "executorch"
    TFLM = "tflm"


def engine_type(name: str) -> EngineType:
    """The engine an ``--engine`` value selects."""
    return EngineType.TFLM if name == EngineName.LITERT else EngineType(name)


def _find_repo_root() -> Path:
    """Locate the helia-profiler checkout root.

    Walks up from this file until a directory containing ``pyproject.toml``
    is found.  Falls back to the current working directory.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "tests").is_dir():
            return parent
    return Path.cwd()
