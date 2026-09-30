"""Bind approximate per-operator timing from a profile run to a fixed-fixture build."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .engines import EngineType
from ._fixture_build import FixedFixture, FixtureBuild, TypedFixture
from .results.models import ProfileResult
from .fixture_capture import FIXTURE_CPU_HZ
from .fixture_target import FIXTURE_CLOCK_PROFILE

#: Clock and placement every fixed fixture is built for (see ``fixture._validate``).
FIXTURE_PLACEMENT = {"arena_location": "sram", "weights_location": "mram"}
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
    ``engine_version`` is the profile run's engine version. The fixture build
    records no engine version or AOT code-generation options, so neither is
    compared here.
    """

    build_identity: str | None
    operators: tuple[OperatorTiming, ...] | None
    reason: str | None
    layer_cycles_sum: float | None = None
    clean_cycles: int | None = None
    unattributed_cycles: float | None = None
    agreement_pct: float | None = None
    tolerance_pct: float | None = None
    runtime_stack: str = "same"
    engine_version: str | None = None

    @property
    def accepted(self) -> bool:
        return self.reason is None


def _identity_reason(
    build: FixtureBuild,
    fixture: FixedFixture | TypedFixture,
    profile: ProfileResult,
    allow_runtime: bool,
) -> str | None:
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
    if any(model.get(key) != value for key, value in FIXTURE_PLACEMENT.items()):
        return "placement_mismatch"
    if build.runtime_manifest is not None and not allow_runtime:
        return "runtime_stack_mismatch"
    if probe != "infer":
        return "clean_window_not_inference"
    return None


def bind_operator_timing(
    build: FixtureBuild,
    fixture: FixedFixture | TypedFixture,
    profile: ProfileResult,
    *,
    allow_runtime_difference: bool = False,
) -> FixtureOperatorTiming:
    """Attribute a profile run's per-layer cycles to ``build`` when they describe it.

    TFLM and heliaRT fixtures link a prepared runtime archive that ``hpx profile``
    does not use, so its per-layer data describe a different runtime build;
    ``allow_runtime_difference`` accepts that and labels the record.
    """
    engine = profile.metadata.engine
    runtime_stack = (
        "profile_baseline_differs_from_fixture_runtime"
        if build.runtime_manifest is not None
        else "same"
    )

    def result(reason: str | None, **values) -> FixtureOperatorTiming:
        return FixtureOperatorTiming(
            build.build_identity,
            values.pop("operators", None),
            reason,
            runtime_stack=runtime_stack,
            engine_version=engine.version if engine else None,
            **values,
        )

    reason = _identity_reason(build, fixture, profile, allow_runtime_difference)
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
    operators = tuple(
        OperatorTiming(layer.id, layer.op, layer.source_index, value, value / total)
        for layer, value in zip(layers, cycles, strict=True)
    )
    return result(None, operators=operators, **values)
