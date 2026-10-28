"""Typed identity of the kernel library and prepared runtime a fixture build linked.

Consumers read these instead of HPX's dependency state or prepared-runtime
manifest, whose layouts are internal. Both are derived from those pinned
files, so verifying a record re-derives them. A value the files do not
state stays ``None``; nothing is filled from a default.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

from ..deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from ..runtime.archive import _STACK_PROVIDERS, STACK_KERNEL_LIBRARY, STACK_RUNTIME_RECORD
from ..runtime.records import runtimes


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
    #: The runtime record built from this runtime and kernel commit, when this
    #: HPX ships one. Verification does not re-derive it: records change by release.
    version: str | None
    commit: str
    archive_sha256: str


Sources = tuple[FixtureKernelSource | None, FixtureRuntimeSource | None]


def pinned_sources(manifest: bytes | None, dependency_state: bytes | None) -> Sources:
    """Kernel and runtime identity stated by a compiled build's pinned files.

    A prepared-runtime build states both in its manifest; any other build
    states the kernel as the one CMSIS-NN module its dependency lock resolved.
    """
    if manifest is not None:
        return _manifest_sources(json.loads(manifest))
    if dependency_state is None:
        return None, None
    modules = json.loads(dependency_state).get("modules")
    found = [
        m
        for m in modules or ()
        if isinstance(m, dict) and m.get("name") in CMSIS_NN_PROVIDER_MODULES
    ]
    if len(found) != 1:
        return None, None
    module = found[0]
    return (
        FixtureKernelSource(
            module["project"], module["url"], module["requested_tag"], module["peeled_commit"]
        ),
        None,
    )


def _manifest_sources(data: dict) -> Sources:
    stack = data.get("stack", "upstream")
    roles = _STACK_PROVIDERS.get(stack)
    if roles is None or stack not in STACK_KERNEL_LIBRARY:
        return None, None
    (runtime_name, _), (kernel_name, _) = roles
    runtime = data["providers"][runtime_name]
    kernel = data["providers"][kernel_name]
    versions = {
        r.version
        for r in runtimes()
        if r.name == STACK_RUNTIME_RECORD[stack]
        and r.source.commit == runtime["revision"]
        and (r.kernels is None or r.kernels.commit == kernel["revision"])
    }
    return (
        FixtureKernelSource(STACK_KERNEL_LIBRARY[stack], kernel["url"], None, kernel["revision"]),
        FixtureRuntimeSource(
            runtime_name,
            runtime["url"],
            versions.pop() if len(versions) == 1 else None,
            runtime["revision"],
            data["archive_sha256"],
        ),
    )


def comparable(sources: Sources) -> Sources:
    """The facts verification re-derives: everything but the runtime record version."""
    kernel, runtime = sources
    return kernel, replace(runtime, version=None) if runtime is not None else None
