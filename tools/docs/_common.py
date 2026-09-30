"""Shared helpers for the docs-site reference extractors.

Named ``_common`` rather than ``_json``: the latter shadows the stdlib C
accelerator module of that name once ``tools/docs`` is on ``sys.path``.
"""

from __future__ import annotations

import enum
import json
import pathlib
from typing import Any

SCHEMA_VERSION = 1


def jsonable(value: Any) -> Any:
    """Coerce a click default or pydantic constraint into JSON."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, pathlib.PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return repr(value)


def dump(payload: dict[str, Any], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def tool_versions() -> dict[str, str]:
    from importlib.metadata import version

    from helia_profiler._version import __version__

    return {
        "helia_profiler": __version__,
        "typer": version("typer"),
        "click": version("click"),
        "pydantic": version("pydantic"),
    }


def provenance() -> dict[str, str]:
    """What produced an artifact: the package and the tool versions it asked.

    No hash of the source is recorded. A tree or commit sha moves on every
    change under ``src/``, including changes that leave the documented contract
    alone, and would rewrite every committed artifact each time. The docs build
    compares the committed files byte for byte with a fresh generation from
    HEAD, which is what ties them to the source, and substitutes the tree into
    the published pages at build time.
    """
    return tool_versions()
