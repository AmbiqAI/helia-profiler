"""Expect: reference outputs compared with a run's outputs as per-tensor facts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from helia_profiler._files import FixtureFile
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.expect import (
    Expect,
    ReferenceOrigin,
    ReferenceOutputs,
    Tolerance,
    compare_fixture_outputs,
    compare_outputs,
    host_reference,
)
from helia_profiler.fixtures.analysis import (
    FixtureTensor,
    PerAxisQuantization,
    PerTensorQuantization,
)
from helia_profiler.fixtures.capture import FixtureCaptureResult
from helia_profiler.fixtures.types import FixtureIO, FixtureTimingScope

TINY_CNN = Path(__file__).resolve().parents[1] / "src/helia_profiler/data/models/tiny_cnn.tflite"
TINY_INPUT = FixtureTensor("input", 0, "int8", (1, 8, 8, 1), PerTensorQuantization(0.1, 0))
TINY_OUTPUT = FixtureTensor(
    "probabilities", 13, "int8", (1, 4), PerTensorQuantization(0.00390625, -128)
)
#: tiny_cnn's output for np.arange(64) under LiteRT's reference kernels.
TINY_GOLDEN = bytes(np.array([-67, -61, -61, -67], dtype=np.int8))
INT8 = FixtureTensor("out", 3, "int8", (1, 4), PerTensorQuantization(0.5, 0))
FLOAT = FixtureTensor("out", 3, "float32", (3,), None)
FILES = ReferenceOrigin("files")


def _int8(*values: int) -> bytes:
    return np.array(values, dtype=np.int8).tobytes()


def _float(*values: float) -> bytes:
    return np.array(values, dtype=np.float32).tobytes()


def _file(path: Path, data: bytes) -> FixtureFile:
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def test_identical_outputs_have_no_error_and_no_verdict_without_tolerance():
    data = _int8(1, 2, 3, 4)
    result = compare_outputs([INT8], ReferenceOutputs(FILES, (data,)), [data])
    (tensor,) = result.tensors

    assert tensor.identical and tensor.mismatched_elements == 0 and tensor.max_abs_error == 0
    assert tensor.within_tolerance is None and result.matched is None
    assert result.reference.outputs == (data,)


def test_integer_error_is_reported_in_steps_and_real_units():
    reference = ReferenceOutputs(FILES, (_int8(1, 2, 3, 4),))
    result = compare_outputs([INT8], reference, [_int8(1, 5, 3, -1)], Tolerance(4))
    (tensor,) = result.tensors

    assert (tensor.mismatched_elements, tensor.max_abs_error) == (2, 5.0)
    assert tensor.max_abs_error_real == 2.5
    assert tensor.within_tolerance is False and result.matched is False
    assert compare_outputs([INT8], reference, [_int8(1, 5, 3, -1)], Tolerance(5)).matched


def test_per_axis_real_error_uses_each_channel_scale():
    tensor = FixtureTensor("out", 1, "int8", (1, 2), PerAxisQuantization(1, (1.0, 0.25), (0, 0)))
    (facts,) = compare_outputs(
        [tensor], ReferenceOutputs(FILES, (_int8(0, 0),)), [_int8(1, 3)]
    ).tensors
    assert facts.max_abs_error == 3 and facts.max_abs_error_real == 1.0


def test_a_nan_on_one_side_only_is_an_infinite_mismatch():
    ref = _float(1.0, math.nan, math.nan)
    obs = _float(1.0, math.nan, 2.0)
    (facts,) = compare_outputs(
        [FLOAT], ReferenceOutputs(FILES, (ref,)), [obs], Tolerance(1e9)
    ).tensors

    assert facts.mismatched_elements == 1 and math.isinf(facts.max_abs_error)
    assert facts.max_abs_error_real is None and facts.within_tolerance is False


def test_matching_infinities_are_not_mismatches():
    data = _float(math.inf, -math.inf, 1.0)
    (same,) = compare_outputs(
        [FLOAT], ReferenceOutputs(FILES, (data,)), [data], Tolerance()
    ).tensors
    assert same.mismatched_elements == 0 and same.within_tolerance is True
    (flipped,) = compare_outputs(
        [FLOAT], ReferenceOutputs(FILES, (data,)), [_float(-math.inf, -math.inf, 1.0)]
    ).tensors
    assert flipped.mismatched_elements == 1 and math.isinf(flipped.max_abs_error)


def test_wrong_counts_or_sizes_are_refused():
    with pytest.raises(ConfigError, match="1 output tensors, 2 reference outputs"):
        compare_outputs([INT8], ReferenceOutputs(FILES, (b"", b"")), [b""])
    with pytest.raises(ConfigError, match="observed has 3 B, the tensor 4 B"):
        compare_outputs([INT8], ReferenceOutputs(FILES, (_int8(1, 2, 3, 4),)), [b"abc"])


def test_expect_arguments_are_validated():
    with pytest.raises(ValueError, match="one file per output"):
        Expect.from_files()
    with pytest.raises(ValueError, match="nonnegative"):
        Tolerance(-1)
    with pytest.raises(ConfigError, match="Unknown LiteRT resolver"):
        Expect.host_litert(resolver="NOPE")  # ty: ignore[invalid-argument-type]
    assert Expect.from_files("a.bin").files == (Path("a.bin"),)


def test_a_host_reference_reproduces_the_golden_output():
    pytest.importorskip("ai_edge_litert.interpreter")
    data = np.arange(64, dtype=np.int8).tobytes()
    reference = host_reference(TINY_CNN.read_bytes(), [(TINY_INPUT, data)], [TINY_OUTPUT])

    assert reference.outputs == (TINY_GOLDEN,)
    origin = reference.origin
    assert (origin.kind, origin.resolver, origin.invocations) == ("host-litert", "BUILTIN_REF", 1)
    import importlib.metadata

    assert origin.litert_version == importlib.metadata.version("ai-edge-litert")


def test_a_host_reference_needs_every_input_in_its_role():
    pytest.importorskip("ai_edge_litert.interpreter")
    model, data = TINY_CNN.read_bytes(), bytes(64)
    with pytest.raises(ConfigError, match=r"inputs \[\] do not match the model inputs \[0\]"):
        host_reference(model, [], [TINY_OUTPUT])
    with pytest.raises(ConfigError, match="is not a model output"):
        host_reference(model, [(TINY_INPUT, data)], [TINY_INPUT])
    wrong = FixtureTensor("input", 0, "int8", (1, 64), PerTensorQuantization(0.1, 0))
    with pytest.raises(
        ConfigError, match=r"input is int8\[1, 64\]; host LiteRT has int8\[1, 8, 8, 1\]"
    ):
        host_reference(model, [(wrong, data)], [TINY_OUTPUT])


class _RecordingInterpreter:
    """Stands in for LiteRT: two inputs, two outputs, and a log of every call."""

    calls: list[str] = []

    def __init__(self, *, model_content: bytes, experimental_op_resolver_type: Any) -> None:
        type(self).calls = []

    def allocate_tensors(self) -> None:
        pass

    def get_input_details(self) -> list[dict[str, Any]]:
        return [{"index": i, "dtype": np.int8, "shape": np.array([4])} for i in (0, 1)]

    def get_output_details(self) -> list[dict[str, Any]]:
        return [{"index": i, "dtype": np.int8, "shape": np.array([4])} for i in (7, 8)]

    def set_tensor(self, index: int, value: np.ndarray) -> None:
        self.calls.append(f"set{index}")

    def invoke(self) -> None:
        self.calls.append("invoke")

    def get_tensor(self, index: int) -> np.ndarray:
        return np.full(4, index, dtype=np.int8)


def test_inputs_are_restored_before_every_invocation_and_outputs_keep_their_order(monkeypatch):
    litert = pytest.importorskip("ai_edge_litert.interpreter")
    monkeypatch.setattr(litert, "Interpreter", _RecordingInterpreter)
    monkeypatch.setattr("importlib.metadata.version", lambda name: "fake")

    def tensor(name: str, index: int) -> FixtureTensor:
        return FixtureTensor(name, index, "int8", (4,), PerTensorQuantization(1, 0))

    reference = host_reference(
        b"model",
        [(tensor("b", 1), bytes(4)), (tensor("a", 0), bytes(4))],
        [tensor("y", 8), tensor("x", 7)],
        invocations=3,
    )

    assert _RecordingInterpreter.calls == ["set1", "set0", "invoke"] * 3
    assert reference.outputs == (bytes([8] * 4), bytes([7] * 4))


def _build(tmp_path: Path, engine: EngineType = EngineType.LITERT, outputs: tuple = ()) -> Any:
    model = _file(tmp_path / "model.tflite", TINY_CNN.read_bytes())
    data = _file(tmp_path / "input.bin", np.arange(64, dtype=np.int8).tobytes())
    return SimpleNamespace(
        engine=engine,
        warmups=2,
        iterations=3,
        model=model,
        inputs=(FixtureIO(TINY_INPUT, data),),
        output_tensors=(TINY_OUTPUT,),
        outputs=outputs,
    )


def _capture(outputs: tuple[FixtureFile, ...], state: str = "success") -> FixtureCaptureResult:
    return FixtureCaptureResult(state, FixtureTimingScope.INVOKE_ONLY, outputs=outputs)


def test_a_fixture_capture_is_compared_with_a_host_reference(tmp_path):
    pytest.importorskip("ai_edge_litert.interpreter")
    observed = _int8(-67, -61, -60, -67)
    capture = _capture((_file(tmp_path / "out.bin", observed),))

    result = compare_fixture_outputs(
        _build(tmp_path), capture, Expect.host_litert(tolerance=Tolerance(1))
    )

    assert result.reference.outputs == (TINY_GOLDEN,) and result.matched is True
    assert result.reference.origin.invocations == 5
    assert (result.tensors[0].mismatched_elements, result.tensors[0].max_abs_error) == (1, 1.0)


def test_build_and_caller_references_and_executorch_refuses_a_host_one(tmp_path):
    expected = _file(tmp_path / "expected.bin", _int8(1, 2, 3, 6))
    build = _build(tmp_path, EngineType.EXECUTORCH, outputs=(expected,))
    capture = _capture((_file(tmp_path / "out.bin", _int8(1, 2, 3, 4)),))

    from_build = compare_fixture_outputs(build, capture, Expect.from_build())
    assert from_build.reference.origin == ReferenceOrigin("build")
    assert from_build.tensors[0].max_abs_error == 2
    from_files = compare_fixture_outputs(build, capture, Expect.from_files(expected.path))
    assert from_files.reference.origin == FILES and from_files.tensors == from_build.tensors
    with pytest.raises(ConfigError, match="cannot run an ExecuTorch model"):
        compare_fixture_outputs(build, capture, Expect.host_litert())
    with pytest.raises(ConfigError, match="Unknown reference source"):
        weird = Expect("weird")  # ty: ignore[invalid-argument-type]
        compare_fixture_outputs(build, capture, weird)
    with pytest.raises(ConfigError, match="carries no expected outputs"):
        compare_fixture_outputs(_build(tmp_path), capture, Expect.from_build())


def test_an_incomplete_capture_or_build_is_refused(tmp_path):
    out = _file(tmp_path / "out.bin", bytes(4))
    expect = Expect.from_files(_file(tmp_path / "ref.bin", bytes(4)).path)
    with pytest.raises(ConfigError, match="Capture success with 0 of 1 outputs"):
        compare_fixture_outputs(_build(tmp_path), _capture(()), expect)
    with pytest.raises(ConfigError, match="Capture failure with 1 of 1 outputs"):
        compare_fixture_outputs(_build(tmp_path), _capture((out,), "failure"), expect)
    legacy = _build(tmp_path)
    legacy.model = None
    with pytest.raises(ConfigError, match="does not record its measurement inputs"):
        compare_fixture_outputs(legacy, _capture((out,)), expect)
    out.path.write_bytes(bytes(5))
    with pytest.raises(ConfigError, match="Cannot read"):
        compare_fixture_outputs(_build(tmp_path), _capture((out,)), expect)
