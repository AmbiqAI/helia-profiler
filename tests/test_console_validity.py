"""Console validity footer + the fail-on-invalid exit policy.

A broken gate does not abort the run; the footer surfaces the verdict so
an INVALID run isn't hidden behind a normal-looking table. It consumes
the RunEvaluation stored on the context, and ``output.fail_on_invalid``
turns INVALID into exit 3 -- opt-in, since automation that treats nonzero
as abort must not silently re-abort a degrade-don't-abort run.
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
from helia_profiler.results import FirmwareMeta, PmuResult, ResultIssue, ResultValidity
from tests.pipeline_context_helpers import set_profile_result


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
    set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
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


def test_missing_cycles_are_unavailable_in_console(tmp_path: Path) -> None:
    from helia_profiler.console.results import print_results
    from helia_profiler.results import LayerResult

    ctx = _ctx(tmp_path)
    set_profile_result(
        ctx,
        PmuResult(
            meta=FirmwareMeta(),
            layers=[LayerResult(0, "MISSING"), LayerResult(1, "KNOWN", cycles=500)],
        ),
    )
    console = HpxConsole(verbosity=0)
    recorder = Console(record=True, highlight=False, width=200)
    console._console = recorder
    print_results(console, ctx)
    text = recorder.export_text()
    assert "unavailable (missing layer cycles)" in text
    missing_row = next(line for line in text.splitlines() if "MISSING" in line)
    known_row = next(line for line in text.splitlines() if "KNOWN" in line)
    assert "—" in missing_row
    assert "100.0%" not in known_row


def test_degraded_run_lists_warning_codes_and_messages(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("power.gate_duration_mismatch", "warning", "Band missed."),),
    )

    text = _render(ctx)

    assert "Validity: DEGRADED (1 issue)" in text
    assert "power.gate_duration_mismatch" in text
    assert "Band missed." in text


def test_invalid_run_renders_errors_first_and_the_optin_hint(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(
            _issue("power.gate_duration_mismatch", "warning", "Band missed."),
            _issue("power.window_observer_mismatch", "error", "Clocks disagree."),
        ),
    )

    text = _render(ctx)

    assert "Validity: INVALID" in text
    # Errors render before warnings regardless of issue order.
    assert text.index("power.window_observer_mismatch") < text.index("power.gate_duration_mismatch")
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
    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(_issue("power.gate_below_minimum", "error", "Too short."),),
    )
    events = []

    class CompletedPipeline:
        def run(self, config):
            events.append("reports complete")
            return ctx

    monkeypatch.setattr(
        "helia_profiler.profiler.build_default_pipeline", lambda **kwargs: CompletedPipeline()
    )
    monkeypatch.setattr(HpxConsole, "print_results", lambda self, ctx: events.append("footer"))
    # _cmd_profile imports these lazily from their home modules -- patch
    # at the source, not on profile_cmd.
    monkeypatch.setattr("helia_profiler.config.load_config", lambda path, cli: ctx.config)

    with pytest.raises(SystemExit) as excinfo:
        profile_cmd._cmd_profile(config=None, verbose=0, fail_on_invalid=True)

    assert excinfo.value.code == 3
    assert events == ["reports complete", "footer"]


def test_invalid_without_the_policy_exits_normally(tmp_path: Path, monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=False)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.INVALID,
        issues=(_issue("power.gate_below_minimum", "error", "Too short."),),
    )
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: ctx)
    # _cmd_profile imports these lazily from their home modules -- patch
    # at the source, not on profile_cmd.
    monkeypatch.setattr("helia_profiler.config.load_config", lambda path, cli: ctx.config)

    # must not raise SystemExit
    profile_cmd._cmd_profile(config=None, verbose=0, fail_on_invalid=False)


def test_degraded_with_policy_exits_normally(tmp_path: Path, monkeypatch) -> None:
    """Exit 3 is INVALID-only: a degraded run with the policy on still
    exits 0 -- warnings are advisory."""
    import helia_profiler.cli.profile_cmd as profile_cmd

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("power.gate_duration_mismatch", "warning", "Band."),),
    )
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: ctx)
    monkeypatch.setattr("helia_profiler.config.load_config", lambda path, cli: ctx.config)

    # must not raise SystemExit
    profile_cmd._cmd_profile(config=None, verbose=0)


def test_output_applier_forwards_the_flag() -> None:
    """The REAL overrides builder, un-mocked (#208: both exit tests
    it): the flag lands in the overrides dict, and a False value leaves
    the section untouched."""
    from helia_profiler.cli.options import _build_cli_overrides

    cli = _build_cli_overrides(fail_on_invalid=True)
    assert cli["output"]["fail_on_invalid"] is True

    cli = _build_cli_overrides(fail_on_invalid=False)
    assert "output" not in cli


def test_yaml_route_sets_the_policy(tmp_path: Path) -> None:
    """The doc promises output.fail_on_invalid works from a config file."""
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text(
        "model:\n  path: test.tflite\nengine:\n  type: helia-rt\noutput:\n  fail_on_invalid: true\n"
    )

    config = load_config(yaml_path, {})

    assert config.output.fail_on_invalid is True


def test_unknown_severity_renders_instead_of_vanishing(tmp_path: Path) -> None:
    """#208: a future severity tier must not yield a verdict header
    invisible causes."""
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("future.code", "info", "A future-tier note."),),
    )

    text = _render(ctx)

    assert "future.code" in text
    assert "A future-tier note." in text


@pytest.mark.parametrize("overflow", [False, True])
def test_report_evaluation_reaches_api_result(
    tmp_path: Path, monkeypatch, capsys, overflow
) -> None:
    """The report, context and public result share one evaluation."""
    import json

    from helia_profiler.api import profile
    from helia_profiler.results import LayerResult, RunMetadata
    from helia_profiler.stages.report import GenerateReportStage

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_metadata = RunMetadata(
        hpx_version="0.1.0",
        run_id="run-1",
        timestamp="2026-08-25T00:00:00+00:00",
        config_snapshot={"engine": {"type": "helia-rt"}},
    )
    set_profile_result(
        ctx,
        PmuResult(
            meta=FirmwareMeta(),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=1000.0)],
            overflow_detected=overflow,
        ),
    )
    lock = tmp_path / "workspace" / "nsx.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    ctx.config.output.dir.mkdir(parents=True, exist_ok=True)

    class ReportPipeline:
        def run(self, config):
            assert config is ctx.config
            GenerateReportStage().run(ctx)
            return ctx

    monkeypatch.setattr(
        "helia_profiler.profiler.build_default_pipeline", lambda **kwargs: ReportPipeline()
    )
    result = profile(ctx.config)
    paths = result.report_paths
    assert result.evaluation is ctx.run_evaluation
    assert paths == ctx.report_paths
    assert capsys.readouterr() == ("", "")

    assert ctx.run_evaluation is not None
    expected = ResultValidity.INVALID if overflow else ResultValidity.VALID
    assert ctx.run_evaluation.validity is expected
    summary_path = next(p for p in paths if p.name == "summary.json")
    summary = json.loads(summary_path.read_text())
    # The stored object IS the one the summary rendered.
    assert summary["validity"] == ctx.run_evaluation.validity.value


def test_fail_on_invalid_defaults_off_behaviorally(tmp_path: Path) -> None:
    """The False default was pinned only by the config docs-drift test,
    which a coordinated doc+code flip would satisfy. The opt-in design is
    behavior; pin it as behavior."""
    config = load_config(
        None,
        {"model": {"path": "test.tflite"}, "engine": {"type": "helia-rt"}},
    )

    assert config.output.fail_on_invalid is False


def test_valid_with_issues_still_renders_the_causes(tmp_path: Path) -> None:
    """Hand-built/rehydrated evaluations must not have causes swallowed by
    the quiet-VALID early return."""
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.VALID,
        issues=(_issue("future.code", "info", "Note."),),
    )

    text = _render(ctx)

    assert "Validity: VALID" in text
    assert "future.code" in text


def test_degraded_header_counts_unknown_severities(tmp_path: Path) -> None:
    """The '(0 warnings)' artifact, fully dead: an info-only evaluation
    reports one issue, not zero warnings."""
    ctx = _ctx(tmp_path)
    ctx.run_evaluation = RunEvaluation(
        validity=ResultValidity.DEGRADED,
        issues=(_issue("future.code", "info", "Note."),),
    )

    text = _render(ctx)

    assert "Validity: DEGRADED (1 issue)" in text
    assert "future.code" in text


@pytest.mark.parametrize("validity", [ResultValidity.VALID, None])
def test_cli_noninvalid_evaluation_with_policy_returns(tmp_path, monkeypatch, validity):
    from helia_profiler.cli.profile_cmd import _cmd_profile

    ctx = _ctx(tmp_path, fail_on_invalid=True)
    ctx.run_evaluation = RunEvaluation(validity=validity) if validity is not None else None
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda *args, **kwargs: ctx)
    monkeypatch.setattr("helia_profiler.config.load_config", lambda *args: ctx.config)
    _cmd_profile()


@pytest.mark.parametrize("failure", ["error", "interrupt", "unexpected"])
@pytest.mark.parametrize("entry", ["api", "cli"])
def test_profile_failure_policy_and_logging_restoration(tmp_path, monkeypatch, failure, entry):
    import logging

    from helia_profiler.api import profile
    from helia_profiler.cli.profile_cmd import _cmd_profile
    from helia_profiler.errors import HpxError

    ctx = _ctx(tmp_path)
    error = {
        "error": HpxError("profile failed"),
        "interrupt": KeyboardInterrupt(),
        "unexpected": RuntimeError("unexpected failure"),
    }[failure]
    logger = logging.getLogger("hpx")
    handlers, level = list(logger.handlers), logger.level

    class FailingPipeline:
        def run(self, config):
            raise error

    monkeypatch.setattr(
        "helia_profiler.profiler.build_default_pipeline", lambda **kwargs: FailingPipeline()
    )
    monkeypatch.setattr("helia_profiler.config.load_config", lambda *args: ctx.config)
    expected = SystemExit if entry == "cli" and failure != "unexpected" else type(error)
    with pytest.raises(expected) as caught:
        if entry == "api":
            profile(ctx.config)
        else:
            _cmd_profile()
    if isinstance(caught.value, SystemExit):
        assert caught.value.code == (130 if failure == "interrupt" else 1)
    else:
        assert caught.value is error
    assert logger.handlers == handlers
    assert logger.level == level
