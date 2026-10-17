"""Kernel and runtime identity come from the build's pinned manifest or state, never a default."""

import json
from dataclasses import asdict, replace

import pytest
from test_heliart_fixture import runtime

from helia_profiler.fixture import FixtureKernelSource, FixtureRuntimeSource
from helia_profiler.fixture_sources import comparable, pinned_sources
from helia_profiler.results.dependencies import ContentDigest, DependencyModule
from helia_profiler.runtime_records import runtime as runtime_record


def _module(name, project, *, tag=None):
    return DependencyModule(
        name=name,
        project=project,
        kind="git",
        requested_ref="e" * 40,
        requested_tag=tag,
        peeled_commit="e" * 40,
        content_hash=ContentDigest("sha256", "f" * 64),
        url=f"https://github.com/AmbiqAI/{project}.git",
        vendored_at=f"modules/{project}",
    )


NS = _module("nsx-cmsis-nn", "ns-cmsis-nn", tag="v7.45.1")
ARM = _module("arm-cmsis-nn", "arm-cmsis-nn")
CORE = _module("nsx-core", "nsx-core")


def state(*modules):
    return json.dumps({"lock": {}, "modules": [asdict(m) for m in modules]}).encode()


@pytest.mark.parametrize(
    "modules,expected",
    [
        ((CORE, NS), FixtureKernelSource("ns-cmsis-nn", NS.url, "v7.45.1", NS.peeled_commit)),
        ((ARM, CORE), FixtureKernelSource("arm-cmsis-nn", ARM.url, None, ARM.peeled_commit)),
        (
            (CORE, replace(NS, peeled_commit=None)),
            FixtureKernelSource("ns-cmsis-nn", NS.url, "v7.45.1", None),
        ),
        ((CORE,), None),
        ((NS, ARM), None),
    ],
)
def test_state_names_exactly_one_kernel_or_nothing(modules, expected):
    assert pinned_sources(None, state(*modules)) == (expected, None)


@pytest.mark.parametrize("data", [b"{}", b'{"lock": {}}'])
def test_state_without_modules_states_nothing(data):
    assert pinned_sources(None, data) == (None, None)
    assert pinned_sources(None, None) == (None, None)


@pytest.mark.parametrize(
    "schema,kernel,provider,stack,url",
    [
        (2, "ns-cmsis-nn", "ns-cmsis-nn", "helia-rt", "https://github.com/AmbiqAI/helia-rt"),
        (
            1,
            "arm-cmsis-nn",
            "cmsis-nn",
            "tflite-micro",
            "https://github.com/tensorflow/tflite-micro",
        ),
    ],
)
def test_manifest_states_kernel_and_runtime_over_any_lock_kernel(
    tmp_path, schema, kernel, provider, stack, url
):
    rt = runtime(tmp_path, schema=schema)
    declared = json.loads(rt.manifest.read())["providers"]
    assert pinned_sources(rt.manifest.read(), state(NS)) == (
        FixtureKernelSource(
            kernel, declared[provider]["url"], None, declared[provider]["revision"]
        ),
        FixtureRuntimeSource(stack, url, None, declared[stack]["revision"], rt.archive.sha256),
    )


def test_unknown_stack_states_nothing():
    assert pinned_sources(json.dumps({"stack": "helia-rt-next"}).encode(), None) == (None, None)


def _recorded(tmp_path, name, provider, kernel, schema):
    record = runtime_record(name)
    assert record is not None
    data = json.loads(runtime(tmp_path, schema=schema).manifest.read())
    data["providers"][provider]["revision"] = record.source.commit
    if record.kernels is not None:
        data["providers"][kernel]["revision"] = record.kernels.commit
    return record, data


@pytest.mark.parametrize(
    "name,provider,kernel,schema",
    [("helia-rt", "helia-rt", "ns-cmsis-nn", 2), ("tflm", "tflite-micro", "cmsis-nn", 1)],
)
def test_runtime_version_is_the_record_built_from_both_commits(
    tmp_path, name, provider, kernel, schema
):
    record, data = _recorded(tmp_path, name, provider, kernel, schema)
    stated = pinned_sources(json.dumps(data).encode(), None)[1]
    assert stated is not None and stated.version == record.version
    if record.kernels is not None:
        data["providers"][kernel]["revision"] = "0" * 40
        stated = pinned_sources(json.dumps(data).encode(), None)[1]
        assert stated is not None and stated.version is None
    assert comparable((None, stated)) == (None, replace(stated, version=None))
