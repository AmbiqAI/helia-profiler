"""Workflow shell snippets must run on every runner's bash (#293).

Some of these snippets are extracted and executed by the test suite under the
host's own ``/bin/bash``. macOS ships bash 3.2, so a bash-4-only construct
turns the whole macOS lane red on every branch, and the failure names a
`CalledProcessError` rather than the syntax that caused it. Cheaper to catch
the construct here, where the message says what to write instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"

#: Constructs bash 3.2 cannot parse, with the portable spelling to use.
BASH_4_ONLY = {
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^?[^}]*\}": "${x^^} — use tr '[:lower:]' '[:upper:]'",
    r"\$\{[A-Za-z_][A-Za-z0-9_]*,,?[^}]*\}": "${x,,} — use tr '[:upper:]' '[:lower:]'",
    r"(?m)^\s*(?:readarray|mapfile)\b": "readarray/mapfile — use a while-read loop",
    r"(?m)^\s*declare\s+-A\b": "declare -A (associative arrays) — unavailable in 3.2",
}


def _workflows() -> list[Path]:
    return sorted(p for p in WORKFLOW_DIR.glob("*.y*ml") if p.is_file())


def _run_blocks(node: Any, trail: str = "") -> Iterator[tuple[str, str]]:
    """Yield every ``run:`` script in a workflow, with a locating trail."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                yield trail, value
            else:
                yield from _run_blocks(value, f"{trail}.{key}" if trail else str(key))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _run_blocks(value, f"{trail}[{index}]")


@pytest.mark.parametrize("workflow", _workflows(), ids=lambda p: p.name)
def test_no_workflow_uses_a_bash_4_only_construct(workflow: Path) -> None:
    document = yaml.safe_load(workflow.read_text())
    offenses = [
        f"{workflow.name}:{trail} uses {advice}"
        for trail, script in _run_blocks(document)
        for pattern, advice in BASH_4_ONLY.items()
        if re.search(pattern, script)
    ]

    assert not offenses, "\n".join(offenses)


def test_the_guard_would_catch_the_construct_that_broke_the_macos_lane() -> None:
    """A guard nothing can trip is a guard that proves nothing."""
    document = {
        "jobs": {"validate": {"steps": [{"run": 'if [[ "$a" != "${ref,,}" ]]; then :; fi'}]}}
    }

    scripts = [script for _, script in _run_blocks(document)]
    assert len(scripts) == 1
    assert any(re.search(pattern, scripts[0]) for pattern in BASH_4_ONLY)
