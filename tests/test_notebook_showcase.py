"""Structural checks for the interactive HPX capability showcase."""

from __future__ import annotations

import ast
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = REPO_ROOT / "examples" / "notebooks" / "hpx_walkthrough.ipynb"
CANDIDATE = REPO_ROOT / "examples" / "notebooks" / "hpx_walkthrough_v2.ipynb"


def test_showcase_is_separate_safe_and_programmatic() -> None:
    assert ORIGINAL.is_file()
    assert CANDIDATE.is_file()
    assert CANDIDATE != ORIGINAL

    notebook = json.loads(CANDIDATE.read_text())
    cells = notebook["cells"]
    cell_ids = [cell.get("id") or cell.get("metadata", {}).get("id") for cell in cells]

    assert notebook["nbformat"] == 4
    assert len(cells) == 32
    assert all(cell_ids)
    assert len(cell_ids) == len(set(cell_ids))

    introduction = "\n".join(cells[0]["source"])
    assert "Get Started with heliaPROFILER" in introduction
    assert "candidate notebook" not in introduction.lower()

    code = "\n\n".join(
        "\n".join(cell["source"])
        for cell in cells
        if cell["cell_type"] == "code"
    )
    ast.parse(code)

    for required in (
        "import helia_profiler as hpx",
        "hpx.Session()",
        "hpx.examples.tiny_cnn()",
        "RUN_HARDWARE =",
        "RUN_PROBE_DISCOVERY =",
        "RUN_POWER = False",
        'TRANSPORT = "rtt"',
        'toolchain="gcc"',
        ".doctor()",
        ".inspect_probes()",
        ".counter_groups()",
        ".with_power(",
        "if RUN_POWER:",
        ".analyze()",
        ".profile()",
        ".compare(",
        "Session.from_yaml",
        "Model Explorer overlays",
    ):
        assert required in code

    for forbidden in (
        "subprocess",
        "shell=True",
        "os.system",
        "uv run hpx",
        "extra_paths",
        'os.environ["PATH"]',
        'REPO_ROOT / "examples"',
        'examples/quickstart/kws_model.tflite',
        "SEGGER_RTT_ROOT",
        "segger_rtt_path",
    ):
        assert forbidden not in code
