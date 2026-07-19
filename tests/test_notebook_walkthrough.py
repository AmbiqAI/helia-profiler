"""Structural checks for the programmatic HPX walkthrough notebook."""

from __future__ import annotations

import ast
import json
from pathlib import Path


NOTEBOOK = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "notebooks"
    / "hpx_walkthrough.ipynb"
)


def test_walkthrough_uses_typed_session_api() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    cells = notebook["cells"]
    cell_ids = [cell.get("id") or cell.get("metadata", {}).get("id") for cell in cells]

    assert len(cells) == 29
    assert all(cell_ids)
    assert len(cell_ids) == len(set(cell_ids))

    code = "\n\n".join(
        "".join(cell["source"]) for cell in cells if cell["cell_type"] == "code"
    )
    ast.parse(code)

    assert "import helia_profiler as hpx" in code
    assert "hpx.Session()" in code
    assert "cwd.parents" in code
    assert "except hpx.HpxError" in code
    assert "subprocess" not in code
    assert "shell=True" not in code
    assert "uv run hpx" not in code
    assert "RUN_HARDWARE = False" in code
