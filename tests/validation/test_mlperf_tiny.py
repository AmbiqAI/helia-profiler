"""MLPerf Tiny hardware validation cases.

Each parametrised invocation of :func:`test_mlperf_tiny_case` drives one
full ``hpx profile`` run against real hardware and asserts the resulting
artifacts meet a minimum bar (layers > 0, cycles > 0, AOT manifest
present for AOT cases, non-zero energy for power cases).

Run with ``hpx validate`` (preferred) or::

    pytest -m hardware tests/validation/
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.validation import CaseSpec, case_validity
from helia_profiler.validation.runner import CaseResult, assert_healthy, run_case


def _skip_result(case: CaseSpec, reason: str) -> CaseResult:
    """Build a CaseResult recording a skipped (known-unsupported) case."""
    return CaseResult(
        case_id=case.case_id,
        status="skip",
        duration_s=0.0,
        engine=case.engine.value,
        model_id=case.model.id,
        board=case.board.id,
        power=case.power,
        toolchain=case.toolchain.value,
        transport=case.transport.value,
        memory=case.memory.value,
        jlink_serial=case.jlink_serial,
        error=reason,
    )


@pytest.mark.hardware
def test_mlperf_tiny_case(
    case: CaseSpec,
    repo_root: Path,
    validation_output_dir: Path,
    results_accumulator: list,
    request: pytest.FixtureRequest,
) -> None:
    """Drive one (model × engine × power × board) case through hpx profile."""
    # Skip early if the fixture file isn't present — surfaces LFS misfetch
    # cleanly instead of as a cryptic pipeline error.
    fixture = repo_root / case.model.fixture_path
    if not fixture.exists() or fixture.stat().st_size < 1024:
        reason = (
            f"fixture missing / LFS not fetched: {fixture} "
            f"(run `git lfs pull` in the helia-profiler checkout)"
        )
        results_accumulator.append(_skip_result(case, reason))
        pytest.skip(reason)

    reason = case_validity(case)
    if reason:
        results_accumulator.append(_skip_result(case, reason))
        pytest.skip(f"unsupported combination: {reason}")

    timeout = float(request.config.getoption("--mlperf-timeout"))
    result = run_case(
        case=case,
        repo_root=repo_root,
        output_root=validation_output_dir,
        timeout_s=timeout,
        verbose=request.config.get_verbosity() > 0,
    )
    results_accumulator.append(result)
    assert_healthy(result)
