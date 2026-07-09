"""Generate docs/reference/configuration.md from the ProfileConfig dataclasses.

The generated page is the single source of truth for the key-by-key config
schema: it is derived mechanically from ``helia_profiler.config`` so it can
never drift from the real validation rules. Regenerate after any change to
the config dataclasses:

    uv run python tools/gen_config_reference.py

``tests/test_config_reference_docs.py`` fails CI if the committed page is
stale relative to the models.
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing
from enum import Enum
from pathlib import Path

from helia_profiler import config as cfg

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "docs" / "reference" / "configuration.md"

_MISSING = dataclasses.MISSING

# Explicit notes for fields whose semantics cannot be derived mechanically
# from the type system alone. Keyed by dotted path (section path + field
# name), e.g. "model.model_location".
_EXPLICIT_NOTES: dict[str, str] = {
    "model.model_location": (
        "**Deprecated** — prefer `arena_location`/`weights_location` for placement control."
    ),
    "profiling.pmu_presets": "**Deprecated** — prefer `pmu_counters`.",
    "keep_work_dir": "**Deprecated** — no-op, the cache work directory is always kept.",
    "engine.type": "`tflm` is temporarily unavailable — use `helia-rt` for the interpreter runtime.",
    "engine.config": "free-form engine-specific mapping (not strictly validated).",
    "target.custom_socs": "advanced raw mapping validated by the platform layer.",
    "target.custom_boards": "advanced raw mapping validated by the platform layer.",
}

# Root dataclass fields that are resolved at runtime and never user-settable;
# excluded from every rendered section.
_EXCLUDED_ROOT_FIELDS = {"platform_registry"}


@dataclasses.dataclass
class FieldRow:
    key: str
    type_str: str
    default_str: str
    notes: str


@dataclasses.dataclass
class Section:
    path: tuple[str, ...]
    cls: type
    rows: list[FieldRow]


def _section_title(path: tuple[str, ...]) -> str:
    if not path:
        return "Top-level keys"
    return ".".join(path)


def _docstring(cls: type) -> str:
    """Class docstring as Markdown prose.

    Numpydoc-style ``Attributes`` blocks are stripped: their setext-style
    underline renders as a broken heading in Markdown, and the per-field
    details are already covered by the generated table.
    """
    doc = inspect.getdoc(cls)
    if not doc:
        return ""
    lines = doc.strip().splitlines()
    for i in range(len(lines) - 1):
        if lines[i].strip() in ("Attributes", "Attributes:") and set(
            lines[i + 1].strip()
        ) == {"-"}:
            lines = lines[:i]
            break
    return "\n".join(lines).rstrip()


def _is_enum(tp: object) -> bool:
    return isinstance(tp, type) and issubclass(tp, Enum)


def _is_union(origin: object) -> bool:
    return origin is typing.Union or origin is types.UnionType


def _render_type(tp: object) -> str:
    origin = typing.get_origin(tp)

    if origin is dict:
        args = typing.get_args(tp)
        if len(args) == 2:
            return f"dict[{_render_type(args[0])}, {_render_type(args[1])}]"
        return "dict"

    if origin is list:
        args = typing.get_args(tp)
        if args:
            return f"list[{_render_type(args[0])}]"
        return "list"

    if origin is tuple:
        args = typing.get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return f"tuple[{_render_type(args[0])}, ...]"
        if args:
            return "tuple[" + ", ".join(_render_type(a) for a in args) + "]"
        return "tuple"

    if _is_union(origin):
        args = typing.get_args(tp)
        has_none = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        # Tolerant-validation pattern: an enum field also accepts a raw str
        # so unrecognized values pass through to the validator's error path.
        # The user-facing surface is the enum's allowed values, not "| str".
        has_enum = any(_is_enum(a) for a in non_none)
        if has_enum and str in non_none:
            non_none = [a for a in non_none if a is not str]
        rendered = " \\| ".join(_render_type(a) for a in non_none)
        if has_none:
            rendered = f"{rendered} \\| null" if rendered else "null"
        return rendered

    if _is_enum(tp):
        return " \\| ".join(member.value for member in tp)

    if tp is type(None):
        return "null"

    if tp is typing.Any:
        return "Any"

    if isinstance(tp, type):
        return tp.__name__

    return str(tp)


def _format_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        return "[" + ", ".join(_format_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{}" if not value else str(value)
    return str(value)


def _mechanical_note(name: str, tp: object) -> str:  # noqa: ARG001 - tp reserved for future rules
    """Best-effort note derived only from unambiguous, mechanical signals."""
    if name.endswith("_ms"):
        return "units: milliseconds"
    if name.endswith("_hz"):
        return "units: hertz"
    if name.endswith("_s") and not name.endswith("bytes_s"):
        return "units: seconds"
    return ""


def _default_for_field(field: dataclasses.Field, tp: object) -> tuple[str, str]:
    """Return (default_str, extra_note) for a dataclass field."""
    if field.default is not _MISSING:
        return _format_value(field.default), ""
    if field.default_factory is not _MISSING:  # type: ignore[misc]
        value = field.default_factory()  # type: ignore[misc]
        if dataclasses.is_dataclass(value):
            return "see section below", ""
        if isinstance(value, dict) and typing.get_origin(tp) is dict:
            value_args = typing.get_args(tp)
            if len(value_args) == 2 and dataclasses.is_dataclass(value_args[1]):
                return _format_value(value), "see subsection below for the per-entry schema"
        return _format_value(value), ""
    return "—", ""


def _collect_sections() -> list[Section]:
    sections: list[Section] = []

    def visit(cls: type, path: tuple[str, ...]) -> None:
        fields = [f for f in dataclasses.fields(cls) if f.name not in _EXCLUDED_ROOT_FIELDS]
        hints = typing.get_type_hints(cls)
        rows: list[FieldRow] = []
        child_visits: list[tuple[type, tuple[str, ...]]] = []

        for f in fields:
            tp = hints.get(f.name, typing.Any)
            dotted = ".".join((*path, f.name))

            # Nested dataclass field (e.g. target.clock) — gets its own
            # section; parent table row just points at it.
            nested_dc: type | None = None
            wildcard_dc: type | None = None
            union_args = typing.get_args(tp)
            for cand in (tp, *union_args):
                if dataclasses.is_dataclass(cand):
                    nested_dc = cand
                    break
                if typing.get_origin(cand) is dict:
                    value_args = typing.get_args(cand)
                    if len(value_args) == 2 and dataclasses.is_dataclass(value_args[1]):
                        wildcard_dc = value_args[1]
                        break

            default_str, extra_note = _default_for_field(f, tp)
            note = _EXPLICIT_NOTES.get(dotted, "")
            if not note:
                note = extra_note or _mechanical_note(f.name, tp)

            rows.append(
                FieldRow(
                    key=f.name,
                    type_str=_render_type(tp),
                    default_str=default_str,
                    notes=note,
                )
            )

            if nested_dc is not None:
                child_visits.append((nested_dc, (*path, f.name)))
            elif wildcard_dc is not None:
                child_visits.append((wildcard_dc, (*path, f.name, "<name>")))

        sections.append(Section(path=path, cls=cls, rows=rows))
        for child_cls, child_path in child_visits:
            visit(child_cls, child_path)

    visit(cfg.ProfileConfig, ())
    return sections


def _split_root_section(sections: list[Section]) -> list[Section]:
    """Split the root ProfileConfig section into per-nested-dataclass
    sections (already emitted as children) plus a trailing "top-level keys"
    section containing only the plain scalar fields.
    """
    root = sections[0]
    assert root.path == ()
    nested_names = {
        s.path[0] for s in sections[1:] if len(s.path) == 1 and s.path[0] != "<name>"
    }
    top_level_rows = [row for row in root.rows if row.key not in nested_names]
    rest = sections[1:]
    top_section = Section(path=("__top_level__",), cls=cfg.ProfileConfig, rows=top_level_rows)
    return [*rest, top_section]


def _render_table(rows: list[FieldRow]) -> str:
    lines = ["| Key | Type | Default | Notes |", "|---|---|---|---|"]
    for row in rows:
        lines.append(f"| `{row.key}` | {row.type_str} | `{row.default_str}` | {row.notes} |")
    return "\n".join(lines)


def render() -> str:
    sections = _collect_sections()
    sections = _split_root_section(sections)

    lines: list[str] = []
    lines.append("# Configuration Reference")
    lines.append("")
    lines.append(
        "This page is **generated** from the `ProfileConfig` pydantic dataclasses in "
        "`src/helia_profiler/config.py` — it is the single source of truth for every "
        "config key, its type, default, and status. Regenerate it after any config model "
        "change with:"
    )
    lines.append("")
    lines.append("```bash")
    lines.append("uv run python tools/gen_config_reference.py")
    lines.append("```")
    lines.append("")
    lines.append(
        "Unknown keys anywhere in the config tree are rejected at load time with "
        "did-you-mean suggestions drawn from these same models — see "
        "[Configuration](../guide/configuration.md#validation) for the general validation "
        "behavior."
    )
    lines.append("")

    for section in sections:
        title = "Top-level keys" if section.path == ("__top_level__",) else _section_title(
            section.path
        )
        lines.append(f"## `{title}`" if section.path != ("__top_level__",) else f"## {title}")
        lines.append("")
        doc = _docstring(section.cls)
        if doc:
            lines.append(doc)
            lines.append("")
        lines.append(_render_table(section.rows))
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
