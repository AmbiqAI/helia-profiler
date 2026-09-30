"""Compare firmware object code between two checkouts using the Tier 2 render matrix.

Compiles every runnable Tier 2 leg (profiler, power and fixture renders from the
current checkout) to an object with the warm workspace's own compile command, then
records section sizes and per-function disassembly digests.

    python tools/firmware_codegen_gate.py record OUT.json [--scratch DIR]
    python tools/firmware_codegen_gate.py compare BASE.json HEAD.json

Run ``record`` once in each checkout with the same ``--scratch`` directory so
embedded paths are identical. ``compare`` fails on any profiler or power
difference and on any fixture disassembly difference; fixture section-size
differences are reported for the change description. Debug sections are ignored:
they embed the compile directory, which differs between checkouts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from tests.contracts import test_render_compile_hw as hw  # noqa: E402

_FUNCTION = re.compile(r"^[0-9a-f]+ <(?P<name>[^>]+)>:$")


def _tool(compiler: Path, name: str) -> str:
    for candidate in (compiler.parent / f"arm-none-eabi-{name}", compiler.parent / f"llvm-{name}"):
        if candidate.exists():
            return str(candidate)
    found = shutil.which(f"arm-none-eabi-{name}") or shutil.which(f"llvm-{name}")
    if found is None:
        raise FileNotFoundError(f"no {name} tool for {compiler}")
    return found


def _sections(size_tool: str, obj: Path) -> dict[str, int]:
    out = subprocess.run([size_tool, "-A", str(obj)], capture_output=True, text=True, check=True)
    sections = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith(".") and parts[1].isdigit():
            sections[parts[0]] = int(parts[1])
    return sections


def _functions(objdump: str, obj: Path) -> dict[str, str]:
    out = subprocess.run(
        [objdump, "-d", "-r", "--no-show-raw-insn", str(obj)],
        capture_output=True,
        text=True,
        check=True,
    )
    functions: dict[str, list[str]] = {}
    current = None
    for line in out.stdout.splitlines():
        match = _FUNCTION.match(line.strip())
        if match:
            current = match["name"]
            functions[current] = []
        elif current is not None and line.strip():
            functions[current].append(line.split("\t", 1)[-1].strip())
    return {
        name: hashlib.sha256("\n".join(body).encode()).hexdigest()
        for name, body in functions.items()
    }


def record(out: Path, scratch: Path) -> None:
    marker = scratch / ".hpx-codegen-gate"
    if scratch.exists():
        if any(scratch.iterdir()) and not marker.exists():
            raise SystemExit(f"refusing to clear {scratch}: not a codegen-gate scratch directory")
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    marker.touch()
    results, skipped = {}, {}
    for case in hw._MATRIX:
        workspace = hw._resolve_workspace(case)
        if isinstance(workspace, str):
            skipped[case.case_id] = workspace
            continue
        case_dir, tus = hw._prepare_case(case, workspace, scratch)
        for tu in tus:
            command = hw._compile_command(workspace, case, case_dir, tu)
            if isinstance(command, str):
                skipped[f"{case.case_id}/{tu.name}"] = command
                continue
            obj = tu.with_suffix(".o")
            command = [c for c in command if c not in ("-fsyntax-only", "-Werror")] + [
                "-c",
                "-o",
                str(obj),
            ]
            done = subprocess.run(command, capture_output=True, text=True)
            key = f"{case.case_id}/{tu.name}"
            if done.returncode != 0:
                results[key] = {"error": done.stderr.splitlines()[:20]}
                continue
            results[key] = {
                "kind": "fixture"
                if case.fixture_kind
                else ("power" if case.power_only else "profile"),
                "sections": _sections(_tool(workspace.compiler, "size"), obj),
                "functions": _functions(_tool(workspace.compiler, "objdump"), obj),
            }
    out.write_text(
        json.dumps({"results": results, "skipped": skipped}, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"recorded {len(results)} objects, skipped {len(skipped)}")


def compare(base_path: Path, head_path: Path) -> int:
    base, head = (
        json.loads(p.read_text(encoding="utf-8"))["results"] for p in (base_path, head_path)
    )
    failures, notes = [], []
    for key in sorted(set(base) | set(head)):
        b, h = base.get(key), head.get(key)
        if b is None or h is None or "error" in b or "error" in h:
            failures.append(f"{key}: missing or failed on one side")
            continue
        changed = sorted(
            name
            for name in set(b["functions"]) | set(h["functions"])
            if b["functions"].get(name) != h["functions"].get(name)
        )
        deltas = {
            s: h["sections"].get(s, 0) - b["sections"].get(s, 0)
            for s in set(b["sections"]) | set(h["sections"])
            if not s.startswith(".debug") and h["sections"].get(s, 0) != b["sections"].get(s, 0)
        }
        if b["kind"] == "fixture":
            if changed:
                failures.append(f"{key}: fixture disassembly changed in {changed}")
            if deltas:
                notes.append(f"{key}: fixture section deltas {deltas}")
        elif changed or deltas:
            failures.append(f"{key}: {b['kind']} changed functions={changed} sections={deltas}")
    print(f"compared {len(set(base) | set(head))} objects")
    for line in notes:
        print("NOTE", line)
    for line in failures:
        print("FAIL", line)
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    rec.add_argument("out", type=Path)
    rec.add_argument("--scratch", type=Path, default=Path("/tmp/hpx-codegen-gate"))
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("base", type=Path)
    cmp_.add_argument("head", type=Path)
    args = parser.parse_args()
    if args.command == "record":
        record(args.out, args.scratch)
    else:
        raise SystemExit(compare(args.base, args.head))


if __name__ == "__main__":
    main()
