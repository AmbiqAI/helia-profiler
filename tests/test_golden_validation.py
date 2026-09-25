"""Fixed-input validation rejects corrupted data and unequal timing boundaries."""

from dataclasses import replace
import re

import flatbuffers
import numpy as np
import pytest
from ai_edge_litert import schema_py_generated as schema

from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages import PreflightStage, ResolvePlatformStage
from helia_profiler.validation.golden import GoldenData, check_golden_output, load_golden
from tests.contracts.test_firmware_render_snapshots import _render


@pytest.fixture
def pair(tmp_path):
    graph = schema.SubGraphT()
    graph.tensors = []
    for name, shape in ((b"input", [1, 3]), (b"output", [1, 2])):
        t = schema.TensorT()
        t.name, t.shape, t.type = name, shape, schema.TensorType.INT8
        t.shapeSignature = [-1, shape[1]]
        graph.tensors.append(t)
    graph.inputs, graph.outputs = [0], [1]
    model = schema.ModelT()
    model.version, model.subgraphs, model.buffers = 3, [graph], [schema.BufferT()]
    builder = flatbuffers.Builder(1024)
    builder.Finish(model.Pack(builder), file_identifier=b"TFL3")
    model_path, data_path = tmp_path / "model.tflite", tmp_path / "golden.npz"
    model_path.write_bytes(builder.Output())
    np.savez(
        data_path,
        input_0=np.array([[-128, 0, 127]], dtype=np.int8),
        output_0=np.array([[23, -41]], dtype=np.int8),
    )
    return model_path, data_path


def _record(golden):
    return (
        f"HPX_GOLDEN_OUTPUT model_sha256={golden.model_sha256} "
        f"input_sha256={golden.input_sha256} output_hex={golden.expected_bytes.hex()}"
    )


def _frame(record):
    return "--- HPX_START ---\n" + record + "\n--- HPX_END ---"


def test_checker_rejects_corrupted_expectation_and_identity(pair):
    golden = load_golden(*pair)
    assert golden.input_bytes == b"\x80\x00\x7f"
    assert check_golden_output(_frame(_record(golden)), golden) == b"\x17\xd7"
    bad = replace(golden, expected_bytes=b"\x16\xd7")
    with pytest.raises(ValueError, match="differs"):
        check_golden_output(_frame(_record(golden)), bad)
    with pytest.raises(ValueError, match="identity"):
        check_golden_output(_frame(_record(golden)), replace(golden, model_sha256="0" * 64))
    with pytest.raises(ValueError, match="identity"):
        check_golden_output(_frame(_record(golden)), replace(golden, input_sha256="0" * 64))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "truncated", "odd", "wrong"])
def test_checker_rejects_incomplete_or_wrong_output(pair, mutation):
    golden = load_golden(*pair)
    line = _record(golden)
    text = {
        "missing": "",
        "duplicate": line + "\n" + line,
        "truncated": line[:-2],
        "odd": line[:-1],
        "wrong": line[:-2] + "00",
    }[mutation]
    with pytest.raises(ValueError):
        check_golden_output(_frame(text), golden)


@pytest.mark.parametrize("kind", ["dtype", "shape", "extra", "pickle"])
def test_loader_rejects_invalid_vectors(pair, kind):
    model, data = pair
    values = dict(
        input_0=np.array([[-128, 0, 127]], dtype=np.int8),
        output_0=np.array([[23, -41]], dtype=np.int8),
    )
    if kind == "dtype":
        values["input_0"] = values["input_0"].astype(np.float32)
    elif kind == "shape":
        values["input_0"] = values["input_0"].reshape(3)
    elif kind == "extra":
        values["input_1"] = values["input_0"]
    else:
        values["input_0"] = np.array([[object()]], dtype=object)
    np.savez(data, allow_pickle=True, **values)
    with pytest.raises(ConfigError):
        load_golden(model, data)


@pytest.mark.parametrize(
    "extra",
    [
        {"engine": {"type": "helia-rt"}},
        {"power": {"enabled": True}},
        {"profiling": {"clean_window_probe": "busy_loop"}},
    ],
)
def test_preflight_rejects_unsupported_validation_before_host_tools(pair, extra):
    model, data = pair
    settings = {
        "model": {"path": str(model), "validation_data": str(data)},
        "engine": {"type": "tflm"},
        **extra,
    }
    ctx = PipelineContext(config=load_config(None, settings), work_dir=data.parent)
    with pytest.raises(ConfigError, match="validation_data"):
        PreflightStage().run(ctx)


def _assert_matched_restore(code):
    begin = code.index("uint32_t clean_stimer_t0 = hpx_stimer_ticks();")
    end = code.index("hpx_stimer_ticks() - clean_stimer_t0", begin)
    timed = code[begin:end]
    loop = timed.index("for (int iter = 0; iter < clean_iters_n; iter++)")
    restore = timed.index("memcpy(")
    assert loop < restore < timed.index("clean_count++;", loop)
    assert "hpx_validation_input, sizeof(hpx_validation_input)" in timed
    assert "memset(" not in timed
    assert "HPX_GOLDEN_OUTPUT" not in timed


@pytest.mark.parametrize("engine", ["tflm", "helia-aot"])
def test_rendered_restore_bracket_and_negative_sensitivity(engine):
    golden = GoldenData(b"\x80\x00\x7f", b"\x17\xd7", "a" * 64, "b" * 64)
    code = _render("apollo510", "rtt", engine, overrides={"golden": golden})
    _assert_matched_restore(code)
    begin = code.index("uint32_t clean_stimer_t0 = hpx_stimer_ticks();")
    tail = code[begin:]
    copy = re.search(r"memcpy\([^;]+;", tail)
    assert copy is not None
    altered = code[:begin] + copy[0] + tail[: copy.start()] + tail[copy.end() :]
    with pytest.raises((AssertionError, ValueError)):
        _assert_matched_restore(altered)
    assert "0x17, 0xd7" not in code
    assert "output_hex=" in code
    assert "golden_int8_refill_included_v1" in code


def test_capture_checks_actual_lines_and_retains_failure(pair, monkeypatch):
    from helia_profiler.capture import capture_pmu

    model, data = pair
    golden = load_golden(model, data)
    config = load_config(
        None,
        {
            "model": {"path": str(model), "validation_data": str(data)},
            "engine": {"type": "tflm"},
            "output": {"dir": str(data.parent / "capture")},
        },
    )
    ctx = PipelineContext(config=config, work_dir=data.parent)
    ResolvePlatformStage().run(ctx)
    bad_record = _record(golden)[:-2] + "00"

    class FakeTransport:
        def prepare(self, *args):
            pass

        def start(self, *args):
            pass

        def collect(self, *args):
            return ["--- HPX_START ---", bad_record, "--- HPX_END ---"]

        def close(self):
            pass

    monkeypatch.setattr("helia_profiler.capture.resolve_transport", lambda _: FakeTransport())
    with pytest.raises(CaptureError, match="Numerical validation failed"):
        capture_pmu(ctx)
    assert bad_record in (config.output.dir / "validation-capture.txt").read_text()


@pytest.mark.parametrize("case", ["before", "after", "missing_end", "two_frames"])
def test_validation_record_must_belong_to_one_complete_capture(pair, case):
    golden = load_golden(*pair)
    line = _record(golden)
    text = {
        "before": line + "\n" + _frame(""),
        "after": _frame("") + "\n" + line,
        "missing_end": "--- HPX_START ---\n" + line,
        "two_frames": _frame(line) + "\n" + _frame(""),
    }[case]
    with pytest.raises(ValueError, match="capture frame"):
        check_golden_output(text, golden)


@pytest.mark.parametrize("broken", ["flatbuffer", "zip", "empty"])
def test_corrupted_containers_report_config_error(pair, broken):
    model, data = pair
    if broken == "flatbuffer":
        model.write_bytes(b"\xff\xff\xff\x7fTFL3")
    elif broken == "zip":
        data.write_bytes(b"PK\x03\x04truncated")
    else:
        data.write_bytes(b"")
    with pytest.raises(ConfigError, match="Invalid validation_data"):
        load_golden(model, data)


def test_missing_analysis_dependency_has_actionable_error(pair, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "ai_edge_litert":
            raise ModuleNotFoundError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ConfigError, match="analysis extra") as caught:
        load_golden(*pair)
    assert caught.value.hint is not None
    assert "helia-profiler[analysis]" in caught.value.hint


def test_capture_keeps_firmware_failure_diagnosis(pair, monkeypatch):
    from helia_profiler.capture import capture_pmu

    model, data = pair
    config = load_config(
        None,
        {
            "model": {"path": str(model), "validation_data": str(data)},
            "engine": {"type": "tflm"},
            "output": {"dir": str(data.parent / "errors")},
        },
    )
    ctx = PipelineContext(config=config, work_dir=data.parent)
    ResolvePlatformStage().run(ctx)

    class FakeTransport:
        def prepare(self, *args):
            pass

        def start(self, *args):
            pass

        def collect(self, *args):
            return ["--- HPX_START ---", "HPX_ERROR=validation_invoke_failed"]

        def close(self):
            pass

    monkeypatch.setattr("helia_profiler.capture.resolve_transport", lambda _: FakeTransport())
    with pytest.raises(CaptureError, match="validation_invoke_failed"):
        capture_pmu(ctx)
    assert "validation_invoke_failed" in (config.output.dir / "validation-capture.txt").read_text()


def test_model_config_keeps_existing_positional_arena_argument(tmp_path):
    from helia_profiler.config import ModelConfig

    config = ModelConfig(tmp_path / "model.tflite", 4096)
    assert config.arena_size == 4096
    assert config.validation_data is None
