"""CompiledBuild.run: capture from a recorded build, state its identity, compare its outputs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler._fixture_record import compiled_fixture_identity, fixture_capture_request
from helia_profiler.compiled_build import CompiledBuild, RunResult
from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.expect import Expect
from helia_profiler.fixture import (
    FixtureBuild,
    FixtureCaptureRequest,
    FixtureCaptureResult,
    FixtureStatePolicy,
    FixtureTiming,
    FixtureTimingScope,
    fixture_run_identity,
)
from helia_profiler.lifecycle import Lifecycle
from tests.run_api_helpers import ATFE_OUTPUT, atfe_build, compiled, plan_for, reseal
from tests.unit.test_fixture_record import _build, _pin

_SCORE, _CACHE = b"\x01\x02", b"\x03\x04\x05\x06"


class _Guard:
    def check(self, *, require_free: bool, remaining_s: float) -> None:
        raise AssertionError("the mocked capture never consults the guard")

    def verify_target(self, **kwargs: object) -> None:
        raise AssertionError("the mocked capture never consults the guard")


@pytest.fixture
def build(tmp_path: Path) -> FixtureBuild:
    return atfe_build(_build(tmp_path / "run"))


@pytest.fixture
def record(tmp_path: Path, build: FixtureBuild, monkeypatch: pytest.MonkeyPatch) -> CompiledBuild:
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output", lambda command, *, timeout_s: ATFE_OUTPUT
    )
    plan = plan_for(build, tmp_path / "model.tflite")
    assert build.state_policy is FixtureStatePolicy.CARRY
    return compiled(plan, reseal(build, iterations=1, warmups=0), Lifecycle.SINGLE)


def _success(root: Path, score: bytes = _SCORE) -> FixtureCaptureResult:
    outputs = (_pin(root / "output.bin", score), _pin(root / "output_1.bin", _CACHE))
    return FixtureCaptureResult(
        "success",
        FixtureTimingScope.INVOKE_ONLY,
        output=outputs[0],
        status=0,
        timing=FixtureTiming(3200, 1, 0, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
        outputs=outputs,
    )


def _failure() -> FixtureCaptureResult:
    return FixtureCaptureResult(
        "failure", FixtureTimingScope.INVOKE_ONLY, status=3, error="model invoke failed"
    )


class _Device:
    """Stands in for the device capture: returns *reply* and records each call."""

    def __init__(self) -> None:
        self.reply: FixtureCaptureResult | None = None
        self.calls: list[tuple[FixtureCaptureRequest, object]] = []

    def capture(self, request: FixtureCaptureRequest, *, guard: object) -> FixtureCaptureResult:
        self.calls.append((request, guard))
        assert self.reply is not None
        return self.reply


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> _Device:
    device = _Device()
    monkeypatch.setattr("helia_profiler.fixture_capture.capture_fixture", device.capture)
    return device


def _run(
    record: CompiledBuild,
    device: _Device,
    capture: FixtureCaptureResult,
    tmp_path: Path,
    *,
    guard: _Guard | None = None,
    expect: Expect | None = None,
    expected_duration_s: float | None = None,
) -> RunResult:
    device.reply = capture
    return record.run(
        guard=guard or _Guard(),
        jlink_serial="1160002255",
        evidence_dir=tmp_path / "evidence",
        settle_seconds=30.0,
        expect=expect,
        expected_duration_s=expected_duration_s,
    )


def test_the_capture_request_comes_from_the_record_and_the_caller(record, device, tmp_path):
    guard = _Guard()
    capture = _success(tmp_path / "raw")

    result = _run(record, device, capture, tmp_path, guard=guard, expected_duration_s=2.5)

    ((request, passed_guard),) = device.calls
    assert passed_guard is guard
    assert request == fixture_capture_request(
        record.build,
        jlink_serial="1160002255",
        evidence_dir=tmp_path / "evidence",
        settle_seconds=30.0,
        expected_duration_s=2.5,
    )
    assert request.expected_duration_s == 2.5 and request.image == record.build.flat_binary
    assert result.record is record and result.capture is capture and result.succeeded


def test_the_identity_states_the_observation_and_the_record_lifecycle(record, device, tmp_path):
    capture = _success(tmp_path / "raw")

    identity = _run(record, device, capture, tmp_path).identity

    plain = fixture_run_identity(record.build, capture)
    assert identity.observation_hash is not None
    assert identity.observation_hash == plain.observation_hash
    assert plain.run.lifecycle == "carry" and identity.run.lifecycle == "single"
    assert plain.intent_hash is None and identity.intent_hash == record.plan.intent_hash
    assert identity == replace(
        plain,
        run=replace(plain.run, lifecycle="single"),
        intent_hash=record.plan.intent_hash,
    )


def test_no_expectation_gives_no_verification(record, device, tmp_path):
    result = _run(record, device, _success(tmp_path / "raw"), tmp_path)

    assert result.succeeded and result.verification is None


@pytest.mark.parametrize("expect", ["build", "files"])
def test_an_expectation_compares_a_successful_capture(record, device, tmp_path, expect):
    if expect == "build":
        reference = (
            _pin(tmp_path / "ref" / "a.bin", b"\x01\x05"),
            _pin(tmp_path / "ref" / "b.bin", _CACHE),
        )
        build = replace(record.build, outputs=reference, expected=reference[0])
        build = replace(build, build_identity=compiled_fixture_identity(build))
        record = replace(record, build=build)
        expectation = Expect.from_build()
    else:
        (tmp_path / "ref").mkdir()
        (tmp_path / "ref" / "a.bin").write_bytes(b"\x01\x05")
        (tmp_path / "ref" / "b.bin").write_bytes(_CACHE)
        expectation = Expect.from_files(tmp_path / "ref" / "a.bin", tmp_path / "ref" / "b.bin")

    result = _run(record, device, _success(tmp_path / "raw"), tmp_path, expect=expectation)

    verification = result.verification
    assert verification is not None and verification.reference.origin.kind == expect
    score, cache = verification.tensors
    assert (score.name, score.mismatched_elements, score.max_abs_error) == ("score", 1, 3.0)
    assert cache.identical


def _references(root: Path, *contents: bytes) -> Expect:
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / f"ref_{index}.bin" for index in range(len(contents))]
    for path, data in zip(paths, contents, strict=True):
        path.write_bytes(data)
    return Expect.from_files(*paths)


def test_a_failed_capture_is_returned_without_verification(record, device, tmp_path):
    capture = _failure()

    result = _run(record, device, capture, tmp_path, expect=_references(tmp_path, _SCORE, _CACHE))

    assert not result.succeeded and result.capture is capture
    assert result.verification is None
    assert result.identity.observation_hash is not None


def test_an_error_raised_by_the_capture_propagates(record, monkeypatch, tmp_path):
    def capture(request, *, guard):
        raise RuntimeError("probe vanished")

    monkeypatch.setattr("helia_profiler.fixture_capture.capture_fixture", capture)

    with pytest.raises(RuntimeError, match="probe vanished"):
        record.run(
            guard=_Guard(),
            jlink_serial="1160002255",
            evidence_dir=tmp_path / "evidence",
            settle_seconds=30.0,
        )


def test_an_unusable_expectation_is_refused_before_flashing(record, device, tmp_path):
    with pytest.raises(ConfigError, match="carries no expected outputs"):
        _run(record, device, _failure(), tmp_path, expect=Expect.from_build())
    assert device.calls == []


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        (None, "Cannot read"),
        ((_SCORE + b"\x00", _CACHE), "reference has 3 B, the tensor 2 B"),
        ((_SCORE,), "2 output tensors, 1 reference outputs"),
    ],
    ids=["missing", "wrong size", "too few"],
)
def test_a_reference_that_cannot_be_compared_costs_no_capture(
    record, device, tmp_path, contents, message
):
    expect = (
        Expect.from_files(tmp_path / "absent.bin", tmp_path / "absent_1.bin")
        if contents is None
        else _references(tmp_path / "refs", *contents)
    )
    with pytest.raises(ConfigError, match=message):
        _run(record, device, _success(tmp_path), tmp_path, expect=expect)
    assert device.calls == []


def test_counts_the_device_reports_must_be_the_builds(record, device, tmp_path):
    capture = replace(
        _success(tmp_path),
        timing=FixtureTiming(3200, 100, 5, 32768, 4, 96_000_000, FixtureTimingScope.INVOKE_ONLY),
    )
    with pytest.raises(CaptureError, match="reported 100 and 5 .* runs 1 and 0"):
        _run(record, device, capture, tmp_path)
