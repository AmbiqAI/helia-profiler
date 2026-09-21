"""Regenerate or verify the committed CLI, configuration and issue-code JSON.

``--check`` re-runs the extractors in process and compares the result with the
committed files. Nothing here reads rendered ``--help`` output, so the check is
immune to terminal width, ``rich`` styling and locale; there is no ``COLUMNS``
pin because there is nothing to pin.

Four comparisons, none of them byte equality:

* CLI: the command tree is flattened to one entry per command path, and options
  and arguments to one entry per declaration. A drift is reported as the
  command path plus the option that was added, removed or changed.
* Configuration: per class, the field name set and each field's type signature,
  default and required flag. Key order, ``$defs`` ordering and the recorded tool
  versions are all ignored, so a pydantic upgrade that reshuffles the document
  without changing the contract stays green.
* Issue codes: per code, the severity envelope, metric group and description,
  and per family the expanded wire codes.
* The source audit, an independent AST parse of the same CLI modules.

``generatedFrom`` is outside all four. It carries the source tree, which moves
with every commit under ``src/``, so comparing it would report every commit as
drift; presence and agreement across the three artifacts are asserted on their
own instead. That is also why ``--check`` needs no ``--source-tree``: the fresh
build is stamped with whatever the committed artifacts claim, and the claim
itself is what gets checked.

    uv run --isolated --no-dev python tools/docs/check_reference.py --check
    uv run --isolated --no-dev python tools/docs/check_reference.py --write \
        --source-tree $(git rev-parse HEAD:src/helia_profiler)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_cli  # noqa: E402
import extract_issues  # noqa: E402
import extract_schema  # noqa: E402
from _common import dump, source_tree  # noqa: E402
from source_audit import audit  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = REPO_ROOT / "astro-site" / "src" / "data"

#: Artifact stem to extractor, in the order the summary prints them.
ARTIFACTS = ("cli", "schema", "issues")

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


def _flatten_issues(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in payload["issues"]:
        out[f"issue {row['code']}"] = {k: v for k, v in row.items() if k != "code"}
    for row in payload["comparability"]:
        out[f"comparability {row['code']}"] = {k: v for k, v in row.items() if k != "code"}
    for row in payload["families"]:
        out[f"family {row['pattern']}"] = {k: v for k, v in row.items() if k != "pattern"}
    return out


def diff_issues(committed: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    old, new = _flatten_issues(committed), _flatten_issues(fresh)
    problems: list[str] = []
    for key in sorted(set(new) - set(old)):
        problems.append(f"{key}: registered in source, absent from issues.json")
    for key in sorted(set(old) - set(new)):
        problems.append(f"{key}: in issues.json, no longer registered in source")
    for key in sorted(set(old) & set(new)):
        for field in sorted(set(old[key]) | set(new[key])):
            if old[key].get(field) != new[key].get(field):
                problems.append(
                    f"{key}: {field} "
                    f"{json.dumps(old[key].get(field))} -> {json.dumps(new[key].get(field))}"
                )
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


def check_provenance(committed: dict[str, dict[str, Any]]) -> list[str]:
    """Provenance on its own, since the diffs exclude it.

    Three artifacts generated from one source tree have to claim that one
    tree; a mismatch means one of them was regenerated and the others were
    not, which no semantic diff would see.
    """
    problems: list[str] = []
    trees = {}
    for stem, payload in committed.items():
        recorded = payload.get("generatedFrom", {})
        tree = recorded.get("sourceTree")
        try:
            trees[stem] = source_tree(tree or "")
        except ValueError:
            problems.append(f"{stem}.json: generatedFrom.sourceTree is {tree!r}, not a git tree.")
        for tool in ("typer", "click", "pydantic"):
            if not recorded.get(tool):
                problems.append(f"{stem}.json: generatedFrom records no resolved {tool} version.")
        if "sourceCommit" in recorded:
            problems.append(
                f"{stem}.json: records a source commit, which does not survive a squash merge."
            )
    if len(set(trees.values())) > 1:
        problems.append(
            "the artifacts claim different source trees: "
            + ", ".join(f"{stem}={tree[:7]}" for stem, tree in sorted(trees.items()))
        )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--source-tree", type=source_tree, default=None)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args(argv)

    paths = {stem: args.data_dir / f"{stem}.json" for stem in ARTIFACTS}

    if args.write:
        if args.source_tree is None:
            parser.error(
                "--write needs --source-tree: an artifact with no provenance claims a "
                "source it cannot name. The docs build passes "
                "`git rev-parse HEAD:src/helia_profiler`."
            )
        payloads = {
            "cli": extract_cli.build(args.source_tree),
            "schema": extract_schema.build(args.source_tree),
            "issues": extract_issues.build(args.source_tree),
        }
        for stem, payload in payloads.items():
            dump(payload, paths[stem])
        print(f"wrote {', '.join(sorted(p.name for p in paths.values()))} to {args.data_dir}")
        return 0

    problems = [
        f"missing committed artifact: {path}" for path in paths.values() if not path.exists()
    ]
    if problems:
        _report(problems)
        return 1

    committed = {stem: json.loads(path.read_text(encoding="utf-8")) for stem, path in paths.items()}
    problems += check_provenance(committed)

    # Stamp the fresh build with what the committed artifacts claim: the diffs
    # ignore provenance, and check_provenance has already judged the claim.
    claimed = committed["cli"].get("generatedFrom", {}).get("sourceTree", "")
    if not problems:
        fresh = {
            "cli": extract_cli.build(claimed),
            "schema": extract_schema.build(claimed),
            "issues": extract_issues.build(claimed),
        }
        problems += diff_cli(committed["cli"], fresh["cli"])
        problems += diff_schema(committed["schema"], fresh["schema"])
        problems += diff_issues(committed["issues"], fresh["issues"])
        problems += cross_check_source(fresh["cli"])

        for name, old_version in committed["cli"]["generatedFrom"].items():
            new_version = fresh["cli"]["generatedFrom"].get(name)
            if name != "sourceTree" and old_version != new_version:
                print(f"note: {name} {old_version} -> {new_version} (not a failure)")

    if problems:
        _report(problems)
        return 1

    cli_counts = committed["cli"]["counts"]
    schema_counts = committed["schema"]["counts"]
    issue_counts = committed["issues"]["counts"]
    versions = committed["cli"]["generatedFrom"]
    print(
        f"ok: {cli_counts['leaf_commands']} leaf commands, {cli_counts['groups']} groups, "
        f"{cli_counts['top_level_entries']} top-level entries, {cli_counts['options']} options, "
        f"{cli_counts['arguments']} arguments, {cli_counts['options_with_panel']} panelled "
        f"options in {len(cli_counts['panel_names'])} panels, {cli_counts['epilogs']} epilogs, "
        f"{cli_counts['options_with_envvar']} environment variables; "
        f"{schema_counts['classes']} config classes, {schema_counts['declaredFields']} fields, "
        f"{schema_counts['schemaProperties']} schema properties; "
        f"{issue_counts['issues']} issue codes, {issue_counts['comparability']} comparability "
        f"codes, {issue_counts['families']} families ({issue_counts['familyCodes']} codes); "
        f"typer {versions['typer']}, click {versions['click']}, pydantic {versions['pydantic']}, "
        f"source tree {versions['sourceTree'][:7]}."
    )
    return 0


def _report(problems: list[str]) -> None:
    print(
        "reference drift detected: the committed JSON no longer matches the source.",
        file=sys.stderr,
    )
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nRegenerate from astro-site with:\n"
        "  npm run reference:build\n"
        "then review the diff and update the authored prose that references the "
        "changed commands or keys.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
