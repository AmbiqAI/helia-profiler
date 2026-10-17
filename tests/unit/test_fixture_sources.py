"""Kernel and runtime identity come from the build's own lock or manifest, never a default."""

from dataclasses import replace

import pytest
from test_heliart_fixture import runtime

from helia_profiler.fixture import FixtureKernelSource, FixtureRuntimeSource
from helia_profiler.fixture_sources import locked_kernel_source, prepared_runtime_sources
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


@pytest.mark.parametrize(
    "modules,expected",
    [
        (
            (CORE, NS),
            FixtureKernelSource("ns-cmsis-nn", NS.url, "v7.45.1", NS.peeled_commit),
        ),
        ((ARM, CORE), FixtureKernelSource("arm-cmsis-nn", ARM.url, None, ARM.peeled_commit)),
        ((CORE,), None),
        ((NS, ARM), None),
    ],
)
def test_lock_names_exactly_one_kernel_or_nothing(modules, expected):
    assert locked_kernel_source(modules) == expected


def test_unresolved_lock_commit_stays_unknown():
    source = locked_kernel_source((replace(NS, peeled_commit=None),))
    assert source is not None and source.commit is None


@pytest.mark.parametrize(
    "schema,kernel,stack,url,commit",
    [
        (2, "ns-cmsis-nn", "helia-rt", "https://github.com/AmbiqAI/helia-rt", "c" * 40),
        (1, "arm-cmsis-nn", "tflite-micro", "https://github.com/tensorflow/tflite-micro", "a" * 40),
    ],
)
def test_prepared_manifest_names_kernel_and_runtime(tmp_path, schema, kernel, stack, url, commit):
    verified = runtime(tmp_path, schema=schema).verify()
    declared = verified.record.providers[1]
    assert prepared_runtime_sources(verified.record) == (
        FixtureKernelSource(kernel, declared.url, None, declared.revision),
        FixtureRuntimeSource(stack, url, None, commit, verified.archive.sha256),
    )


def test_runtime_version_comes_from_the_record_built_from_that_commit(tmp_path):
    record = runtime_record("helia-rt")
    assert record is not None
    verified = runtime(tmp_path).verify()
    providers = (replace(verified.record.providers[0], revision=record.source.commit),)
    manifest = replace(verified.record, providers=providers + verified.record.providers[1:])
    assert prepared_runtime_sources(manifest)[1].version == record.version
