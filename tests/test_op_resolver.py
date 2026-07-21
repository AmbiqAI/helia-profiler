from __future__ import annotations

from helia_profiler.engines import EngineType
from helia_profiler.firmware.op_resolver import build_resolver_plan
from helia_profiler.evaluation import LayerOps, ModelAnalysis


def _analysis(*ops: str) -> ModelAnalysis:
    return ModelAnalysis(
        layers=[LayerOps(id=index, op=op) for index, op in enumerate(ops)],
        total_macs=0,
        total_ops=0,
        num_parameters=0,
    )


def test_auto_mode_selects_only_needed_rt_registrations():
    plan = build_resolver_plan(
        engine_type=EngineType.HELIA_RT,
        engine_config={"resolver_ops": "auto"},
        model_analysis=_analysis("CONV_2D", "SOFTMAX", "CONV_2D"),
    )

    assert plan.mode == "auto"
    assert plan.registrations == (
        "r.AddConv2D();",
        "r.AddDequantize();",
        "r.AddQuantize();",
        "r.AddSoftmax();",
    )
    assert plan.max_ops == 4


def test_rt_defaults_to_auto_when_unset():
    plan = build_resolver_plan(
        engine_type=EngineType.HELIA_RT,
        engine_config={},
        model_analysis=_analysis("CONV_2D", "SOFTMAX"),
    )

    assert plan.mode == "auto"
    assert plan.registrations == (
        "r.AddConv2D();",
        "r.AddDequantize();",
        "r.AddQuantize();",
        "r.AddSoftmax();",
    )


def test_rt_auto_mode_includes_quantize_for_heliart_prepare():
    plan = build_resolver_plan(
        engine_type=EngineType.HELIA_RT,
        engine_config={"resolver_ops": "auto"},
        model_analysis=_analysis("CONV_2D", "SOFTMAX"),
    )

    assert "r.AddQuantize();" in plan.registrations
    assert "r.AddDequantize();" in plan.registrations


def test_auto_mode_selects_resource_variable_ops_when_needed():
    plan = build_resolver_plan(
        engine_type=EngineType.HELIA_RT,
        engine_config={},
        model_analysis=_analysis(
            "VAR_HANDLE",
            "ASSIGN_VARIABLE",
            "READ_VARIABLE",
            "CALL_ONCE",
        ),
    )

    assert plan.mode == "auto"
    assert plan.registrations == (
        "r.AddAssignVariable();",
        "r.AddCallOnce();",
        "r.AddDequantize();",
        "r.AddQuantize();",
        "r.AddReadVariable();",
        "r.AddVarHandle();",
    )


def test_auto_mode_falls_back_without_analysis():
    plan = build_resolver_plan(
        engine_type=EngineType.HELIA_RT,
        engine_config={"resolver_ops": "auto"},
        model_analysis=None,
    )

    assert plan.mode == "all"
    assert "r.AddConv2D();" in plan.registrations


def test_non_rt_engines_ignore_auto_mode():
    plan = build_resolver_plan(
        engine_type=EngineType.TFLM,
        engine_config={"resolver_ops": "auto"},
        model_analysis=_analysis("CONV_2D"),
    )

    assert plan.mode == "all"
    assert len(plan.registrations) > 1
