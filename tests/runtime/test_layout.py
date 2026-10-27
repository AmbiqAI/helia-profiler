"""Runtime ownership must survive module nesting and import order."""

from __future__ import annotations

import ast
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "src/helia_profiler"


def _imports(path: Path, package: str) -> set[str]:
    targets = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                target = resolve_name("." * node.level + target, package)
            targets.add(target)
            targets.update(f"{target}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
    return targets


def test_runtime_value_owners_stay_below_orchestration() -> None:
    for file, allowed in (
        ("records.py", {"errors", "platform"}),
        ("archive.py", {"_files", "errors", "results"}),
    ):
        targets = _imports(PACKAGE / "runtime" / file, "helia_profiler.runtime")
        hpx_owners = {t.split(".")[1] for t in targets if t.startswith("helia_profiler.")}
        assert hpx_owners <= allowed
    assert not any(
        t == "helia_profiler" or t.startswith("helia_profiler.")
        for t in _imports(PACKAGE / "_files.py", "helia_profiler")
    )


def test_runtime_package_initializer_has_no_execution() -> None:
    tree = ast.parse((PACKAGE / "runtime/__init__.py").read_text(encoding="utf-8"))
    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


@pytest.mark.host_integration
@pytest.mark.parametrize("root_first", [True, False])
def test_runtime_import_order_preserves_public_objects(root_first: bool) -> None:
    first = "before = hpx.runtimes" if root_first else ""
    code = f"""
import helia_profiler as hpx
{first}
import helia_profiler.runtime
from helia_profiler.runtime import archive, records, prepare
from helia_profiler._files import FixtureFile
assert callable(hpx.runtimes)
assert hpx.runtimes is records.runtimes
assert hpx.FixtureFile is FixtureFile is archive.FixtureFile is prepare.FixtureFile
assert hpx.PreparedUpstreamRuntime is archive.PreparedUpstreamRuntime
assert hpx.RuntimeRecord is records.RuntimeRecord
assert hpx.runtimes()
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
