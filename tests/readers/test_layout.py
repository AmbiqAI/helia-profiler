"""Offline reader initialization and canonical API objects."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src/helia_profiler"


def test_readers_initializer_has_no_execution():
    tree = ast.parse((PACKAGE / "readers/__init__.py").read_text())
    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


@pytest.mark.host_integration
@pytest.mark.parametrize("api_first", [True, False])
def test_reader_import_order_preserves_api_without_capture(api_first):
    first = (
        "from helia_profiler import api"
        if api_first
        else ("from helia_profiler.readers import native, profile_view, run_view")
    )
    code = f"""
import sys
{first}
from helia_profiler import api
from helia_profiler.readers import native, profile_view, run_view
assert api.load_result is native.load_result
assert api.inspect_result is native.inspect_result
assert api.observations is profile_view.observations
assert api.export_result is profile_view.export_result
assert profile_view.ProfileResultRecord is native.ProfileResultRecord
assert profile_view.RunResultRecord is run_view.RunResultRecord
forbidden = ('pylink', 'pyjoulescope_driver', 'neuralspotx',
             'helia_profiler.target', 'helia_profiler.fixtures.capture',
             'helia_profiler.runtime.prepare', 'helia_profiler.profiler')
assert not [name for name in sys.modules if name.startswith(forbidden)]
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
