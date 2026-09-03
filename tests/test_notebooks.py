"""Structural checks for the shipped example notebooks.

These guard the two properties that actually break readers: the code must be
valid Python, and it must drive HPX through the typed public API rather than
shelling out or reaching into a repository checkout. Someone who downloads a
notebook on its own has neither ``uv run`` nor ``pyproject.toml`` available.

Every notebook under ``examples/notebooks/`` is checked, so a new one is
covered the moment it lands.

Deliberately *not* asserted: cell counts, prose wording, or section titles.
Those change every time someone improves a notebook, and pinning them buys
nothing but failed CI on documentation edits.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

NOTEBOOK_DIR = Path(__file__).resolve().parent.parent / "examples" / "notebooks"
NOTEBOOKS = sorted(NOTEBOOK_DIR.glob("*.ipynb"))

#: Patterns that tie a notebook to a shell or a repository checkout. Each of
#: these has shipped in a notebook before and broken it for readers who
#: installed HPX from PyPI.
FORBIDDEN = (
    "subprocess",
    "shell=True",
    "os.system",
    "uv run hpx",
    'os.environ["PATH"]',
    'REPO_ROOT / "examples"',
    "examples/quickstart/kws_model.tflite",
    "SEGGER_RTT_ROOT",
)


def _cells(notebook: Path) -> list[dict]:
    return json.loads(notebook.read_text(encoding="utf-8"))["cells"]


def _code(notebook: Path) -> str:
    return "\n\n".join(
        "".join(cell["source"]) for cell in _cells(notebook) if cell["cell_type"] == "code"
    )


def test_notebooks_exist() -> None:
    """The docs site links into this directory; an empty glob is a silent pass."""
    assert NOTEBOOKS, f"no notebooks found under {NOTEBOOK_DIR}"


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_code_parses(notebook: Path) -> None:
    ast.parse(_code(notebook))


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_cell_ids_are_unique(notebook: Path) -> None:
    """nbformat 4.5+ requires a unique id per cell; duplicates corrupt diffs."""
    cell_ids = [cell.get("id") or cell.get("metadata", {}).get("id") for cell in _cells(notebook)]

    assert all(cell_ids), f"{notebook.name} has cells without ids"
    assert len(cell_ids) == len(set(cell_ids)), f"{notebook.name} has duplicate cell ids"


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_uses_the_typed_session_api(notebook: Path) -> None:
    code = _code(notebook)

    assert "import helia_profiler as hpx" in code
    assert "hpx.Session()" in code


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_runs_from_an_installed_package(notebook: Path) -> None:
    """No shelling out, and no dependence on a repository checkout."""
    offenders = [pattern for pattern in FORBIDDEN if pattern in _code(notebook)]

    assert not offenders, f"{notebook.name} depends on the repo or a shell: {offenders}"


@pytest.mark.parametrize("notebook", NOTEBOOKS, ids=lambda p: p.stem)
def test_notebook_gates_hardware_behind_a_toggle(notebook: Path) -> None:
    """Hardware cells must be switchable, so the notebook reads without an EVB."""
    code = _code(notebook)

    assert "RUN_HARDWARE" in code, f"{notebook.name} has no RUN_HARDWARE toggle"
    assert "if RUN_HARDWARE" in code, f"{notebook.name} defines RUN_HARDWARE but never gates on it"
