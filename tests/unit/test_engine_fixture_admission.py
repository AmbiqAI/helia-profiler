"""Engine admission uses metadata only; no interpreter, compiler or model execution."""

from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from test_typed_fixture_analysis import typed_model

from helia_profiler import fixture as api
from helia_profiler._fixture_build import _check_typed_fixture, _state_method
from helia_profiler.errors import ConfigError
from helia_profiler.fixture_analysis import analyze_fixture_model

F32 = ("x", "float32", (1, 4), None, None)
Y32 = ("y", "float32", (1, 4), None, None)


def test_internal_symbolic_shapes_are_engine_owned(tmp_path):
    def change(model, graph):
        graph.tensors[2].shapeSignature = [1, -1]

    path = typed_model(
        tmp_path, [F32], [Y32], extra=[("internal", "float32", (1, 4), None, None)], change=change
    )
    for engine in (None, api.EngineType.HELIA_AOT, api.EngineType.TFLM, api.EngineType.HELIA_RT):
        actual = (
            api.analyze_typed_fixture_model(path)
            if engine is None
            else api.analyze_typed_fixture_model_for_engine(path, engine=engine)
        )
        assert actual.inputs[0].shape == (1, 4)
        assert actual.output_bytes == 16


@pytest.mark.parametrize("engine", [api.EngineType.HELIA_AOT, api.EngineType.TFLM])
@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda m, g: setattr(g.tensors[0], "shapeSignature", [1, -1]), "Dynamic"),
        (lambda m, g: setattr(g.tensors[1], "shape", [1, 0]), "Dynamic"),
        (lambda m, g: setattr(g, "inputs", [99]), "tensor index"),
        (lambda m, g: setattr(g.operators[0], "outputs", [99]), "tensor index"),
        (lambda m, g: setattr(g.tensors[2], "buffer", 99), "buffer index"),
        (lambda m, g: setattr(g.operators[0], "opcodeIndex", 99), "code index"),
    ],
)
def test_common_hostile_io_and_container_checks_remain(tmp_path, engine, mutation, match):
    path = typed_model(
        tmp_path, [F32], [Y32], extra=[("internal", "float32", (1, 4), None, None)], change=mutation
    )
    with pytest.raises(ValueError, match=match):
        api.analyze_typed_fixture_model_for_engine(path, engine=engine)


def test_auxiliary_subgraph_stays_in_original_model_and_is_validated(tmp_path):
    def change(model, graph):
        model.subgraphs = [graph, graph]

    path = typed_model(tmp_path, [F32], [Y32], change=change)
    before = path.read_bytes()
    actual = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    assert actual.input_bytes == 16
    assert actual.resolver.registrations == ()
    assert path.read_bytes() == before
    micro = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM)
    assert micro.subgraph_count == 2
    assert micro.resolver.registrations == ("r.AddAdd();",)


def test_aot_is_not_gated_by_micro_registration(tmp_path):
    from test_typed_fixture_analysis import s

    def change(model, graph):
        model.operatorCodes[0].builtinCode = s.BuiltinOperator.GELU
        model.operatorCodes[0].deprecatedBuiltinCode = 127

    path = typed_model(tmp_path, [F32], [Y32], change=change)
    assert (
        api.analyze_typed_fixture_model_for_engine(
            path, engine=api.EngineType.HELIA_AOT
        ).resolver.registrations
        == ()
    )
    with pytest.raises(ValueError, match="Unsupported fixture operators: GELU"):
        api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM)


def test_internal_state_carries_without_a_declared_policy(tmp_path):
    def change(model, graph):
        graph.tensors[2].isVariable = True

    path = typed_model(
        tmp_path, [F32], [Y32], extra=[("state", "float32", (1, 4), None, None)], change=change
    )
    model = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    assert model.has_internal_state
    with pytest.raises(ValueError, match="FixtureStatePolicy"):
        api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY, cast(Any, "carry"))
    implicit = api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY)
    carry = api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY, api.FixtureStatePolicy.CARRY)
    assert _state_method(model, implicit) == carry
    assert _state_method(model, carry) == carry
    assert _state_method(replace(model, has_internal_state=False), implicit) == implicit


def test_state_policy_binds_request_identity_without_changing_legacy(tmp_path):
    from helia_profiler.fixture_runtime import FixtureFile

    t = api.FixtureTensor("x", 0, "float32", (1, 4), None)
    file = FixtureFile(Path("unused"), "0" * 64)
    fixture = api.TypedFixture(
        file, (api.FixtureIO(t, file),), (api.FixtureIO(replace(t, name="y", index=1), file),)
    )
    request = api.FixtureBuildRequest(
        fixture,
        api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY),
        tmp_path,
        api.EngineType.HELIA_AOT,
        32768,
        100,
        5,
    )
    same = replace(request, method=api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY, None))
    carry = replace(
        request,
        method=api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY, api.FixtureStatePolicy.CARRY),
    )
    assert request.intent_identity == same.intent_identity
    assert request.intent_identity != carry.intent_identity


def test_stateful_build_records_carry_without_a_declared_policy(tmp_path, monkeypatch):
    import hashlib
    import json

    from helia_profiler import _fixture_build as build
    from helia_profiler.config import load_config
    from helia_profiler.fixture_runtime import FixtureFile

    def stateful(model, graph):
        graph.tensors[2].isVariable = True

    path = typed_model(
        tmp_path, [F32], [Y32], extra=[("state", "float32", (1, 4), None, None)], change=stateful
    )
    data = tmp_path / "io.bin"
    data.write_bytes(bytes(16))

    def pin(p):
        return FixtureFile(p, hashlib.sha256(p.read_bytes()).hexdigest())

    analyzed = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    assert analyzed.has_internal_state
    fixture = api.TypedFixture(
        pin(path),
        tuple(api.FixtureIO(t, pin(data)) for t in analyzed.inputs),
        tuple(api.FixtureIO(t, pin(data)) for t in analyzed.outputs),
    )
    work = tmp_path / "native"
    config = load_config(
        None,
        {
            "model": {
                "path": str(path),
                "arena_size": 32768,
                "arena_location": "sram",
                "weights_location": "mram",
            },
            "engine": {"type": "helia-aot"},
            "target": {"board": "apollo510_evb", "toolchain": "atfe", "clock": {"cpu": "lp"}},
            "work_dir": str(work),
        },
    )
    monkeypatch.setattr(build, "_refuse_overrides", lambda config: None)

    class ReachedPipeline(Exception):
        pass

    def bind_identity_then_stop(self, config):
        self._stages[0].run(None)
        raise ReachedPipeline

    monkeypatch.setattr(build.PipelineRunner, "run", bind_identity_then_stop)
    with pytest.raises(ReachedPipeline):
        api.build_fixed_fixture(
            config, fixture, method=api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY)
        )
    identity = json.loads((work / "fixed-fixture-identity.json").read_text(encoding="utf-8"))
    assert identity["state_policy"] == "carry"


def test_output_transport_and_dtype_limits_remain(tmp_path):
    path = typed_model(tmp_path, [F32], [("y", "float32", (1, 40000), None, None)])
    model = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    from helia_profiler.fixture_runtime import FixtureFile

    file = FixtureFile(Path("unused"), "0" * 64)
    fixture = api.TypedFixture(
        file,
        tuple(api.FixtureIO(t, file) for t in model.inputs),
        tuple(api.FixtureIO(t, file) for t in model.outputs),
    )
    with pytest.raises(ConfigError, match="readback budget"):
        _check_typed_fixture(fixture, model, api.EngineType.HELIA_AOT)
    mismatch = replace(
        fixture,
        outputs=(replace(fixture.outputs[0], tensor=replace(model.outputs[0], shape=(1, 4))),),
    )
    with pytest.raises(ConfigError, match="declarations differ"):
        _check_typed_fixture(mismatch, model, api.EngineType.HELIA_AOT)


def test_legacy_single_int8_build_analysis_also_selects_engine(tmp_path):
    from test_fixture_analysis import model_file, s

    path = model_file(tmp_path, op=s.BuiltinOperator.GELU)
    assert (
        analyze_fixture_model(path, _engine=api.EngineType.HELIA_AOT).resolver.registrations == ()
    )
    with pytest.raises(ValueError, match="GELU"):
        analyze_fixture_model(path)


def test_real_build_entry_selects_engine_before_pipeline(tmp_path, monkeypatch):
    import hashlib

    from test_typed_fixture_analysis import s

    from helia_profiler import _fixture_build as build
    from helia_profiler.config import load_config
    from helia_profiler.fixture_runtime import FixtureFile

    def gelu(model, graph):
        model.operatorCodes[0].builtinCode = s.BuiltinOperator.GELU
        model.operatorCodes[0].deprecatedBuiltinCode = 127

    path = typed_model(tmp_path, [F32], [Y32], change=gelu)

    def pin(p):
        return FixtureFile(p, hashlib.sha256(p.read_bytes()).hexdigest())

    data = tmp_path / "io.bin"
    data.write_bytes(bytes(16))
    analyzed = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    fixture = api.TypedFixture(
        pin(path),
        tuple(api.FixtureIO(t, pin(data)) for t in analyzed.inputs),
        tuple(api.FixtureIO(t, pin(data)) for t in analyzed.outputs),
    )
    config = load_config(
        None,
        {
            "model": {
                "path": str(path),
                "arena_size": 32768,
                "arena_location": "sram",
                "weights_location": "mram",
            },
            "engine": {"type": "helia-aot"},
            "target": {"board": "apollo510_evb", "toolchain": "atfe", "clock": {"cpu": "lp"}},
            "work_dir": str(tmp_path / "native"),
        },
    )
    # Isolate installed dependency classification; the engine pipeline must never execute.
    monkeypatch.setattr(build, "_refuse_overrides", lambda config: None)

    class ReachedPipeline(Exception):
        pass

    def stop(self, config):
        raise ReachedPipeline

    monkeypatch.setattr(build.PipelineRunner, "run", stop)
    with pytest.raises(ReachedPipeline):
        api.build_fixed_fixture(
            config,
            fixture,
            method=api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY),
            compile=False,
        )


@pytest.mark.parametrize("engine", [api.EngineType.HELIA_AOT, api.EngineType.TFLM])
def test_selected_engine_still_checks_quantized_public_io(tmp_path, engine):
    path = typed_model(tmp_path, [("x", "int16", (1, 4), None, None)], [Y32])
    with pytest.raises(ValueError, match="requires quantization"):
        api.analyze_typed_fixture_model_for_engine(path, engine=engine)


def test_resource_resolver_covers_initializer_subgraph(tmp_path):
    from test_typed_fixture_analysis import s

    def change(model, graph):
        code = s.OperatorCodeT()
        code.builtinCode = s.BuiltinOperator.VAR_HANDLE
        code.deprecatedBuiltinCode = 127
        model.operatorCodes.append(code)
        aux = s.SubGraphT()
        aux.tensors = []
        op = s.OperatorT()
        op.opcodeIndex = 1
        op.inputs = []
        op.outputs = []
        aux.operators = [op]
        aux.inputs = []
        aux.outputs = []
        model.subgraphs.append(aux)

    path = typed_model(tmp_path, [F32], [Y32], change=change)
    actual = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM)
    assert actual.subgraph_count == 2
    assert actual.resource_variable_count == 1
    assert "r.AddVarHandle();" in actual.resolver.registrations
    assert "r.AddAdd();" in actual.resolver.registrations


def test_resource_renderer_allocates_once_and_keeps_full_model(tmp_path):
    import hashlib

    from helia_profiler._fixture_build import FixtureRenderSpec
    from helia_profiler.config import load_config
    from helia_profiler.firmware.fixture import fixture_template_vars
    from helia_profiler.firmware.render import _jinja_env
    from helia_profiler.fixture_runtime import FixtureFile
    from helia_profiler.pipeline import PipelineContext

    path = typed_model(tmp_path, [F32], [Y32])
    model = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM)
    model = replace(
        model,
        has_internal_state=True,
        has_initializer=True,
        subgraph_count=2,
        resource_variable_count=20,
    )
    data = tmp_path / "io.bin"
    data.write_bytes(bytes(16))

    def pin(p):
        return FixtureFile(p, hashlib.sha256(p.read_bytes()).hexdigest())

    fixture = api.TypedFixture(
        pin(path),
        tuple(api.FixtureIO(t, pin(data)) for t in model.inputs),
        tuple(api.FixtureIO(t, pin(data)) for t in model.outputs),
    )
    config = load_config(
        None,
        {
            "model": {
                "path": str(path),
                "arena_size": 1048576,
                "arena_location": "sram",
                "weights_location": "mram",
            },
            "engine": {"type": "tflm", "backend": "cmsis_nn"},
            "profiling": {"iterations": 100, "warmup": 5},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(
        fixture,
        api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY, api.FixtureStatePolicy.CARRY),
        model,
    )
    text = _jinja_env.get_template("fixed_fixture.cc.j2").render(**fixture_template_vars(ctx, []))
    assert "model->subgraphs()->size() != 2" in text
    assert "MicroResourceVariables::Create(allocator, 20)" in text
    assert "MicroInterpreter interpreter(model, resolver, allocator, resources)" in text
    assert text.count("interpreter.AllocateTensors()") == 1
    assert "ResetAll" not in text and "interpreter.Reset" not in text
    assert text.index("interpreter.AllocateTensors()") < text.index("for (unsigned i = 0;")
    assert "graph->inputs()->Get(0) != 0" in text
    assert "graph->outputs()->Get(0) != 1" in text


@pytest.mark.parametrize("engine", [api.EngineType.HELIA_AOT, api.EngineType.TFLM])
def test_stateful_operator_carries_without_tensor_flags(tmp_path, engine):
    from test_typed_fixture_analysis import s

    def change(model, graph):
        model.operatorCodes[0].builtinCode = s.BuiltinOperator.SVDF
        model.operatorCodes[0].deprecatedBuiltinCode = s.BuiltinOperator.SVDF

    path = typed_model(tmp_path, [F32], [Y32], change=change)
    actual = api.analyze_typed_fixture_model_for_engine(path, engine=engine)
    assert actual.has_internal_state
    implicit = api.FixtureMethod(api.FixtureTimingScope.INVOKE_ONLY)
    assert _state_method(actual, implicit).state_policy is api.FixtureStatePolicy.CARRY


def test_auxiliary_float16_is_metadata_not_public_io_capability(tmp_path):
    from test_typed_fixture_analysis import _tensor, s

    def change(model, graph):
        aux = s.SubGraphT()
        aux.tensors = [_tensor("half", "float16", (1, 4), None, None)]
        aux.inputs = []
        aux.outputs = []
        aux.operators = []
        model.subgraphs.append(aux)

    path = typed_model(tmp_path, [F32], [Y32], change=change)
    actual = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM)
    assert actual.has_float16
    from helia_profiler.fixture_runtime import FixtureFile

    file = FixtureFile(Path("unused"), "0" * 64)
    fixture = api.TypedFixture(
        file,
        tuple(api.FixtureIO(t, file) for t in actual.inputs),
        tuple(api.FixtureIO(t, file) for t in actual.outputs),
    )
    assert _check_typed_fixture(fixture, actual, api.EngineType.TFLM) == (
        ("float32", api.FixtureCapability.SUPPORTED),
    )


@pytest.mark.parametrize("engine", [api.EngineType.TFLM, api.EngineType.HELIA_AOT])
def test_connected_half_storage_keeps_source_and_supported_public_io(tmp_path, engine):
    from test_typed_fixture_analysis import s

    def dequantize(model, graph):
        code = s.OperatorCodeT()
        code.builtinCode = s.BuiltinOperator.DEQUANTIZE
        code.deprecatedBuiltinCode = code.builtinCode
        model.operatorCodes.append(code)
        constant = s.BufferT()
        constant.data = bytes(8)
        model.buffers.append(constant)
        graph.tensors[2].buffer = 1
        op = s.OperatorT()
        op.opcodeIndex = 1
        op.inputs = [2]
        op.outputs = [3]
        graph.operators[0].inputs = [0, 3]
        graph.operators.insert(0, op)

    path = typed_model(
        tmp_path,
        [F32],
        [Y32],
        extra=[
            ("half", "float16", (1, 4), None, None),
            ("expanded", "float32", (1, 4), None, None),
        ],
        change=dequantize,
    )
    before = path.read_bytes()
    model = api.analyze_typed_fixture_model_for_engine(path, engine=engine)
    assert model.has_float16 and model.input_bytes == model.output_bytes == 16
    file = api.FixtureFile(Path("unused"), "0" * 64)
    fixture = api.TypedFixture(
        file,
        tuple(api.FixtureIO(t, file) for t in model.inputs),
        tuple(api.FixtureIO(t, file) for t in model.outputs),
    )
    assert _check_typed_fixture(fixture, model, engine) == (
        ("float32", api.FixtureCapability.SUPPORTED),
    )
    assert path.read_bytes() == before
    assert model.resolver.registrations == (
        ("r.AddAdd();", "r.AddDequantize();") if engine is api.EngineType.TFLM else ()
    )


def test_carry_operator_attribution_is_refused_without_state_binding(tmp_path):
    from test_fixture_operator_timing import KWS_SHA, build_for, fixture_for, profile

    from helia_profiler.fixture_operator_timing import observe_operator_timing

    fixture = fixture_for(KWS_SHA, tmp_path)
    build = replace(
        build_for(api.EngineType.HELIA_AOT, fixture), state_policy=api.FixtureStatePolicy.CARRY
    )
    actual = observe_operator_timing(build, fixture, profile("kws_aot"))
    assert actual.attribution_reason == "state_policy_not_bound_by_profile"
    assert actual.reason is None
    assert actual.operators is not None


def test_engine_selection_is_a_typed_closed_set(tmp_path):
    with pytest.raises(ValueError, match="Explicit supported fixture engine"):
        api.analyze_typed_fixture_model_for_engine(
            tmp_path / "unread", engine=cast(Any, "helia-aot")
        )


def test_inspection_reports_facts_before_engine_admission(tmp_path):
    from helia_profiler._litert_inspection import inspect_litert_model

    def change(model, graph):
        graph.tensors[2].isVariable = True
        graph.tensors[2].shapeSignature = [1, -1]
        model.subgraphs.append(graph)

    path = typed_model(
        tmp_path, [F32], [Y32], extra=[("state", "float32", (1, 4), None, None)], change=change
    )
    original = path.read_bytes()
    facts = inspect_litert_model(path).facts
    assert facts.has_internal_state and facts.has_dynamic_shapes
    assert not facts.has_dynamic_io
    assert facts.subgraph_count == 2
    assert facts.operator_names == ("ADD",)
    heliart = api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_RT)
    assert heliart.has_internal_state and heliart.subgraph_count == 2
    assert inspect_litert_model(path).facts == facts
    assert path.read_bytes() == original


def test_selected_adapter_owns_fixture_preparation(tmp_path, monkeypatch):
    from helia_profiler.engines import get_adapter

    path = typed_model(tmp_path, [F32], [Y32])
    expected = api.analyze_typed_fixture_model(path)
    adapter_type = type(get_adapter(api.EngineType.TFLM))
    calls = []

    def prepare(self, model_path, *, single_io):
        calls.append((model_path, single_io))
        return expected

    monkeypatch.setattr(adapter_type, "prepare_fixture_model", prepare)
    assert api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.TFLM) is expected
    assert calls == [(path, False)]


def test_engine_fixture_inspection_parses_once(tmp_path, monkeypatch):
    from test_typed_fixture_analysis import s

    from helia_profiler.modelcost import model_analysis

    path = typed_model(tmp_path, [F32], [Y32])
    original = s.Model.GetRootAs
    models = []

    def parse(*args, **kwargs):
        model = original(*args, **kwargs)
        models.append(model)
        return model

    original_analysis = model_analysis._analyze_parsed_model

    def analyze(model):
        assert models == [model]
        return original_analysis(model)

    monkeypatch.setattr(s.Model, "GetRootAs", parse)
    monkeypatch.setattr(model_analysis, "_analyze_parsed_model", analyze)
    api.analyze_typed_fixture_model_for_engine(path, engine=api.EngineType.HELIA_AOT)
    assert len(models) == 1


def test_executorch_fixture_refusal_precedes_litert_inspection(tmp_path):
    from helia_profiler.engines import get_adapter

    with pytest.raises(ValueError, match="ExecuTorch fixture model integration is unavailable"):
        get_adapter(api.EngineType.EXECUTORCH).prepare_fixture_model(
            tmp_path / "unread.pte", single_io=False
        )
