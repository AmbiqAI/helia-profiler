"""Retained-run package initialization and canonical public objects."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src/helia_profiler"


def test_runs_initializer_has_no_execution():
    tree = ast.parse((PACKAGE / "runs/__init__.py").read_text())
    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


@pytest.mark.host_integration
@pytest.mark.parametrize("root_first", [True, False])
def test_run_import_order_preserves_root_api_without_capture(root_first):
    first = (
        "before = hpx.FrozenRun, hpx.CompiledBuild" if root_first else "import helia_profiler.runs"
    )
    code = f"""
import sys
import helia_profiler as hpx
{first}
from helia_profiler.runs import plan, compiled, record, inputs, guard, expect
assert hpx.FrozenRun is plan.FrozenRun
assert hpx.CompiledBuild is compiled.CompiledBuild
assert hpx.RunResult is compiled.RunResult
assert hpx.RunResultRecord is record.RunResultRecord
assert hpx.OperatorGuard is guard.OperatorGuard
assert hpx.Expect is expect.Expect
assert hpx.build is compiled.build
assert hpx.run_build is compiled.run_build
assert hpx.load_build is compiled.load_build
assert hpx.load_run is record.load_run
assert hpx.capabilities is inputs.capabilities
assert callable(hpx.runtimes)
assert 'helia_profiler.fixtures.capture' not in sys.modules
assert 'helia_profiler.runtime.prepare' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
