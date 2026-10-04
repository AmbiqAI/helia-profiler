"""Contract: fixed-fixture render snapshots for every kind, engine and timing scope.

Digests the production ``fixed_fixture.cc.j2`` render and its generated headers.
Regenerate after an intentional template change with::

    HPX_UPDATE_SNAPSHOTS=1 pytest tests/contracts/test_fixture_render_snapshots.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from .fixture_compile_cases import (
    FIXTURE_ENGINES,
    FIXTURE_GATED_CASES,
    FIXTURE_KINDS,
    FIXTURE_SCOPES,
    render_fixture,
)

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "fixture_render.json"
_UPDATE = os.environ.get("HPX_UPDATE_SNAPSHOTS") == "1"
_CASES = [
    (kind, engine, scope, False)
    for kind in FIXTURE_KINDS
    for engine in FIXTURE_ENGINES
    for scope in FIXTURE_SCOPES
] + list(FIXTURE_GATED_CASES)


def _digest(kind: str, engine: str, scope: str, gate: bool) -> dict[str, str]:
    text, headers = render_fixture(kind, engine, scope, energy_gate=gate)
    digests = {"fixed_fixture.cc": hashlib.sha256(text.encode()).hexdigest()}
    digests.update(
        {name: hashlib.sha256(content.encode()).hexdigest() for name, content in headers.items()}
    )
    return digests


def _key(kind: str, engine: str, scope: str, gate: bool) -> str:
    return f"{kind}|{engine}|{scope}" + ("|energy_gate" if gate else "")


if _UPDATE:
    _SNAPSHOT_PATH.write_text(
        json.dumps({_key(*case): _digest(*case) for case in _CASES}, indent=2, sort_keys=True)
        + "\n"
    )

_SNAPSHOTS: dict = json.loads(_SNAPSHOT_PATH.read_text()) if _SNAPSHOT_PATH.exists() else {}


@pytest.mark.parametrize("kind,engine,scope,gate", _CASES, ids=[_key(*case) for case in _CASES])
def test_fixture_render_matches_snapshot(kind, engine, scope, gate):
    assert _digest(kind, engine, scope, gate) == _SNAPSHOTS.get(_key(kind, engine, scope, gate))


def test_snapshot_file_has_no_stale_cases():
    assert set(_SNAPSHOTS) == {_key(*case) for case in _CASES}
