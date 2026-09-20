"""Regenerate or verify the committed CLI and configuration JSON.

``--check`` re-runs the extractors in process and compares the result with
the committed files. Nothing here reads rendered ``--help`` output, so the
check is immune to terminal width, ``rich`` styling and locale; there is no
``COLUMNS`` pin because there is nothing to pin.

Two comparisons, neither of them byte equality:

* CLI: the command tree is flattened to one entry per command path, and
  options and arguments to one entry per declaration. A drift is reported as
  the command path plus the option that was added, removed or changed.
* Configuration: per class, the field name set and each field's type
  signature, default and required flag. Key order, ``$defs`` ordering and
  the recorded tool versions are all ignored, so a pydantic upgrade that
  reshuffles the document without changing the contract stays green.

    uv run --isolated --no-dev python tools/docs/check_reference.py --check
    uv run --isolated --no-dev python tools/docs/check_reference.py --write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_cli  # noqa: E402
import extract_schema  # noqa: E402
from _common import dump  # noqa: E402
from source_audit import audit  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_JSON = REPO_ROOT / "spike" / "data" / "cli.json"
SCHEMA_JSON = REPO_ROOT / "spike" / "data" / "schema.json"

_PARAM_KEYS = (
    "kind",
    "type",
    "python_type",
    "default",
    "required",
    "help",
    "panel",
    "aliases",
    "secondary_opts",
    "is_flag",
    "multiple",
    "count",
    "nargs",
    "hidden",
    "envvar",
)


def _flatten_cli(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    flat: dict[str, dict[str, Any]] = {}

    def visit(node: dict[str, Any]) -> None:
        key = " ".join(node["path"]) or "hpx"
        params: dict[str, Any] = {}
        for param in node["arguments"] + node["options"]:
            params[param["declaration"]] = {k: param[k] for k in _PARAM_KEYS}
        flat[key] = {
            "kind": node["kind"],
            "help": node["help"],
            "short_help": node["short_help"],
            "epilog": node["epilog"],
            "deprecated": node["deprecated"],
            "hidden": node["hidden"],
            "panels": node["panels"],
            "params": params,
        }
        for child in node.get("commands", []):
            visit(child)

    visit(payload["root"])
    return flat


def diff_cli(committed: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    old, new = _flatten_cli(committed), _flatten_cli(fresh)
    problems: list[str] = []
    for name in sorted(set(new) - set(old)):
        problems.append(f"command added in source, absent from cli.json: hpx {name}")
    for name in sorted(set(old) - set(new)):
        problems.append(f"command in cli.json no longer registered in source: hpx {name}")
    for name in sorted(set(old) & set(new)):
        a, b = old[name], new[name]
        for key in ("kind", "help", "short_help", "epilog", "deprecated", "hidden", "panels"):
            if a[key] != b[key]:
                problems.append(f"hpx {name}: {key} changed")
        for decl in sorted(set(b["params"]) - set(a["params"])):
            problems.append(f"hpx {name}: option added in source, absent from cli.json: {decl}")
        for decl in sorted(set(a["params"]) - set(b["params"])):
            problems.append(f"hpx {name}: option in cli.json no longer in source: {decl}")
        for decl in sorted(set(a["params"]) & set(b["params"])):
            for key in _PARAM_KEYS:
                if a["params"][decl][key] != b["params"][decl][key]:
                    problems.append(
                        f"hpx {name} {decl}: {key} "
                        f"{json.dumps(a['params'][decl][key])} -> "
                        f"{json.dumps(b['params'][decl][key])}"
                    )
    return problems


def _type_signature(prop: dict[str, Any]) -> str:
    if "$ref" in prop:
        return prop["$ref"]
    if "anyOf" in prop:
        return "anyOf[" + ",".join(sorted(_type_signature(p) for p in prop["anyOf"])) + "]"
    if "enum" in prop:
        return "enum[" + ",".join(str(v) for v in prop["enum"]) + "]"
    base = prop.get("type", "unknown")
    if base == "array" and "items" in prop:
        return f"array[{_type_signature(prop['items'])}]"
    return str(base)


def _flatten_schema(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    defs = payload["$defs"]
    index = payload["x-hpx"]["fieldIndex"]
    out: dict[str, dict[str, Any]] = {}
    for cls, fields in index.items():
        props = defs.get(cls, {}).get("properties", {})
        out[cls] = {
            row["name"]: {
                "pythonType": row["pythonType"],
                "required": row["required"],
                "default": row["default"],
                "schemaType": _type_signature(props.get(row["name"], {})),
            }
            for row in fields
        }
    return out


def diff_schema(committed: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    old, new = _flatten_schema(committed), _flatten_schema(fresh)
    problems: list[str] = []
    for cls in sorted(set(new) - set(old)):
        problems.append(f"config class added in source, absent from schema.json: {cls}")
    for cls in sorted(set(old) - set(new)):
        problems.append(f"config class in schema.json no longer in source: {cls}")
    for cls in sorted(set(old) & set(new)):
        a, b = old[cls], new[cls]
        for name in sorted(set(b) - set(a)):
            problems.append(f"{cls}.{name}: field added in source, absent from schema.json")
        for name in sorted(set(a) - set(b)):
            problems.append(f"{cls}.{name}: field in schema.json no longer in source")
        for name in sorted(set(a) & set(b)):
            for key in ("pythonType", "schemaType", "required", "default"):
                if a[name][key] != b[name][key]:
                    problems.append(
                        f"{cls}.{name}: {key} "
                        f"{json.dumps(a[name][key])} -> {json.dumps(b[name][key])}"
                    )
    old_enums = committed["x-hpx"]["enums"]
    new_enums = fresh["x-hpx"]["enums"]
    for name in sorted(set(old_enums) | set(new_enums)):
        if old_enums.get(name) != new_enums.get(name):
            problems.append(f"enum {name}: members changed")
    return problems


def cross_check_source(payload: dict[str, Any]) -> list[str]:
    """Compare cli.json against an AST parse of the same CLI modules."""
    source = audit()
    flat = _flatten_cli(payload)
    problems: list[str] = []
    json_paths = {tuple(k.split(" ")) for k in flat if k != "hpx"} | {()}
    source_paths = set(source.commands)
    for path in sorted(source_paths - json_paths):
        problems.append(f"registered in source but not in cli.json: hpx {' '.join(path)}")
    for path in sorted(json_paths - source_paths):
        problems.append(f"in cli.json but no registration found in source: hpx {' '.join(path)}")
    for path in sorted(source_paths & json_paths):
        key = " ".join(path) or "hpx"
        declared = {p["name"] for p in payload_params(payload, path)}
        expected = set(source.commands[path].options) | set(source.commands[path].arguments)
        for name in sorted(expected - declared):
            problems.append(f"hpx {key}: {name} declared in source, missing from cli.json")
        for name in sorted(declared - expected):
            problems.append(f"hpx {key}: {name} in cli.json, no typer declaration in source")
    return problems


def payload_params(payload: dict[str, Any], path: tuple[str, ...]) -> list[dict[str, Any]]:
    node = payload["root"]
    for part in path:
        node = next(c for c in node["commands"] if c["name"] == part)
    return node["arguments"] + node["options"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    fresh_cli = extract_cli.build()
    fresh_schema = extract_schema.build()

    if args.write:
        dump(fresh_cli, CLI_JSON)
        dump(fresh_schema, SCHEMA_JSON)
        print(f"wrote {CLI_JSON.relative_to(REPO_ROOT)} and {SCHEMA_JSON.relative_to(REPO_ROOT)}")
        return 0

    problems: list[str] = []
    for path in (CLI_JSON, SCHEMA_JSON):
        if not path.exists():
            problems.append(f"missing committed artifact: {path.relative_to(REPO_ROOT)}")
    if problems:
        _report(problems)
        return 1

    committed_cli = json.loads(CLI_JSON.read_text(encoding="utf-8"))
    committed_schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))

    problems += diff_cli(committed_cli, fresh_cli)
    problems += diff_schema(committed_schema, fresh_schema)
    problems += cross_check_source(fresh_cli)

    for name, old_version in committed_cli["generatedFrom"].items():
        new_version = fresh_cli["generatedFrom"].get(name)
        if old_version != new_version:
            print(f"note: {name} {old_version} -> {new_version} (not a failure)")

    if problems:
        _report(problems)
        return 1
    counts = fresh_cli["counts"]
    schema_counts = fresh_schema["counts"]
    print(
        f"ok: {counts['leaf_commands']} leaf commands, {counts['groups']} groups, "
        f"{counts['options']} options, {counts['arguments']} arguments; "
        f"{schema_counts['classes']} config classes, "
        f"{schema_counts['declaredFields']} fields"
    )
    return 0


def _report(problems: list[str]) -> None:
    print("reference drift detected: the committed JSON no longer matches the source.", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nRegenerate with:\n"
        "  uv run --isolated --no-dev python tools/docs/check_reference.py --write\n"
        "then review the diff and update the authored prose that references the "
        "changed commands or keys.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
