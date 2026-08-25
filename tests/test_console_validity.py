"""Console validity footer + the fail-on-invalid exit policy (#197).

Since #142/#181 a broken gate no longer aborts the run; without this footer
an INVALID run showed a normal-looking table and exited 0. The footer
consumes the single RunEvaluation ``write_report`` stores on the context
(#204 D5), and ``output.fail_on_invalid`` turns INVALID into exit 3 —
deliberately opt-in, because automation that treats nonzero as abort (the
validation runner's subprocess path) must not silently re-abort the
degrade-don't-abort runs #195 built.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from helia_profiler.config import load_config
from helia_profiler.console import HpxConsole
from helia_profiler.console.results import render_validity
from helia_profiler.evaluation import RunEvaluation
from helia_profiler.pipeline import PipelineContext
from helia_profiler.results import FirmwareMeta, PmuResult, ResultValidity
from helia_profiler.results import ResultIssue


def _ctx(tmp_path: Path, *, fail_on_invalid: bool = False) -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "output": {"dir": tmp_path, "fail_on_invalid": fail_on_invalid},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.pmu_result = PmuResult(meta=FirmwareMeta(), layers=[])
    return ctx


def _render(ctx: PipelineContext) -> str:
    hpx_console = HpxConsole(verbosity=0)
    recorder = Console(record=True, highlight=False, width=200)
    hpx_console._console = recorder
    render_validity(hpx_console, ctx)
    return recorder.export_text()


def _issue(code: str, severity: str, message: str) -> ResultIssue:
    return ResultIssue(code=code, severity=severity, message=message, context={})


def test_valid_run_renders_one_quiet_line(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(validity=ResultValidity.VALID)

    text = _render(ctx)

    assert "Validity: VALID" in text
    assert "hint" not in text


def test_degraded_run_lists_warning_codes_and_messages(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(
            _issue("power.gate_duration_mismatch", "warning", "Band missed."),
        ),
    )

    text = _render(ctx)

    assert "Validity: DEGRADED (1 warning)" in text
    assert "power.gate_duration_mismatch" in text
    assert "Band missed." in text


def test_invalid_run_renders_errors_first_and_the_optin_hint(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(
            _issue("power.gate_duration_mismatch", "warning", "Band missed."),
            _issue(
                "power.window_observer_mismatch", "error", "Clocks disagree."
            ),
        ),
    )

    text = _render(ctx)

    assert "Validity: INVALID" in text
    # Errors render before warnings regardless of issue order.
    assert text.index("power.window_observer_mismatch") < text.index(
        "power.gate_duration_mismatch"
    )
    assert "--fail-on-invalid" in text


def test_hint_is_omitted_when_the_policy_is_already_on(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(_issue("power.gate_below_minimum", "error", "Too short."),),
    )

    assert "--fail-on-invalid" not in _render(ctx)


def test_footer_falls_back_to_a_fresh_evaluation(tmp_path: Path) -> None:
    """Direct callers that never wrote a report still get a verdict."""
    ctx = _ctx(tmp_path)
    assert ctx.run_evaluation is None

    assert "Validity: VALID" in _render(ctx)


def test_fail_on_invalid_exits_3_after_the_run(tmp_path: Path, monkeypatch) -> None:
    import argparse

    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(_issue("power.gate_below_minimum", "error", "Too short."),),
    )
    monkeypatch.setattr(
        "helia_profiler.profiler.run_profile", lambda config, console: ctx
    )
    # _cmd_profile imports these lazily from their home modules -- patch
    # at the source, not on profile_cmd.
    monkeypatch.setattr(
        "helia_profiler.config.load_config", lambda path, cli: ctx.config
    )

    args = argparse.Namespace(
        config=None, verbose=0, fail_on_invalid=True
    )
    monkeypatch.setattr(
        profile_cmd, "_apply_model_engine_overrides", lambda a, c: None
    )
    for name in (
        "_apply_target_overrides",
        "_apply_pmu_overrides",
        "_apply_power_overrides",
        "_apply_output_overrides",
        "_apply_workdir_overrides",
        "_apply_build_overrides",
    ):
        monkeypatch.setattr(profile_cmd, name, lambda a, c: None)

    with pytest.raises(SystemExit) as excinfo:
        profile_cmd._cmd_profile(args)

    assert excinfo.value.code == 3


def test_invalid_without_the_policy_exits_normally(
    tmp_path: Path, monkeypatch
) -> None:
    import argparse

    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=False)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(_issue("power.gate_below_minimum", "error", "Too short."),),
    )
    monkeypatch.setattr(
        "helia_profiler.profiler.run_profile", lambda config, console: ctx
    )
    # _cmd_profile imports these lazily from their home modules -- patch
    # at the source, not on profile_cmd.
    monkeypatch.setattr(
        "helia_profiler.config.load_config", lambda path, cli: ctx.config
    )
    for name in (
        "_apply_model_engine_overrides",
        "_apply_target_overrides",
        "_apply_pmu_overrides",
        "_apply_power_overrides",
        "_apply_output_overrides",
        "_apply_workdir_overrides",
        "_apply_build_overrides",
    ):
        monkeypatch.setattr(profile_cmd, name, lambda a, c: None)

    args = argparse.Namespace(config=None, verbose=0, fail_on_invalid=False)
    profile_cmd._cmd_profile(args)  # must not raise SystemExit


def test_degraded_with_policy_exits_normally(tmp_path: Path, monkeypatch) -> None:
    """Exit 3 is INVALID-only: a degraded run with the policy on still
    exits 0 -- warnings are advisory."""
    import argparse

    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("power.gate_duration_mismatch", "warning", "Band."),),
    )
    monkeypatch.setattr(
        "helia_profiler.profiler.run_profile", lambda config, console: ctx
    )
    monkeypatch.setattr(
        "helia_profiler.config.load_config", lambda path, cli: ctx.config
    )
    for name in (
        "_apply_model_engine_overrides",
        "_apply_target_overrides",
        "_apply_pmu_overrides",
        "_apply_power_overrides",
        "_apply_output_overrides",
        "_apply_workdir_overrides",
        "_apply_build_overrides",
    ):
        monkeypatch.setattr(profile_cmd, name, lambda a, c: None)

    profile_cmd._cmd_profile(
        argparse.Namespace(config=None, verbose=0)
    )  # must not raise SystemExit


def test_output_applier_forwards_the_flag() -> None:
    """The REAL applier, un-mocked (#208 review: both exit tests bypass it):
    the flag lands in the overrides dict, and its absence -- older Namespaces
    lack the attribute -- leaves the dict untouched."""
    import argparse

    from helia_profiler.cli.profile_cmd import _apply_output_overrides

    base = argparse.Namespace(
        output_dir=None, output_format=None, no_model_explorer=False, detailed=False
    )

    cli: dict = {}
    _apply_output_overrides(
        argparse.Namespace(**vars(base), fail_on_invalid=True), cli
    )
    assert cli["output"]["fail_on_invalid"] is True

    cli = {}
    _apply_output_overrides(
        argparse.Namespace(**vars(base), fail_on_invalid=False), cli
    )
    assert "output" not in cli

    cli = {}
    _apply_output_overrides(base, cli)  # attribute absent entirely
    assert "output" not in cli


def test_yaml_route_sets_the_policy(tmp_path: Path) -> None:
    """The doc promises output.fail_on_invalid works from a config file."""
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(
        "model:\n  path: test.tflite\n"
        "engine:\n  type: helia-rt\n"
        "output:\n  fail_on_invalid: true\n"
    )

    config = load_config(yaml_path, {})

    assert config.output.fail_on_invalid is True


def test_unknown_severity_renders_instead_of_vanishing(tmp_path: Path) -> None:
    """#208 review: a future severity tier must not yield a verdict header
    with invisible causes."""
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("future.code", "info", "A future-tier note."),),
    )

    text = _render(ctx)

    assert "future.code" in text
    assert "A future-tier note." in text
