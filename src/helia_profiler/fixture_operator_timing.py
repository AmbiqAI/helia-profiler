"""Observe per-operator timing and its relation to a fixed-fixture build."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from itertools import combinations

from ._fixture_build import FixedFixture, FixtureBuild, TypedFixture
from .deps.compatibility import _ENGINE_SOURCE_OVERRIDE_KEYS, _MODULE_SOURCE_OVERRIDE_KEYS
from .engines import EngineType
from .fixture_capture import FIXTURE_CPU_HZ
from .fixture_runtime import _unique_object
from .fixture_target import FIXTURE_CLOCK_PROFILE
from .results.models import DEVICE_CLOCK_TOLERANCE, ProfileResult

FIXTURE_TFLM_BACKEND = "cmsis_nn"


@dataclass(frozen=True)
class OperatorTiming:
    """Aggregated per-operator cycles from the instrumented profile image."""

    index: int | str
    op: str
    source_index: int | None
    cycles: float
    share: float


@dataclass(frozen=True)
class FixtureOperatorTimingFacts:
    """Observed sibling-image cycles, with no comparison acceptance verdict.

    ``reason`` reports unusable counters or invalid supplied provenance.
    ``attribution_reason`` records why these observations cannot be attributed
    to the supplied fixture build; valid observed cycles remain available.
    ``runtime_stack`` is the recorded same/different/unknown relation, not
    qualification. Consumers choose comparison eligibility and tolerances.
    These cycles never become the fixture image's measured latency.
    """

    build_identity: str | None
    operators: tuple[OperatorTiming, ...] | None
    reason: str | None
    attribution_reason: str | None
    runtime_stack: str
    engine_version: str | None
    cpu_hz: int | None
    layer_cycles_sum: float | None = None
    clean_cycles: int | None = None
    unattributed_cycles: float | None = None
    agreement_pct: float | None = None


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
    "extra_modules",
)


def _providers(record: dict) -> dict[str, tuple] | None:
    if "modules" not in record:
        return None
    modules = record["modules"]
    if not isinstance(modules, list):
        raise ValueError("Invalid modules collection")
    providers = {}
    for module in modules:
        if (
            not isinstance(module, dict)
            or not all(
                isinstance(module.get(key), str) and module[key]
                for key in ("name", "project", "kind")
            )
            or any(
                key not in module or (module[key] is not None and not isinstance(module[key], str))
                for key in ("url", "peeled_commit")
            )
        ):
            raise ValueError("Invalid provider module")
        name = module["name"]
        digest = module.get("content_hash")
        if name in providers or not _valid_digest(digest):
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


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, dict)
        and value.get("algorithm") == "sha256"
        and isinstance(value.get("value"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["value"]) is not None
    )


def _engine_inputs(record: dict) -> dict:
    value = record
    for key in ("workspace", "inputs", "engine"):
        value = value.get(key, {})
        if not isinstance(value, dict):
            raise ValueError("Invalid engine input snapshot")
    for key in ("config", "cmake_vars"):
        if key in value and not isinstance(value[key], dict):
            raise ValueError("Invalid engine settings")
    if "cmake_vars" in value and not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value["cmake_vars"].items()
    ):
        raise ValueError("Invalid compiler definitions")
    if "extra_modules" in value and not isinstance(value["extra_modules"], list):
        raise ValueError("Invalid extra modules")
    for entry in value.get("extra_modules", []):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry["name"]
        ):
            raise ValueError("Invalid extra module identity")
        if (
            type(entry.get("local")) is not bool
            or any(
                key not in entry or (entry[key] is not None and not isinstance(entry[key], str))
                for key in ("project", "ref", "version")
            )
            or (entry["local"] and not _valid_digest(entry.get("content_hash")))
        ):
            raise ValueError("Invalid extra module source")
    for key in (
        "type",
        "backend",
        "resolved_backend",
        "resolved_variant",
        "resolved_version",
        "toolchain_tag",
    ):
        if key in value and value[key] is not None and not isinstance(value[key], str):
            raise ValueError("Invalid engine setting")
    if value.get("config_path_hash") is not None and not _valid_digest(value["config_path_hash"]):
        raise ValueError("Invalid engine config digest")
    source = value.get("source")
    if source is not None and (
        not isinstance(source, dict)
        or any(
            key in source and source[key] is not None and not isinstance(source[key], str)
            for key in ("package", "version", "commit")
        )
    ):
        raise ValueError("Invalid engine source")
    return value


def _settings_relation(left: object, right: object) -> str:
    """Compare recorded settings without guessing an absent key's default."""
    if isinstance(left, dict) and isinstance(right, dict):
        relations = [
            _settings_relation(left[key], right[key]) for key in left.keys() & right.keys()
        ]
        if "different" in relations:
            return "different"
        if left.keys() != right.keys() or "unknown" in relations:
            return "unknown"
        return "same"
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return "different"
        relations = [_settings_relation(a, b) for a, b in zip(left, right)]
        if "different" in relations:
            return "different"
        if "unknown" in relations:
            return "unknown"
        return "same"
    return "same" if type(left) is type(right) and left == right else "different"


def _effective_config(config: dict) -> dict:
    # Source selectors request dependencies; resolved identities supply proof.
    selectors = _ENGINE_SOURCE_OVERRIDE_KEYS | _MODULE_SOURCE_OVERRIDE_KEYS
    return {key: value for key, value in config.items() if key not in selectors}


def _effective_modules(entries: list[dict]) -> list[dict]:
    return [
        {key: value for key, value in entry.items() if key not in {"ref", "version"}}
        for entry in entries
    ]


def _runtime_identity(build: FixtureBuild, profile: ProfileResult) -> tuple[str, str | None]:
    """Compare available facts; an absent fact never supplies equality evidence."""
    different = (
        profile.metadata.engine is not None and profile.metadata.engine.type != build.engine.value
    )
    source = asdict(build.engine_source) if build.engine_source is not None else None
    engine = profile.metadata.engine
    if source and engine and engine.version and source["version"] != engine.version:
        different = True
    try:
        # Validate every provided pin before taking any missing-evidence path.
        if build.runtime_manifest is not None:
            build.runtime_manifest.read()
        record = (
            {}
            if build.dependency_state is None
            else json.loads(build.dependency_state.read(), object_pairs_hook=_unique_object)
        )
        if not isinstance(record, dict):
            raise ValueError("Expected dependency state object")
        other = (
            {} if profile.metadata.dependencies is None else profile.metadata.dependencies.to_dict()
        )
        inputs, other_inputs = _engine_inputs(record), _engine_inputs(other)
        providers, other_providers = _providers(record), _providers(other)
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return "unknown", "runtime_provenance_unverified"
    complete = True
    for snapshot, expected_engine in (
        (inputs, build.engine.value),
        (other_inputs, engine.type if engine else None),
    ):
        if (
            "type" in snapshot
            and expected_engine is not None
            and snapshot["type"] != expected_engine
        ):
            return "unknown", "runtime_provenance_unverified"
    for key in _ENGINE_SETTINGS:
        if key not in inputs or key not in other_inputs:
            complete = False
        elif key == "config":
            relation = _settings_relation(
                _effective_config(inputs[key]), _effective_config(other_inputs[key])
            )
            different |= relation == "different"
            complete &= relation != "unknown"
        elif key == "extra_modules":
            if _effective_modules(inputs[key]) != _effective_modules(other_inputs[key]):
                different = True
        elif key == "config_path_hash":
            if inputs[key] != other_inputs[key]:
                # Differing config-file bytes do not establish differing parsed
                # settings; the normalized file contents are not recorded here.
                complete = False
        elif key == "cmake_vars":
            left, right = dict(inputs[key]), dict(other_inputs[key])
            # Generated attribute-header paths are workspace locations, not
            # effective compiler settings. No content binding is recorded for
            # these individual paths, so differing locations remain unknown.
            for name in set(left) | set(right):
                if name.endswith("_ATTRIBUTES_HEADER"):
                    if left.get(name) != right.get(name):
                        complete = False
                    left.pop(name, None)
                    right.pop(name, None)
            if left != right:
                different = True
        elif inputs[key] != other_inputs[key]:
            different = True
    sources = (source, inputs.get("source"), other_inputs.get("source"))
    if build.engine is EngineType.HELIA_AOT:
        for left, right in combinations(sources, 2):
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
    if providers is None or other_providers is None:
        complete = False
    elif providers != other_providers:
        different = True
    if build.runtime_manifest is not None:
        complete = False
    return ("different" if different else "same" if complete else "unknown"), None


def observe_operator_timing(
    build: FixtureBuild,
    fixture: FixedFixture | TypedFixture,
    profile: ProfileResult,
) -> FixtureOperatorTimingFacts:
    """Read measured cycles and provenance without benchmark policy.

    Valid observations remain available when runtime identity is unknown or
    different, attribution is unavailable, or the layer sum differs from the
    clean window. Invalid supplied provenance and unusable counters refuse.
    No firmware, provider or device operation is performed.
    """
    engine = profile.metadata.engine
    try:
        runtime_stack, provenance_error = _runtime_identity(build, profile)
    except RecursionError:
        runtime_stack, provenance_error = "unknown", "runtime_provenance_unverified"

    attribution = _identity_reason(build, fixture, profile)

    def result(reason: str | None, **values) -> FixtureOperatorTimingFacts:
        return FixtureOperatorTimingFacts(
            build.build_identity,
            values.pop("operators", None),
            reason,
            attribution,
            runtime_stack=runtime_stack,
            engine_version=engine.version if engine else None,
            cpu_hz=profile.pmu.meta.system_clock_hz,
            **values,
        )

    snapshot = profile.metadata.config_snapshot or {}
    if (snapshot.get("profiling") or {}).get("clean_window_probe") != "infer":
        return result("clean_window_not_inference")
    layers = profile.pmu.layers
    if not layers:
        return result("per_layer_unavailable")
    if profile.pmu.overflow_detected or any(layer.overflow for layer in layers):
        return result("counter_overflow")
    cycles: list[float] = []
    for layer in layers:
        if isinstance(layer.cycles, bool) or not isinstance(layer.cycles, (int, float)):
            return result("layer_cycles_unavailable")
        try:
            value = float(layer.cycles)
        except (OverflowError, TypeError, ValueError):
            return result("layer_cycles_unavailable")
        if not math.isfinite(value) or value < 0:
            return result("layer_cycles_unavailable")
        cycles.append(value)
    clean = profile.pmu.meta.clean_infer_avg_cycles
    if not isinstance(clean, int) or isinstance(clean, bool) or clean <= 0:
        return result("clean_window_unavailable")
    try:
        finite_clean = math.isfinite(float(clean))
    except OverflowError:
        finite_clean = False
    if not finite_clean:
        return result("clean_window_unavailable")
    total = sum(cycles)
    if not math.isfinite(total):
        return result("layer_cycles_unavailable")
    agreement = 100.0 * (total - clean) / clean
    if not math.isfinite(agreement):
        return result("layer_cycles_unavailable")
    values = dict(
        layer_cycles_sum=total,
        clean_cycles=clean,
        unattributed_cycles=clean - total,
        agreement_pct=agreement,
    )
    if provenance_error is not None:
        return result(provenance_error, **values)
    operators = tuple(
        OperatorTiming(
            layer.id, layer.op, layer.source_index, value, value / total if total else 0.0
        )
        for layer, value in zip(layers, cycles, strict=True)
    )
    return result(None, operators=operators, **values)
