"""Walk the ``hpx`` Typer app into ``cli.json`` for the docs site.

Introspection targets the click command objects Typer builds, not rendered
``--help`` text, so terminal width and rich formatting never enter the
artifact. Typer vendors click (``typer._click``), so ``isinstance`` against
the top-level ``click`` package is always False here; group detection is
duck-typed on ``.commands`` instead.

    uv run --isolated --no-dev python tools/docs/extract_cli.py --source-tree <sha>
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
import typing
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import SCHEMA_VERSION, dump, jsonable, provenance, source_tree  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO_ROOT / "astro-site" / "src" / "data" / "cli.json"


def _load_app() -> Any:
    from typer.main import get_command

    from helia_profiler.cli.app import app

    return get_command(app)


def _annotations(command: Any) -> dict[str, str]:
    """Map parameter name to its resolved Python annotation.

    ``cli/app.py`` uses ``from __future__ import annotations``, so the raw
    signature only carries strings; resolving gives the real Annotated type.
    """
    callback = getattr(command, "callback", None)
    if callback is None:
        return {}
    try:
        hints = typing.get_type_hints(callback, include_extras=True)
    except Exception:
        return {}
    out: dict[str, str] = {}
    for name, hint in hints.items():
        if name == "return":
            continue
        args = typing.get_args(hint)
        base = args[0] if typing.get_origin(hint) is not None and args else hint
        out[name] = _type_name(base)
    return out


_MODULE_PREFIX = re.compile(r"\b(?:[A-Za-z_]\w*\.)+([A-Z]\w*)")


def _type_name(obj: Any) -> str:
    if isinstance(obj, type):
        return obj.__name__
    text = str(obj).replace("typing.", "")
    # Annotations resolve to fully qualified names (pathlib._local.Path on
    # 3.13); the docs site wants the bare class the user would type.
    return _MODULE_PREFIX.sub(r"\1", text)


def _source(command: Any) -> dict[str, Any] | None:
    callback = getattr(command, "callback", None)
    if callback is None:
        return None
    try:
        file = inspect.getsourcefile(callback)
        line = inspect.getsourcelines(callback)[1]
    except (TypeError, OSError):
        return None
    if file is None:
        return None
    path = Path(file)
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        rel = (
            Path(*path.parts[path.parts.index("helia_profiler") :])
            if "helia_profiler" in path.parts
            else path
        )
    return {"path": str(rel), "line": line}


def _param_type(param: Any) -> dict[str, Any]:
    ptype = param.type
    info: dict[str, Any] = {"name": ptype.name, "class": type(ptype).__name__}
    choices = getattr(ptype, "choices", None)
    if choices is not None:
        info["choices"] = [str(c) for c in choices]
    for attr in ("exists", "file_okay", "dir_okay", "readable", "writable"):
        if hasattr(ptype, attr):
            info[attr] = bool(getattr(ptype, attr))
    for attr in ("min", "max"):
        if getattr(ptype, attr, None) is not None:
            info[attr] = jsonable(getattr(ptype, attr))
    return info


def _envvar(param: Any) -> list[str]:
    """Normalise ``envvar`` to a list so the field is empty, never absent."""
    raw = getattr(param, "envvar", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(item) for item in raw]


def _param(param: Any, annotations: dict[str, str]) -> dict[str, Any]:
    opts = list(param.opts)
    kind = param.param_type_name
    long_opts = [o for o in opts if o.startswith("--")]
    aliases = [o for o in opts if o.startswith("-") and not o.startswith("--")]
    return {
        "name": param.name,
        "kind": kind,
        "declaration": long_opts[0] if long_opts else (opts[0] if opts else param.name),
        "opts": opts,
        "aliases": aliases,
        "secondary_opts": list(param.secondary_opts),
        "metavar": param.metavar,
        "type": _param_type(param),
        "python_type": annotations.get(param.name),
        "default": jsonable(param.default),
        "required": bool(param.required),
        "help": getattr(param, "help", None),
        "panel": getattr(param, "rich_help_panel", None),
        "is_flag": bool(getattr(param, "is_flag", False)),
        "multiple": bool(param.multiple),
        "count": bool(getattr(param, "count", False)),
        "nargs": param.nargs,
        "hidden": bool(getattr(param, "hidden", False)),
        "envvar": _envvar(param),
    }


def _usage(command: Any, path: list[str]) -> str:
    pieces = ["hpx", *path]
    if getattr(command, "commands", None):
        pieces.append("COMMAND [ARGS]...")
    else:
        if any(p.param_type_name == "option" for p in command.params):
            pieces.append("[OPTIONS]")
        for param in command.params:
            if param.param_type_name != "argument":
                continue
            token = (param.metavar or param.name).upper()
            pieces.append(token if param.required else f"[{token}]")
    return " ".join(pieces)


def _node(command: Any, path: list[str]) -> dict[str, Any]:
    annotations = _annotations(command)
    params = [_param(p, annotations) for p in command.params if p.name != "help"]
    subcommands = getattr(command, "commands", None)
    options = [p for p in params if p["kind"] == "option"]
    node: dict[str, Any] = {
        "path": list(path),
        "name": path[-1] if path else "hpx",
        "kind": "group" if subcommands else "command",
        "usage": _usage(command, path),
        "short_help": command.get_short_help_str(limit=120) or None,
        "help": inspect.cleandoc(command.help) if command.help else None,
        # Epilogs carry indented example blocks; cleandoc would flatten them.
        "epilog": command.epilog or None,
        "deprecated": bool(command.deprecated),
        "hidden": bool(getattr(command, "hidden", False)),
        "source": _source(command),
        "arguments": [p for p in params if p["kind"] == "argument"],
        "options": options,
        "panels": sorted({p["panel"] for p in options if p["panel"]}),
    }
    if subcommands is not None:
        node["commands"] = [_node(sub, [*path, name]) for name, sub in sorted(subcommands.items())]
    return node


def _counts(root: dict[str, Any]) -> dict[str, Any]:
    leaves: list[str] = []
    groups: list[str] = []
    epilogs: list[str] = []
    panels: set[str] = set()
    panel_options = 0
    options = 0
    arguments = 0

    def visit(node: dict[str, Any]) -> None:
        nonlocal panel_options, options, arguments
        label = " ".join(node["path"]) or "hpx"
        (groups if node["kind"] == "group" else leaves).append(label)
        if node["epilog"]:
            epilogs.append(label)
        options += len(node["options"])
        arguments += len(node["arguments"])
        panel_options += sum(1 for o in node["options"] if o["panel"])
        panels.update(node["panels"])
        for child in node.get("commands", []):
            visit(child)

    visit(root)
    return {
        "leaf_commands": len(leaves),
        "leaf_command_names": leaves,
        "groups": len(groups),
        "group_names": groups,
        "top_level_entries": len(root.get("commands", [])),
        "epilogs": len(epilogs),
        "epilog_commands": epilogs,
        "options": options,
        "arguments": arguments,
        "options_with_panel": panel_options,
        "panel_names": sorted(panels),
        "options_with_envvar": _envvar_total(root),
    }


def _envvar_total(node: dict[str, Any]) -> int:
    total = sum(1 for p in node["options"] + node["arguments"] if p["envvar"])
    for child in node.get("commands", []):
        total += _envvar_total(child)
    return total


def build(tree: str) -> dict[str, Any]:
    root = _node(_load_app(), [])
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "cli",
        "program": "hpx",
        "generatedFrom": provenance(tree),
        "counts": _counts(root),
        "root": root,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--source-tree", type=source_tree, required=True)
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)
    payload = build(args.source_tree)
    if args.stdout:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        dump(payload, args.out)
        counts = payload["counts"]
        print(
            f"wrote {args.out} "
            f"({counts['leaf_commands']} leaf commands, {counts['groups']} groups, "
            f"{counts['options']} options)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
