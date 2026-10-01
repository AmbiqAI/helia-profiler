"""window_reference_inference_us: the plan's reference wins over the profile average."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from helia_profiler.power.clean_window import window_reference_inference_us


def _ctx(*, plan: Any = None, avg_us: int | None = None, power: bool = True, pmu: bool = True):
    power_result = SimpleNamespace(metadata=SimpleNamespace(power_plan=plan)) if power else None
    pmu_result = SimpleNamespace(meta=SimpleNamespace(clean_infer_avg_us=avg_us)) if pmu else None
    return cast(Any, SimpleNamespace(power_result=power_result, pmu_result=pmu_result))


def test_plan_reference_overrides_profile_average():
    assert (
        window_reference_inference_us(_ctx(plan={"reference_inference_us": 900}, avg_us=1000))
        == 900
    )


@pytest.mark.parametrize(
    "plan",
    [None, {}, {"reference_inference_us": None}, {"reference_inference_us": 0}, "not-a-dict"],
)
def test_falls_back_to_profile_average(plan: Any):
    assert window_reference_inference_us(_ctx(plan=plan, avg_us=1000)) == 1000


def test_plan_reference_is_coerced_to_int():
    assert window_reference_inference_us(_ctx(plan={"reference_inference_us": 900.0})) == 900


def test_no_power_result_uses_profile_average():
    assert window_reference_inference_us(_ctx(power=False, avg_us=1000)) == 1000


def test_none_when_neither_source_exists():
    assert window_reference_inference_us(_ctx(power=False, pmu=False)) is None
    assert window_reference_inference_us(_ctx(plan={}, avg_us=None)) is None
