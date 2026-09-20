"""Shared helpers for the docs-site reference extractors.

Named ``_common`` rather than ``_json``: the latter shadows the stdlib C
accelerator module of that name once ``tools/docs`` is on ``sys.path``.
"""

from __future__ import annotations

import enum
import json
import pathlib
import re
from typing import Any

SCHEMA_VERSION = 1

#: The tree whose provenance every artifact records, matching the
#: ``SOURCE_PATH`` the site's build chain documents.
SOURCE_PATH = "src/helia_profiler"

_TREE = re.compile(r"^[0-9a-f]{40}$")


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


def source_tree(value: str) -> str:
    """Validate the git tree sha the build chain hands down.

    The tree of ``src/helia_profiler`` is the identity these artifacts record:
    a commit sha rewrites every generated file on every commit and does not
    survive a squash merge, and a branch name is a property of the build, not
    of the source. Resolving it here would be a second copy of the policy the
    site's build chain already owns, including how it refuses a checkout with
    no git data, so the sha arrives as an argument and is only checked.
    """
    if not _TREE.match(value):
        raise ValueError(
            f"--source-tree must be the 40-hex git tree of {SOURCE_PATH}, got {value!r}. "
            "The docs build passes it; run the extractors through "
            "`npm run reference:build` rather than by hand."
        )
    return value


def provenance(tree: str) -> dict[str, str]:
    return {"sourceTree": source_tree(tree), **tool_versions()}
