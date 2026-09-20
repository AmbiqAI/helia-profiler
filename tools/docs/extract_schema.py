"""Emit ``schema.json`` for ``ProfileConfig`` and its nested config models.

The document is standard JSON Schema straight from pydantic, so any JSON
Schema renderer can consume it, plus an ``x-hpx`` index that JSON Schema
cannot express: the declared Python annotation, the dotted config key each
class hangs off, and the field order as written in the source.

``MonitorBoardPreset`` is not reachable from ``ProfileConfig`` (it is the
value type of the ``INA228_BOARD_PRESETS`` lookup table, surfaced through a
property rather than a field), so the roots are enumerated per class instead
of walking down from ``ProfileConfig`` alone.

    uv run --isolated --no-dev python tools/docs/extract_schema.py --out schema.json
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import re
import sys
import typing
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SCHEMA_VERSION, dump, jsonable, tool_versions  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "astro-site" / "src" / "data" / "schema.json"
ROOT_CLASS = "ProfileConfig"
_MODULE_PREFIX = re.compile(r"\b(?:[A-Za-z_]\w*\.)+([A-Z]\w*)")


def _type_name(obj: Any) -> str:
    if isinstance(obj, type):
        return obj.__name__
    return _MODULE_PREFIX.sub(r"\1", str(obj).replace("typing.", ""))


def _config_classes() -> list[Any]:
    from helia_profiler import config as cfg
    from helia_profiler.config import power as power_cfg

    seen: dict[str, type] = {}
    for module in (cfg, power_cfg):
        for name in dir(module):
            obj = getattr(module, name)
            if not isinstance(obj, type) or not dataclasses.is_dataclass(obj):
                continue
            if not hasattr(obj, "__pydantic_fields__"):
                continue
            if not obj.__module__.startswith("helia_profiler.config"):
                continue
            seen.setdefault(obj.__qualname__, obj)
    return sorted(seen.values(), key=lambda c: c.__name__)


def _enums() -> list[type[enum.StrEnum]]:
    from helia_profiler import config as cfg
    from helia_profiler.config import power as power_cfg

    seen: dict[str, type[enum.StrEnum]] = {}
    for module in (cfg, power_cfg):
        for name in dir(module):
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, enum.StrEnum) and obj is not enum.StrEnum:
                if obj.__module__.startswith("helia_profiler.config"):
                    seen.setdefault(obj.__name__, obj)
    return sorted(seen.values(), key=lambda c: c.__name__)


def _field_index(cls: Any) -> list[dict[str, Any]]:
    try:
        hints = typing.get_type_hints(cls, include_extras=True)
    except Exception:
        hints = {}
    rows: list[dict[str, Any]] = []
    for field in dataclasses.fields(cls):
        has_default = field.default is not dataclasses.MISSING
        has_factory = field.default_factory is not dataclasses.MISSING
        default: Any = None
        if has_default:
            default = jsonable(field.default)
        elif has_factory:
            try:
                default = jsonable(field.default_factory())
            except Exception:
                default = None
        rows.append(
            {
                "name": field.name,
                "pythonType": _type_name(hints.get(field.name, field.type)),
                "required": not (has_default or has_factory),
                "default": default,
                "defaultFromFactory": has_factory,
            }
        )
    return rows


def _sections(classes: dict[str, Any]) -> dict[str, str]:
    """Map dotted config key to class name by walking down from the root."""
    by_type = {cls: name for name, cls in classes.items()}
    out: dict[str, str] = {"": ROOT_CLASS}

    def visit(cls: Any, prefix: str) -> None:
        try:
            hints = typing.get_type_hints(cls, include_extras=True)
        except Exception:
            return
        for field in dataclasses.fields(cls):
            hint = hints.get(field.name)
            for candidate in (hint, *typing.get_args(hint or int)):
                if candidate in by_type:
                    key = f"{prefix}{field.name}"
                    if key not in out:
                        out[key] = by_type[candidate]
                        visit(candidate, f"{key}.")
                    break

    visit(classes[ROOT_CLASS], "")
    return out


def build() -> dict[str, Any]:
    from pydantic import TypeAdapter

    classes = {cls.__name__: cls for cls in _config_classes()}
    root = classes[ROOT_CLASS]
    schema = TypeAdapter(root).json_schema(mode="validation", ref_template="#/$defs/{model}")
    defs: dict[str, Any] = schema.pop("$defs", {})

    unreachable: list[str] = []
    for name, cls in classes.items():
        if name == ROOT_CLASS or name in defs:
            continue
        unreachable.append(name)
        extra = TypeAdapter(cls).json_schema(mode="validation", ref_template="#/$defs/{model}")
        for dep_name, dep in extra.pop("$defs", {}).items():
            defs.setdefault(dep_name, dep)
        defs[name] = extra

    defs[ROOT_CLASS] = {k: v for k, v in schema.items() if k != "$schema"}

    index = {name: _field_index(cls) for name, cls in classes.items()}
    coverage = {
        name: {
            "declared": len(index[name]),
            "inSchema": len(defs.get(name, {}).get("properties", {})),
            "reachableFromRoot": name not in unreachable,
        }
        for name in sorted(classes)
    }

    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "config-schema",
        "generatedFrom": tool_versions(),
        "counts": {
            "classes": len(classes),
            "declaredFields": sum(c["declared"] for c in coverage.values()),
            "schemaProperties": sum(c["inSchema"] for c in coverage.values()),
            "enums": len(_enums()),
            "unreachableFromRoot": unreachable,
        },
        "x-hpx": {
            "root": ROOT_CLASS,
            "sections": _sections(classes),
            "fieldIndex": index,
            "coverage": coverage,
            "enums": {
                enum_cls.__name__: [member.value for member in enum_cls] for enum_cls in _enums()
            },
        },
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": f"#/$defs/{ROOT_CLASS}",
        "$defs": dict(sorted(defs.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    payload = build()
    if args.stdout:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    dump(payload, args.out)
    counts = payload["counts"]
    print(
        f"wrote {args.out} ({counts['classes']} classes, "
        f"{counts['declaredFields']} declared fields, "
        f"{counts['schemaProperties']} schema properties)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
