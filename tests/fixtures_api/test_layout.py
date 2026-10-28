"""Fixture declaration ownership and import-order contracts."""

import ast
import subprocess
import sys
from importlib.util import resolve_name
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2] / "src/helia_profiler"


def test_fixture_initializer_has_no_execution():
    tree = ast.parse((PACKAGE / "fixtures/__init__.py").read_text())
    assert all(
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        for node in tree.body
    )


def test_fixture_declarations_and_renderer_do_not_import_orchestrators():
    forbidden = {"helia_profiler.fixtures.build", "helia_profiler.fixtures.capture"}
    for filename, package in (
        ("fixtures/types.py", "helia_profiler.fixtures"),
        ("firmware/fixture.py", "helia_profiler.firmware"),
    ):
        for node in ast.walk(ast.parse((PACKAGE / filename).read_text())):
            if isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = resolve_name("." * node.level + base, package)
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            assert not any(
                target == owner or target.startswith(owner + ".")
                for target in targets
                for owner in forbidden
            ), (filename, node.lineno, targets)


@pytest.mark.host_integration
@pytest.mark.parametrize("renderer_first", [True, False])
def test_fixture_import_order_preserves_class_identity(renderer_first):
    first = (
        "import helia_profiler.firmware.fixture as renderer"
        if renderer_first
        else "before = hpx.TypedFixture"
    )
    code = f"""
import helia_profiler as hpx
{first}
import helia_profiler.firmware.fixture as renderer
from helia_profiler.fixtures import types, io, build
assert hpx.TypedFixture is types.TypedFixture is renderer.TypedFixture is build.TypedFixture
assert hpx.FixtureMethod is types.FixtureMethod
assert hpx.FixtureBuild is types.FixtureBuild
assert hpx.FixtureDType is io.FixtureDType
assert 'helia_profiler.fixtures.capture' not in __import__('sys').modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)
