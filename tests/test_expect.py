"""Expect: reference outputs compared with a run's outputs as per-tensor facts."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from helia_profiler._fixture_build import (
    FixtureIO,
    FixtureOutput,
    FixtureStatePolicy,
    FixtureTimingScope,
    TypedFixture,
)
from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.expect import (
    Expect,
    Reference,
    ReferenceOrigin,
    Tolerance,
    compare_outputs,
    host_reference,
    verify_fixture_outputs,
)
from helia_profiler.fixture_analysis import (
    FixtureTensor,
    PerAxisQuantization,
    PerTensorQuantization,
)
from helia_profiler.fixture_capture import FixtureCaptureResult
from helia_profiler.fixture_runtime import FixtureFile

TINY_CNN = Path(__file__).resolve().parents[1] / "src/helia_profiler/data/models/tiny_cnn.tflite"
INT8 = FixtureTensor("out", 3, "int8", (1, 4), PerTensorQuantization(0.5, 0))
FLOAT = FixtureTensor("out", 3, "float32", (3,), None)
FILES = ReferenceOrigin("files")


def _int8(*values: int) -> bytes:
    return np.array(values, dtype=np.int8).tobytes()


def _file(path: Path, data: bytes) -> FixtureFile:
    path.write_bytes(data)
    return FixtureFile(path, hashlib.sha256(data).hexdigest())


def test_identical_outputs_have_no_error_and_no_verdict_without_tolerance():
    data = _int8(1, 2, 3, 4)
    result = compare_outputs([INT8], Reference(FILES, (data,)), [data])
    (tensor,) = result.tensors

    assert tensor.identical and tensor.mismatched_elements == 0 and tensor.max_abs_error == 0
    assert tensor.within_tolerance is None and result.matched is None
    assert result.reference.outputs == (data,)


def test_integer_error_is_reported_in_steps_and_real_units():
    result = compare_outputs(
        [INT8],
        Reference(FILES, (_int8(1, 2, 3, 4),)),
        [_int8(1, 5, 3, -1)],
        Tolerance(max_abs_error=4),
    )
    (tensor,) = result.tensors

    assert (tensor.mismatched_elements, tensor.max_abs_error) == (2, 5.0)
    assert tensor.max_abs_error_real == 2.5
    assert tensor.within_tolerance is False and result.matched is False
    assert compare_outputs(
        [INT8], Reference(FILES, (_int8(1, 2, 3, 4),)), [_int8(1, 5, 3, -1)], Tolerance(5)
    ).matched


def test_per_axis_real_error_uses_each_channel_scale():
    tensor = FixtureTensor("out", 1, "int8", (1, 2), PerAxisQuantization(1, (1.0, 0.25), (0, 0)))
    (facts,) = compare_outputs([tensor], Reference(FILES, (_int8(0, 0),)), [_int8(1, 3)]).tensors
    assert facts.max_abs_error == 3 and facts.max_abs_error_real == 1.0


def test_a_nan_on_one_side_only_is_an_infinite_mismatch():
    ref = np.array([1.0, math.nan, math.nan], dtype=np.float32).tobytes()
    obs = np.array([1.0, math.nan, 2.0], dtype=np.float32).tobytes()
    (facts,) = compare_outputs([FLOAT], Reference(FILES, (ref,)), [obs], Tolerance(1e9)).tensors

    assert facts.mismatched_elements == 1 and math.isinf(facts.max_abs_error)
    assert facts.max_abs_error_real is None and facts.within_tolerance is False


def test_wrong_counts_or_sizes_are_refused():
    with pytest.raises(ConfigError, match="1 output tensors, 2 reference outputs"):
        compare_outputs([INT8], Reference(FILES, (b"", b"")), [b""])
    with pytest.raises(ConfigError, match="observed has 3 B, the tensor 4 B"):
        compare_outputs([INT8], Reference(FILES, (_int8(1, 2, 3, 4),)), [b"abc"])


def test_expect_arguments_are_validated():
    with pytest.raises(ValueError, match="one file per output"):
        Expect.from_files()
    with pytest.raises(ValueError, match="nonnegative"):
        Tolerance(-1)
    assert Expect.from_files("a.bin").files == (Path("a.bin"),)


def _tiny_fixture(tmp_path: Path) -> tuple[TypedFixture, FixtureTensor]:
    from helia_profiler.fixture_analysis import analyze_typed_fixture_model

    analysis = analyze_typed_fixture_model(TINY_CNN)
    (input_tensor,) = analysis.inputs
    (output_tensor,) = analysis.outputs
    data = np.arange(input_tensor.size_bytes, dtype=np.int8).tobytes()
    fixture = TypedFixture(
        model=FixtureFile(TINY_CNN, hashlib.sha256(TINY_CNN.read_bytes()).hexdigest()),
        inputs=(FixtureIO(input_tensor, _file(tmp_path / "input.bin", data)),),
        outputs=(FixtureOutput(output_tensor),),
    )
    return fixture, output_tensor


def _build(engine: EngineType = EngineType.TFLM, state: FixtureStatePolicy | None = None) -> Any:
    return SimpleNamespace(engine=engine, warmups=2, iterations=3, state_policy=state)


def _capture(outputs: tuple[FixtureFile, ...]) -> FixtureCaptureResult:
    return FixtureCaptureResult("complete", FixtureTimingScope.INVOKE_ONLY, outputs=outputs)


def test_a_host_reference_matches_a_capture_of_the_same_bytes(tmp_path):
    pytest.importorskip("ai_edge_litert.interpreter")
    fixture, output = _tiny_fixture(tmp_path)
    io = fixture.inputs[0]
    reference = host_reference(TINY_CNN.read_bytes(), [(io.tensor, io.data.read())], [output])
    capture = _capture((_file(tmp_path / "out.bin", reference.outputs[0]),))

    result = verify_fixture_outputs(
        fixture, _build(), capture, Expect.reference(tolerance=Tolerance())
    )

    assert result.matched is True and result.tensors[0].identical
    origin = result.reference.origin
    assert (origin.kind, origin.resolver, origin.invocations) == ("host-litert", "BUILTIN_REF", 1)
    assert origin.litert_version


def test_a_carried_state_reference_invokes_as_often_as_the_firmware(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake(model, inputs, outputs, *, resolver, invocations):
        calls.append(invocations)
        return Reference(ReferenceOrigin("host-litert", invocations=invocations), (bytes(4),))

    monkeypatch.setattr("helia_profiler.expect.host_reference", fake)
    fixture, _ = _tiny_fixture(tmp_path)
    capture = _capture((_file(tmp_path / "out.bin", bytes(4)),))
    verify_fixture_outputs(
        fixture, _build(state=FixtureStatePolicy.CARRY), capture, Expect.reference()
    )
    verify_fixture_outputs(fixture, _build(), capture, Expect.reference())
    assert calls == [5, 1]


def test_caller_files_are_the_reference_and_executorch_refuses_a_host_one(tmp_path):
    fixture, _ = _tiny_fixture(tmp_path)
    observed = _file(tmp_path / "out.bin", _int8(1, 2, 3, 4))
    expected = _file(tmp_path / "expected.bin", _int8(1, 2, 3, 6))
    capture = _capture((observed,))

    result = verify_fixture_outputs(
        fixture, _build(EngineType.EXECUTORCH), capture, Expect.from_files(expected)
    )
    assert result.reference.origin == FILES and result.tensors[0].max_abs_error == 2
    with pytest.raises(ConfigError, match="cannot run an ExecuTorch model"):
        verify_fixture_outputs(fixture, _build(EngineType.EXECUTORCH), capture, Expect.reference())


def test_a_capture_without_every_output_is_refused(tmp_path):
    fixture, _ = _tiny_fixture(tmp_path)
    with pytest.raises(ConfigError, match="Capture has 0 outputs"):
        verify_fixture_outputs(fixture, _build(), _capture(()), Expect.reference())
