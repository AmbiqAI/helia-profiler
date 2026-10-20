"""A compiled build links the runtime and kernels its plan names, and build() resolves them."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler._fixture_build import (
    FixtureMethod,
    FixtureOutput,
    FixtureTimingScope,
    TypedFixture,
)
from helia_profiler.errors import ConfigError
from helia_profiler.frozen_run import _ENVIRONMENT_VARIABLES
from helia_profiler.lifecycle import Lifecycle
from helia_profiler.runtime_records import RuntimeRecord, runtime
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, linking, plan_for, reseal
from tests.unit.test_fixture_record import _build


def _record(name: str, version: str | None = None) -> RuntimeRecord:
    record = runtime(name, version)
    assert record is not None and record.kernels is not None
    return record


RT = _record("helia-rt")
TFLM = _record("tflm")
assert RT is not None and RT.kernels is not None and TFLM is not None and TFLM.kernels is not None


@pytest.fixture(autouse=True)
def compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    for variable in ("HELIART_DIST_PATH", "HELIART_SOURCE_PATH", *_ENVIRONMENT_VARIABLES):
        monkeypatch.delenv(variable, raising=False)


def _linked(tmp_path: Path, engine: str, **commits: str):
    """A plan for *engine* and a build linking what it names, unless *commits* say otherwise."""
    if engine == "helia-aot":
        # The plan pins the installed heliaAOT release; without it there is none to compare.
        pytest.importorskip("helia_aot")
    base = atfe_build(_build(tmp_path / "run"))
    plan = plan_for(base, tmp_path / "model.tflite", engine=engine)
    assert plan.runtime is not None
    if engine == "tflm":
        assert TFLM.kernels is not None
        pinned = (TFLM.source.commit, TFLM.kernels.commit)
    elif engine == "helia-rt":
        pinned = (plan.runtime.source_commit, plan.runtime.kernels_commit)
    else:
        pinned = (plan.runtime.version, plan.runtime.kernels_commit)
    assert pinned[0] is not None and pinned[1] is not None
    linked = {"runtime": pinned[0], "kernels": pinned[1], **commits}
    return plan, linking(
        base,
        tmp_path / "run",
        engine,
        linked["runtime"],
        linked["kernels"],
        aot_commit=plan.runtime.source_commit,
    )


@pytest.mark.parametrize("engine", ["helia-rt", "tflm", "helia-aot"])
def test_a_build_linking_what_its_plan_names_verifies(tmp_path, engine):
    plan, build = _linked(tmp_path, engine)
    compiled(plan, build, Lifecycle.CARRY).verify()


@pytest.mark.parametrize(
    "engine,commits,message",
    [
        ("helia-rt", {"runtime": "c" * 40}, f"runtime commit {'c' * 40!r} differs"),
        ("helia-rt", {"kernels": "d" * 40}, f"kernel commit {'d' * 40!r} differs"),
        ("tflm", {"runtime": "a" * 40}, f"runtime commit {'a' * 40!r} differs"),
        ("tflm", {"kernels": "b" * 40}, f"kernel commit {'b' * 40!r} differs"),
        ("helia-aot", {"runtime": "0.1.0"}, "runtime version '0.1.0' differs"),
        ("helia-aot", {"kernels": "d" * 40}, f"kernel commit {'d' * 40!r} differs"),
    ],
)
def test_verify_names_the_runtime_fact_a_build_does_not_share(tmp_path, engine, commits, message):
    plan, build = _linked(tmp_path, engine, **commits)
    with pytest.raises(ConfigError, match=re.escape(message)):
        compiled(plan, build, Lifecycle.CARRY).verify()


def test_a_helia_rt_plan_refuses_another_prepared_release(tmp_path):
    other = _record("helia-rt", "2.2.0")
    assert other.kernels is not None and other.version != RT.version
    plan, build = _linked(
        tmp_path, "helia-rt", runtime=other.source.commit, kernels=other.kernels.commit
    )
    assert build.runtime_source is not None and build.runtime_source.version == "2.2.0"
    with pytest.raises(
        ConfigError, match="runtime version '2.2.0' differs from its plan's '2.2.2'"
    ):
        compiled(plan, build, Lifecycle.CARRY).verify()


def test_tflm_compares_with_the_prepared_record_not_the_profile_modules(tmp_path):
    plan, build = _linked(tmp_path, "tflm")
    assert plan.runtime is not None and plan.runtime.source_commit != TFLM.source.commit
    compiled(plan, build, Lifecycle.CARRY).verify()


@pytest.mark.parametrize(
    "engine,expected",
    [("helia-rt", ("helia-rt", RT.version)), ("tflm", ("tflm", TFLM.version)), ("helia-aot", None)],
)
def test_build_links_the_prepared_runtime_its_plan_names(tmp_path, monkeypatch, engine, expected):
    plan, build = _linked(tmp_path, engine)
    fixture = TypedFixture(
        build.model, build.inputs, tuple(FixtureOutput(t) for t in build.output_tensors)
    )
    sentinel = object()
    asked: list[tuple[str, str | None]] = []
    calls: list[dict[str, Any]] = []

    def prepared(name: str, version: str | None = None) -> SimpleNamespace:
        asked.append((name, version))
        return SimpleNamespace(runtime=sentinel)

    def fake(config, fixture, **options):
        calls.append(options)
        return reseal(build)

    monkeypatch.setattr("helia_profiler.prepared_runtimes.prepared_runtime", prepared)
    monkeypatch.setattr("helia_profiler._fixture_build.build_fixed_fixture", fake)
    hpx.build(plan, fixture, method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY))

    assert asked == ([expected] if expected else [])
    assert calls[0]["runtime"] is (sentinel if expected else None)


def test_a_helia_aot_vcs_install_compares_the_commit(tmp_path):
    plan, build = _linked(tmp_path, "helia-aot")
    assert plan.runtime is not None and build.engine_source is not None
    vcs = replace(plan, runtime=replace(plan.runtime, source_commit="a" * 40))
    other = reseal(build, engine_source=replace(build.engine_source, commit="b" * 40))
    with pytest.raises(ConfigError, match=f"runtime commit {'b' * 40!r} differs"):
        compiled(vcs, other, Lifecycle.CARRY).verify()


def test_a_plan_without_a_recorded_release_names_why_it_links_nothing(tmp_path):
    from helia_profiler.compiled_build import _prepared_runtime

    base = atfe_build(_build(tmp_path / "run"))
    plan = plan_for(base, tmp_path / "model.tflite", engine="helia-rt")
    assert plan.runtime is not None
    reason = "heliaRT selected by engine.config.dist_path"
    unrecorded = replace(
        plan, runtime=replace(plan.runtime, version=None, unavailable_reason=reason)
    )
    with pytest.raises(ConfigError, match=f"no prepared helia-rt release to link: {reason}"):
        _prepared_runtime(unrecorded)


def test_a_saved_tflm_build_verifies_after_the_default_record_changes(tmp_path, monkeypatch):
    plan, build = _linked(tmp_path, "tflm")
    record = compiled(plan, build, Lifecycle.CARRY)
    real = runtime

    def newer_default(name: str, version: str | None = None) -> RuntimeRecord | None:
        if name == "tflm" and version is None:
            return replace(TFLM, version="newer", source=replace(TFLM.source, commit="e" * 40))
        return real(name, version)

    monkeypatch.setattr("helia_profiler.runtime_records.runtime", newer_default)
    record.verify()
