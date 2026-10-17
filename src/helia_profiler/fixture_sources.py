"""Typed identity of the kernel library and prepared runtime a fixture build linked.

Consumers read these instead of HPX's dependency lock or prepared-runtime
manifest, whose layouts are internal. A value HPX cannot observe stays
``None``; nothing is filled from a default.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .engines.cmsis_nn import (
    ARM_CMSIS_NN_MODULE,
    ARM_CMSIS_NN_PROJECT,
    CMSIS_NN_MODULE,
    CMSIS_NN_PROJECT,
)
from .fixture_runtime import RuntimeManifest
from .results.dependencies import DependencyModule
from .runtime_records import runtimes


@dataclass(frozen=True)
class FixtureKernelSource:
    """The kernel library a build linked: ``ns-cmsis-nn`` or ``arm-cmsis-nn``."""

    name: str
    url: str | None
    #: The release tag the dependency lock resolved, when it named one.
    version: str | None
    commit: str | None


@dataclass(frozen=True)
class FixtureRuntimeSource:
    """The prepared runtime archive a build linked: ``helia-rt`` or ``tflite-micro``."""

    name: str
    url: str
    #: The runtime record whose source is this commit, when HPX has one.
    version: str | None
    commit: str
    archive_sha256: str


_LOCKED_KERNELS = {CMSIS_NN_MODULE: CMSIS_NN_PROJECT, ARM_CMSIS_NN_MODULE: ARM_CMSIS_NN_PROJECT}
#: Kernel name of each prepared-runtime manifest's kernel provider.
_PROVIDER_KERNELS = {"ns-cmsis-nn": CMSIS_NN_PROJECT, "cmsis-nn": ARM_CMSIS_NN_PROJECT}
#: Runtime record name of each prepared-runtime manifest's runtime provider.
_PROVIDER_RECORDS = {"helia-rt": "helia-rt", "tflite-micro": "tflm"}


def locked_kernel_source(modules: Iterable[DependencyModule]) -> FixtureKernelSource | None:
    """The one kernel module a dependency lock resolved, or ``None`` when not exactly one."""
    found = [module for module in modules if module.name in _LOCKED_KERNELS]
    if len(found) != 1:
        return None
    module = found[0]
    return FixtureKernelSource(
        _LOCKED_KERNELS[module.name], module.url, module.requested_tag, module.peeled_commit
    )


def prepared_runtime_sources(
    manifest: RuntimeManifest,
) -> tuple[FixtureKernelSource, FixtureRuntimeSource]:
    """Kernel and runtime identity declared by a verified prepared-runtime manifest."""
    runtime, kernel = manifest.providers
    record = _PROVIDER_RECORDS[runtime.name]
    versions = {
        r.version for r in runtimes() if r.name == record and r.source.commit == runtime.revision
    }
    return (
        FixtureKernelSource(_PROVIDER_KERNELS[kernel.name], kernel.url, None, kernel.revision),
        FixtureRuntimeSource(
            runtime.name,
            runtime.url,
            versions.pop() if len(versions) == 1 else None,
            runtime.revision,
            manifest.archive_sha256,
        ),
    )
