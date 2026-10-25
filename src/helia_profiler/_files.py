"""Writing files without exposing a partial one, strict JSON, and paths under named roots."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any


def write_text_atomically(target: Path, text: str) -> None:
    """Replace *target* with *text*.

    An interrupted write leaves the previous file intact, and replacing the
    name rather than writing through it leaves any other link to that file
    unchanged.
    """
    write_bytes_atomically(target, text.encode("utf-8"))


def write_bytes_atomically(target: Path, data: bytes) -> None:
    """Replace *target* with *data*, as :func:`write_text_atomically` does."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_bytes(data)
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)


def read_json(value: str | bytes, *, label: str) -> Any:
    """Parse *value*, refusing a repeated key or a non-finite number, as a *label* record.

    Raises:
        ValueError: Naming the repeated key, the non-finite number or the JSON error.
    """

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"Duplicate {label} field: {key}")
            result[key] = item
        return result

    def constant(text: str) -> Any:
        raise ValueError(f"Nonfinite {label} value: {text}")

    return json.loads(value, object_pairs_hook=unique, parse_constant=constant)


class RootedPaths:
    """Paths recorded as ``{"root": name, "path": relative}`` under named roots."""

    def __init__(self, roots: Mapping[str, Path]) -> None:
        if not roots or any(
            not isinstance(k, str) or not k or not isinstance(v, Path) for k, v in roots.items()
        ):
            raise ValueError("Named explicit Path roots required")
        self.roots = {k: v.resolve() for k, v in roots.items()}

    def encode(self, value: Path) -> dict[str, str]:
        """*value* under the deepest root that holds it."""
        resolved = value.resolve()
        for name, root in sorted(self.roots.items(), key=lambda kv: (-len(kv[1].parts), kv[0])):
            if resolved.is_relative_to(root):
                return {"root": name, "path": resolved.relative_to(root).as_posix()}
        raise ValueError(f"Path has no supplied root: {value}")

    def decode(self, value: Any) -> Path:
        """The path *value* names, refusing an unknown root or a non-canonical path."""
        if not isinstance(value, dict) or value.keys() != {"root", "path"}:
            raise ValueError("A recorded path has missing or unexpected fields")
        name, relative = value["root"], value["path"]
        if not isinstance(name, str) or name not in self.roots or not isinstance(relative, str):
            raise ValueError("Unknown record root/path")
        path = PurePosixPath(relative)
        if (
            not relative
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
            or path.as_posix() != relative
        ):
            raise ValueError("A record requires canonical relative paths")
        result = (self.roots[name] / relative).resolve()
        if not result.is_relative_to(self.roots[name]):
            raise ValueError("A recorded path escapes its root")
        return result
