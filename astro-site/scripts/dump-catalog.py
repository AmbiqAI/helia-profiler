"""Read the board and engine registries out of the package source as JSON.

Parsed with `ast`, never imported, for the same reason the Python reference is
generated with griffe: the documentation job installs neither the package nor
its runtime dependencies (see the "Set up Python" step in
.github/workflows/docs.yml), and importing would make a site build depend on a
working profiler environment and, through it, on the pinned NSX toolchain.

Only the fields Home renders are emitted. The registry carries more per board
(GPIO pins, PSRAM size, starter profile), and several of those need the SoC
defaults resolved to mean anything, which is exactly the work an importing
reader would do and a source reader must not guess at.

Standard library only. Runs on the interpreter the docs job already sets up.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

BOARD_MODULE = "platform/board.py"
ENGINE_MODULE = "engines/__init__.py"
REGISTER_CALL = "_register_board"
BOARD_FACTORY = "BoardDef"
ENGINE_ENUM = "EngineType"


def _literal(node: ast.AST) -> object:
    """The constant a node stands for, or raise: nothing here is computed."""
    try:
        return ast.literal_eval(node)
    except ValueError as error:
        raise SystemExit(
            f"{BOARD_MODULE}: expected a literal, got {ast.dump(node)[:120]} ({error})"
        ) from error


def _board_calls(tree: ast.Module) -> list[ast.Call]:
    """Every `BoardDef(...)` handed to `_register_board(...)`, in source order."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != REGISTER_CALL:
            continue
        if not node.args:
            raise SystemExit(f"{BOARD_MODULE}: {REGISTER_CALL}() with no argument.")
        inner = node.args[0]
        if not isinstance(inner, ast.Call) or getattr(inner.func, "id", None) != BOARD_FACTORY:
            raise SystemExit(
                f"{BOARD_MODULE}: {REGISTER_CALL}() was handed something other "
                f"than a {BOARD_FACTORY}(...) literal."
            )
        calls.append(inner)
    return calls


def boards(source_root: Path) -> list[dict]:
    tree = ast.parse((source_root / BOARD_MODULE).read_text(encoding="utf-8"))
    found = []
    for call in _board_calls(tree):
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        name = _literal(call.args[0]) if call.args else _literal(keywords["name"])
        channel = keywords.get("channel")
        if channel is None:
            raise SystemExit(f"{BOARD_MODULE}: board {name!r} declares no channel.")
        found.append(
            {
                "id": name,
                "soc": _literal(keywords["soc"]),
                "channel": _literal(channel),
                "isFpga": bool(_literal(keywords["is_fpga"])) if "is_fpga" in keywords else False,
            }
        )
    if not found:
        raise SystemExit(f"{BOARD_MODULE}: no boards found. The registry shape changed.")
    return found


def engines(source_root: Path) -> list[dict]:
    tree = ast.parse((source_root / ENGINE_MODULE).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == ENGINE_ENUM:
            found = [
                {"id": _literal(item.value)}
                for item in node.body
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
            ]
            if not found:
                raise SystemExit(f"{ENGINE_MODULE}: {ENGINE_ENUM} has no members.")
            return found
    raise SystemExit(f"{ENGINE_MODULE}: no {ENGINE_ENUM} class. The enum moved.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Path to src/helia_profiler.")
    args = parser.parse_args()

    root = Path(args.source)
    if not root.is_dir():
        raise SystemExit(f"{root} is not a directory.")

    json.dump({"boards": boards(root), "engines": engines(root)}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
