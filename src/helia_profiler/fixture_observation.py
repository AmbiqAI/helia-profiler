"""Normalize existing fixture observations without numerical-policy decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ._fixture_build import FixtureBuild
from .fixture_capture import FixtureCaptureResult
from .fixture_metrics import FixtureFootprint, FixtureMetric
from .fixture_runtime import FixtureFile


@dataclass(frozen=True)
class FixtureMeasurements:
    schema_version: int
    build_identity: str
    latency_s: FixtureMetric
    arena_capacity_bytes: FixtureMetric
    arena_used_after_io_bytes: FixtureMetric
    arena_used_after_warmup_bytes: FixtureMetric
    arena_used_after_invoke_bytes: FixtureMetric
    footprint: FixtureFootprint
    evidence: tuple[FixtureFile, ...]


def summarize_fixture_measurements(
    build: FixtureBuild,
    capture: FixtureCaptureResult,
    footprint: FixtureFootprint,
) -> FixtureMeasurements:
    """Bind complete raw observations to a compiled receipt; no numerical acceptance."""
    if (
        not build.built
        or build.build_identity is None
        or build.binary is None
        or build.flat_binary is None
    ):
        raise ValueError("Compiled build identity required")
    if (footprint.elf.sha256, footprint.image.sha256) != (
        build.binary.sha256,
        build.flat_binary.sha256,
    ):
        raise ValueError("Footprint differs from compiled image")
    if build.link_map is None or footprint.link_map.sha256 != build.link_map.sha256:
        raise ValueError("Footprint map differs from compiled image")
    for ref in (footprint.elf, footprint.image, footprint.link_map):
        ref.read()
    identities = [ref for ref in capture.artifacts if ref.path.name == "identity.json"]
    if len(identities) != 1:
        raise ValueError("Exactly one pinned capture identity required")
    for ref in capture.artifacts:
        ref.read()
    identity = json.loads(identities[0].read())
    if (identity.get("elf_sha256"), identity.get("image_sha256")) != (
        build.binary.sha256,
        build.flat_binary.sha256,
    ):
        raise ValueError("Capture differs from compiled image")
    timing = capture.timing
    if (
        capture.state != "success"
        or capture.status != 0
        or capture.output is None
        or timing is None
        or timing.iterations != build.iterations
        or timing.warmups != build.warmups
        or timing.timing_scope != build.timing_scope
        or capture.timing_scope != build.timing_scope
        or timing.ticks <= 0
        or timing.timer_hz <= 0
        or timing.iterations <= 0
    ):
        raise ValueError("Incomplete capture or mismatched method")
    build.binary.read()
    build.flat_binary.read()
    capture.output.read()
    memory = capture.memory
    if memory is None:
        capacity = after_io = warmup = invoke = FixtureMetric(
            None, "B", "allocator terminal", "not_observed"
        )
    else:
        if not all(
            0 < v <= memory.capacity
            for v in (memory.after_io_access, memory.after_warmup, memory.after_invoke)
        ):
            raise ValueError("Invalid allocator snapshots")
        capacity = FixtureMetric(memory.capacity, "B", "firmware arena capacity; not consumption")
        after_io, warmup, invoke = tuple(
            FixtureMetric(v, "B", "current allocator snapshot; not transient peak")
            for v in (memory.after_io_access, memory.after_warmup, memory.after_invoke)
        )
    return FixtureMeasurements(
        2,
        build.build_identity,
        FixtureMetric(
            timing.ticks / timing.timer_hz / timing.iterations,
            "s/inference",
            str(build.timing_scope),
        ),
        capacity,
        after_io,
        warmup,
        invoke,
        footprint,
        capture.artifacts,
    )
