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

from .fixture_compile_cases import FIXTURE_KIND_ENGINES, FIXTURE_SCOPES, render_fixture

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "fixture_render.json"
_UPDATE = os.environ.get("HPX_UPDATE_SNAPSHOTS") == "1"
_CASES = [
    (kind, engine, scope) for kind, engine in FIXTURE_KIND_ENGINES for scope in FIXTURE_SCOPES
]


def _digest(kind: str, engine: str, scope: str) -> dict[str, str]:
    text, headers = render_fixture(kind, engine, scope)
    digests = {"fixed_fixture.cc": hashlib.sha256(text.encode()).hexdigest()}
    digests.update(
        {name: hashlib.sha256(content.encode()).hexdigest() for name, content in headers.items()}
    )
    return digests


def _key(kind: str, engine: str, scope: str) -> str:
    return f"{kind}|{engine}|{scope}"


if _UPDATE:
    _SNAPSHOT_PATH.write_text(
        json.dumps({_key(*case): _digest(*case) for case in _CASES}, indent=2, sort_keys=True)
        + "\n"
    )

_SNAPSHOTS: dict = json.loads(_SNAPSHOT_PATH.read_text()) if _SNAPSHOT_PATH.exists() else {}


@pytest.mark.parametrize("kind,engine,scope", _CASES, ids=[_key(*case) for case in _CASES])
def test_fixture_render_matches_snapshot(kind, engine, scope):
    assert _digest(kind, engine, scope) == _SNAPSHOTS.get(_key(kind, engine, scope))


def test_snapshot_file_has_no_stale_cases():
    assert set(_SNAPSHOTS) == {_key(*case) for case in _CASES}


@pytest.mark.parametrize("engine", ["helia-aot", "litert"])
def test_power_terminal_written_from_live_main_before_initialization(engine):
    text, _ = render_fixture("power-resource", engine, "restore_and_invoke")
    main = text.split("int main() {", 1)[1]
    initialization = main.index("nsx_system_init")
    for index, value in enumerate(("0x50575231", "2", "29", "36", "14")):
        assert main.index(f"deployment_power[{index}] = {value}U;") < initialization


@pytest.mark.parametrize("engine", ["helia-aot", "litert"])
def test_power_idle_before_model_and_done_after_terminal(engine):
    text, _ = render_fixture("power-resource", engine, "restore_and_invoke")
    main = text.split("int main() {", 1)[1]
    assert (
        main.index("nsx_system_init")
        < main.index("hpx_sync_init()")
        < main.index("infer_fixture()")
    )
    assert (
        main.index("deployment_status = status")
        < main.index("if (status == 0)", main.index("deployment_status = status"))
        < main.index("hpx_pin_set(kStateGpioPin, true)")
    )
    assert "if (!hpx_sync_wait_go()) return -11;" in text
    body = text.split("static int infer_fixture() {", 1)[1].split("int main() {", 1)[0]
    assert "hpx_sync_init()" not in body
    assert body.index("hpx_sync_window_begin()") < body.index("const uint32_t t0")
    assert body.index("ticks = hpx_stimer_ticks() - t0") < body.index("hpx_sync_window_end()")
