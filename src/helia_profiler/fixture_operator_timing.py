"""Bind approximate per-operator timing from a profile run to a fixed-fixture build."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass

from ._fixture_build import FixedFixture, FixtureBuild, TypedFixture
from .engines import EngineType
from .fixture_capture import FIXTURE_CPU_HZ
from .fixture_runtime import _unique_object
from .fixture_target import FIXTURE_CLOCK_PROFILE
from .results.models import DEVICE_CLOCK_TOLERANCE, ProfileResult

FIXTURE_TFLM_BACKEND = "cmsis_nn"
#: Layer-sum agreement with the uninstrumented clean window, in percent of clean.
AGREEMENT_PCT = 1.0
SHORT_AGREEMENT_PCT = 2.0
SHORT_INFERENCE_S = 0.002


@dataclass(frozen=True)
class OperatorTiming:
    """Aggregated per-operator cycles from the instrumented profile image."""

    index: int | str
    op: str
    source_index: int | None
    cycles: float
    share: float


@dataclass(frozen=True)
class FixtureOperatorTiming:
    """Per-operator timing attributed to a fixture build, or null with a reason.

    ``operators`` is set only when every identity check and the agreement rule
    pass. Cycles come from a separate PMU-instrumented image; they are
    approximate shares of the fixture's work, not its measured latency.
    ``runtime_stack`` compares recorded compiler source, runtime providers and
    engine settings: same, different or unknown. Older records can lack these
    facts. ``engine_version`` is the observed profile package version; it is
    not source identity or qualification evidence.
    """

    build_identity: str | None
    operators: tuple[OperatorTiming, ...] | None
    reason: str | None
    layer_cycles_sum: float | None = None
    clean_cycles: int | None = None
    unattributed_cycles: float | None = None
    agreement_pct: float | None = None
    tolerance_pct: float | None = None
    runtime_stack: str = "unknown"
    engine_version: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


def _identity_reason(
    build: FixtureBuild,
    fixture: FixedFixture | TypedFixture,
    profile: ProfileResult,
) -> str | None:
    if build.psram_weights is not None:
        return "psram_placement_not_bound_by_profile"
    if build.arena_location is None or build.weights_location is None:
        return "build_placement_unrecorded"
    if build.state_policy is not None:
        return "state_policy_not_bound_by_profile"
    meta = profile.metadata
    snapshot = meta.config_snapshot or {}
    model = snapshot.get("model") or {}
    probe = (snapshot.get("profiling") or {}).get("clean_window_probe")
    if build.fixture_identity != fixture.identity:
        return "fixture_mismatch"
    if meta.model is None or not meta.model.sha256:
        return "profile_model_identity_unrecorded"
    if meta.model.sha256 != fixture.model.sha256:
        return "model_mismatch"
    if meta.engine is None or meta.engine.type != build.engine.value:
        return "engine_mismatch"
    backend = (snapshot.get("engine") or {}).get("backend")
    if build.engine is EngineType.TFLM and backend != FIXTURE_TFLM_BACKEND:
        return "backend_mismatch"
    if build.engine is EngineType.HELIA_RT and backend not in (None, "helia"):
        return "backend_mismatch"
    if build.toolchain is None or meta.toolchain is None:
        return "toolchain_unrecorded"
    if (meta.toolchain.compiler, meta.toolchain.compiler_version) != (
        build.toolchain.compiler,
        build.toolchain.compiler_version,
    ):
        return "compiler_mismatch"
    if meta.platform is None or meta.platform.board != build.target.board:
        return "board_mismatch"
    if (
        meta.platform.cpu_clock_name != FIXTURE_CLOCK_PROFILE
        or profile.pmu.meta.system_clock_hz != FIXTURE_CPU_HZ
    ):
        return "clock_mismatch"
    # Absent before the on-device probe existed; 0 when it could not count.
    measured = profile.pmu.meta.measured_clock_hz
    if measured and abs(measured - FIXTURE_CPU_HZ) > DEVICE_CLOCK_TOLERANCE * FIXTURE_CPU_HZ:
        return "measured_clock_mismatch"
    if (model.get("arena_location"), model.get("weights_location")) != (
        build.arena_location.value,
        build.weights_location.value,
    ):
        return "placement_mismatch"
    if probe != "infer":
        return "clean_window_not_inference"
    return None


# These are recorded engine-input fields, not inferred defaults or opaque hashes.
_ENGINE_SETTINGS = (
    "type",
    "backend",
    "config",
    "config_path_hash",
    "resolved_backend",
    "resolved_variant",
    "resolved_version",
    "toolchain_tag",
    "cmake_vars",
)
_PROVIDER = re.compile(r"helia-rt|tflite-micro|cmsis-nn")


def _providers(record: dict) -> dict[str, tuple] | None:
    modules = record.get("modules")
    if not isinstance(modules, list):
        return None
    providers = {}
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("Invalid provider module")
        name = module.get("name")
        if not isinstance(name, str) or not _PROVIDER.search(name):
            continue
        digest = module.get("content_hash")
        if (
            name in providers
            or not isinstance(digest, dict)
            or digest.get("algorithm") != "sha256"
            or not isinstance(digest.get("value"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest["value"])
        ):
            raise ValueError("Invalid provider identity")
        providers[name] = (
            module.get("project"),
            module.get("kind"),
            module.get("url"),
            module.get("peeled_commit"),
            digest["algorithm"],
            digest["value"],
        )
    return providers or None


def _runtime_identity(build: FixtureBuild, profile: ProfileResult) -> tuple[str, str | None]:
    """Compare available facts; an absent fact never supplies equality evidence."""
    different = False
    source = asdict(build.engine_source) if build.engine_source is not None else None
    engine = profile.metadata.engine
    if source and engine and engine.version and source["version"] != engine.version:
        different = True
    if build.dependency_state is None:
        return ("different" if different else "unknown"), None
    try:
        record = json.loads(build.dependency_state.read(), object_pairs_hook=_unique_object)
        if not isinstance(record, dict):
            raise ValueError("Expected dependency state object")
        if profile.metadata.dependencies is None:
            return ("different" if different else "unknown"), None
        other = profile.metadata.dependencies.to_dict()
        inputs = record.get("workspace", {}).get("inputs", {}).get("engine", {})
        other_inputs = other.get("workspace", {}).get("inputs", {}).get("engine", {})
        if not isinstance(inputs, dict) or not isinstance(other_inputs, dict):
            raise ValueError("Expected engine inputs object")
    except (OSError, ValueError, TypeError, AttributeError):
        return "unknown", "runtime_provenance_unverified"
    for snapshot in (inputs, other_inputs):
        for key in ("config", "cmake_vars"):
            if key in snapshot and not isinstance(snapshot[key], dict):
                return "unknown", "runtime_provenance_unverified"
        for key in (
            "type",
            "backend",
            "resolved_backend",
            "resolved_variant",
            "resolved_version",
            "toolchain_tag",
        ):
            if key in snapshot and snapshot[key] is not None and not isinstance(snapshot[key], str):
                return "unknown", "runtime_provenance_unverified"
        if "source" in snapshot and snapshot["source"] is not None:
            value = snapshot["source"]
            if not isinstance(value, dict) or any(
                field in value and value[field] is not None and not isinstance(value[field], str)
                for field in ("package", "version", "commit")
            ):
                return "unknown", "runtime_provenance_unverified"
    complete = True
    if inputs.get("type") != build.engine.value or other_inputs.get("type") != build.engine.value:
        return "unknown", "runtime_provenance_unverified"
    for key in _ENGINE_SETTINGS:
        if key not in inputs or key not in other_inputs:
            complete = False
        elif inputs[key] != other_inputs[key]:
            different = True
    sources = (source, inputs.get("source"), other_inputs.get("source"))
    if build.engine is EngineType.HELIA_AOT:
        for left, right in zip(sources, sources[1:]):
            if isinstance(left, dict) and isinstance(right, dict):
                for key in ("package", "version", "commit"):
                    if left.get(key) and right.get(key) and left[key] != right[key]:
                        different = True
        if (
            not all(
                isinstance(item, dict)
                and item.get("package") == build.engine.value
                and isinstance(item.get("version"), str)
                and item["version"]
                and isinstance(item.get("commit"), str)
                and re.fullmatch(r"[0-9a-f]{40}", item["commit"])
                for item in sources
            )
            or engine is None
            or not engine.version
        ):
            complete = False
        if isinstance(sources[2], dict) and engine and engine.version:
            if sources[2].get("version") and sources[2]["version"] != engine.version:
                different = True
    else:
        # Prepared archives have ABI/build settings not recorded by an ordinary
        # profile. Matching provider revisions alone cannot bind that archive.
        complete = False
    try:
        providers, other_providers = _providers(record), _providers(other)
    except ValueError:
        return "unknown", "runtime_provenance_unverified"
    if providers is None or other_providers is None:
        complete = False
    elif providers != other_providers:
        different = True
    if build.runtime_manifest is not None:
        try:
            build.runtime_manifest.read()
        except (OSError, ValueError):
            return "unknown", "runtime_provenance_unverified"
        complete = False
    return ("different" if different else "same" if complete else "unknown"), None


def bind_operator_timing(
    build: FixtureBuild,
    fixture: FixedFixture | TypedFixture,
    profile: ProfileResult,
    *,
    allow_runtime_difference: bool = False,
) -> FixtureOperatorTiming:
    """Attribute a profile run's per-layer cycles to ``build`` when they describe it.

    Default attribution requires fully recorded matching runtime identity.
    ``allow_runtime_difference`` retains approximate observations for different
    or unknown runtimes with that explicit label; it does not waive malformed
    or hash-invalid provenance, placement, model, counter or agreement checks.
    Prepared-archive build settings are not recorded by an ordinary profile,
    so matching provider revisions alone leave its archive identity unknown.
    PSRAM builds are refused because an ordinary profile does not bind their
    active payload, external arena layout, or loader configuration.
    """
    engine = profile.metadata.engine
    runtime_stack, provenance_error = _runtime_identity(build, profile)

    def result(reason: str | None, **values) -> FixtureOperatorTiming:
        return FixtureOperatorTiming(
            build.build_identity,
            values.pop("operators", None),
            reason,
            runtime_stack=runtime_stack,
            engine_version=engine.version if engine else None,
            **values,
        )

    reason = _identity_reason(build, fixture, profile)
    if reason is not None:
        return result(reason)
    layers = profile.pmu.layers
    if not layers:
        return result("per_layer_unavailable")
    if profile.pmu.overflow_detected or any(layer.overflow for layer in layers):
        return result("counter_overflow")
    cycles: list[float] = []
    for layer in layers:
        if layer.cycles is None or not math.isfinite(layer.cycles) or layer.cycles < 0:
            return result("layer_cycles_unavailable")
        cycles.append(float(layer.cycles))
    clean = profile.pmu.meta.clean_infer_avg_cycles
    if not isinstance(clean, int) or isinstance(clean, bool) or clean <= 0:
        return result("clean_window_unavailable")
    total = sum(cycles)
    agreement = 100.0 * (total - clean) / clean
    tolerance = SHORT_AGREEMENT_PCT if clean / FIXTURE_CPU_HZ < SHORT_INFERENCE_S else AGREEMENT_PCT
    values = dict(
        layer_cycles_sum=total,
        clean_cycles=clean,
        unattributed_cycles=clean - total,
        agreement_pct=agreement,
        tolerance_pct=tolerance,
    )
    if abs(agreement) > tolerance:
        return result("layer_sum_disagrees_with_clean_window", **values)
    if provenance_error is not None:
        return result(provenance_error, **values)
    if runtime_stack != "same" and not allow_runtime_difference:
        return result(
            "runtime_stack_mismatch" if runtime_stack == "different" else "runtime_stack_unknown",
            **values,
        )
    operators = tuple(
        OperatorTiming(layer.id, layer.op, layer.source_index, value, value / total)
        for layer, value in zip(layers, cycles, strict=True)
    )
    return result(None, operators=operators, **values)
