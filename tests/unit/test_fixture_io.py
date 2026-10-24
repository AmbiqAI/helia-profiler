"""Shared fixture IO policy and compatibility of relocated declarations."""

import json
import pickle
from pathlib import Path

import pytest

import helia_profiler as hpx
from helia_profiler import _fixture_build as build
from helia_profiler import _fixture_io as io
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.fixture_analysis import FixtureTensor


@pytest.mark.parametrize("name", ["FixtureDType", "FixtureCapability"])
def test_root_and_old_imports_share_the_relocated_enum(name):
    cls = getattr(io, name)
    assert cls is getattr(build, name) is getattr(hpx, name)
    assert cls.__module__ == "helia_profiler._fixture_io"


def test_capability_aliases_share_one_table(monkeypatch):
    assert build.FIXTURE_CAPABILITIES is io.FIXTURE_CAPABILITIES
    assert build.FIXTURE_READBACK_BUDGET == io.FIXTURE_READBACK_BUDGET
    table = dict(
        io.FIXTURE_CAPABILITIES[EngineType.HELIA_AOT], float32=io.FixtureCapability.UNSUPPORTED
    )
    monkeypatch.setitem(build.FIXTURE_CAPABILITIES, EngineType.HELIA_AOT, table)
    tensor = FixtureTensor("out", 0, "float32", (1,), None)
    with pytest.raises(ConfigError, match="do not support float32"):
        io.io_capabilities((), (tensor,), EngineType.HELIA_AOT)
    facts = hpx.capabilities()
    assert (
        next(
            item.capability
            for item in facts.engines
            if item.engine is EngineType.HELIA_AOT and item.dtype is io.FixtureDType.FLOAT32
        )
        is io.FixtureCapability.UNSUPPORTED
    )


def test_pre_relocation_enum_pickles_resolve_to_canonical_members():
    # Captured before relocation; regenerating these with the current classes loses the control.
    path = Path(__file__).parents[1] / "fixtures/fixture_io_enum_pickles.json"
    records = json.loads(path.read_text(encoding="utf-8"))
    for name, members in records.items():
        cls = getattr(hpx, name)
        assert set(members) == set(cls.__members__)
        for member, payload in members.items():
            assert pickle.loads(payload.encode("ascii")) is cls[member]


@pytest.mark.parametrize("delta", [-4, 0, 4])
def test_combined_output_budget_boundary(delta):
    first = FixtureTensor(
        "first", 0, "float32", ((io.FIXTURE_READBACK_BUDGET - 4 + delta) // 4,), None
    )
    tail = FixtureTensor("tail", 1, "float32", (1,), None)
    if delta > 0:
        with pytest.raises(ConfigError, match="readback budget"):
            io.io_capabilities((), (first, tail), EngineType.LITERT)
    else:
        assert io.io_capabilities((), (first, tail), EngineType.LITERT) == (
            ("float32", io.FixtureCapability.SUPPORTED),
        )


def test_int32_output_rejection_precedes_budget_and_engine_lookup():
    output = FixtureTensor("out", 0, "int32", (io.FIXTURE_READBACK_BUDGET,), None)
    with pytest.raises(ConfigError, match="INT32 fixture support is input-only"):
        io.io_capabilities((), (output,), EngineType.EXECUTORCH)
