from __future__ import annotations

import ast
import logging
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from helia_profiler.console import HpxConsole
from helia_profiler.console.lifecycle import profile_presentation
from helia_profiler.errors import CaptureError, HpxError
from helia_profiler.pipeline import PipelineRunner
from helia_profiler.profiler import run_profile
from tests.test_pipeline import ProgressStage, SkipStage, _make_config


def test_run_profile_is_presentation_neutral_by_default() -> None:
    config = Mock()
    context = Mock()
    pipeline = Mock()
    pipeline.run.return_value = context
    logger = logging.getLogger("hpx")
    before = (list(logger.handlers), logger.level)
    with patch("helia_profiler.profiler.build_default_pipeline", return_value=pipeline) as build:
        assert run_profile(config) is context
    build.assert_called_once_with(observer=None, progress_sink=None)
    assert (logger.handlers, logger.level) == before


@pytest.mark.parametrize("explicit_progress", [False, True])
def test_presentation_order_and_workspace_release(tmp_path, monkeypatch, explicit_progress):
    events = []
    held = False

    @contextmanager
    def mutex(_path):
        nonlocal held
        held = True
        try:
            yield
        finally:
            held = False
            events.append("release")

    monkeypatch.setattr("helia_profiler.pipeline.file_mutex", mutex)
    console = HpxConsole()
    monkeypatch.setattr(console, "print_banner", lambda: events.append("banner"))
    monkeypatch.setattr(
        console, "stage_start", lambda name, index, total: events.append((name, index, total))
    )
    monkeypatch.setattr(console, "stage_skip", lambda name: events.append("skip"))
    monkeypatch.setattr(console, "stage_done", lambda name: events.append("stage_done"))
    monkeypatch.setattr(
        console, "progress_update", lambda update: events.append("console_progress")
    )
    monkeypatch.setattr(console, "pipeline_done", lambda: events.append("pipeline_done"))
    monkeypatch.setattr(console, "_stop_spinner", lambda: events.append("cleanup"))

    def completed(ctx):
        assert not held
        events.append("profile_done")

    monkeypatch.setattr(console, "profile_done", completed)
    runner = PipelineRunner(
        [SkipStage(), ProgressStage("capture")],
        observer=console,
        progress_sink=(lambda update: events.append("explicit_progress"))
        if explicit_progress
        else None,
    )
    with patch("helia_profiler.profiler.build_default_pipeline", return_value=runner):
        with profile_presentation(console):
            run_profile(_make_config(tmp_path), observer=console)
    assert events == [
        "banner",
        "skip",
        ("capture", 2, 2),
        "explicit_progress" if explicit_progress else "console_progress",
        "stage_done",
        "pipeline_done",
        "release",
        "profile_done",
        "cleanup",
    ]


@pytest.mark.parametrize("existing_handler", [False, True])
@pytest.mark.parametrize(
    "failure_site",
    [
        "banner",
        "should_skip",
        "stage_skip",
        "stage_start",
        "stage_done",
        "progress_update",
        "pipeline_done",
        "profile_done",
        "typed",
        "unexpected",
        "interrupt",
        "cleanup",
        "success",
    ],
)
def test_real_runner_lifetime(tmp_path, monkeypatch, existing_handler, failure_site):
    console = HpxConsole(1)
    logger = logging.getLogger("hpx")
    monkeypatch.setattr(logger, "handlers", [logging.NullHandler()] if existing_handler else [])
    monkeypatch.setattr(logger, "level", logging.ERROR)
    before = (list(logger.handlers), logger.level)
    events = []
    error = (
        CaptureError("failure")
        if failure_site == "typed"
        else (KeyboardInterrupt() if failure_site == "interrupt" else RuntimeError("failure"))
    )

    def fail(*args):
        raise error

    for method in (
        "print_banner",
        "stage_start",
        "stage_skip",
        "stage_done",
        "progress_update",
        "pipeline_done",
        "profile_done",
    ):

        def record(*args, name=method):
            events.append(name)

        monkeypatch.setattr(console, method, record)
    if failure_site in (
        "banner",
        "stage_start",
        "stage_skip",
        "stage_done",
        "progress_update",
        "pipeline_done",
        "profile_done",
    ):
        monkeypatch.setattr(
            console, "print_banner" if failure_site == "banner" else failure_site, fail
        )

    def cleanup():
        events.append("cleanup")
        if failure_site != "success":
            raise ValueError("cleanup failure")

    monkeypatch.setattr(console, "_stop_spinner", cleanup)
    stage = SkipStage() if failure_site == "stage_skip" else ProgressStage()
    if failure_site == "should_skip":
        monkeypatch.setattr(stage, "should_skip", fail)
    if failure_site in ("typed", "unexpected", "interrupt"):
        monkeypatch.setattr(stage, "run", fail)
    runner = PipelineRunner([stage], observer=console)
    with patch("helia_profiler.profiler.build_default_pipeline", return_value=runner):
        if failure_site == "success":
            with profile_presentation(console):
                run_profile(_make_config(tmp_path), observer=console)
        else:
            with pytest.raises(BaseException) as caught:
                with profile_presentation(console):
                    run_profile(_make_config(tmp_path), observer=console)
            if failure_site == "cleanup":
                assert isinstance(caught.value, ValueError)
            elif failure_site in ("unexpected", "progress_update"):
                assert isinstance(caught.value, HpxError)
                assert caught.value.__cause__ is error
            else:
                assert caught.value is error
    assert events[-1] == "cleanup"
    assert (logger.handlers, logger.level) == before
    if failure_site not in ("success", "cleanup", "profile_done"):
        assert "profile_done" not in events
    if failure_site not in ("success", "cleanup", "profile_done", "pipeline_done"):
        assert "pipeline_done" not in events


def test_profile_completion_renders_only_captured_results():
    console = HpxConsole()
    console.print_results = Mock()
    empty = Mock(pmu_result=None)
    captured = Mock(pmu_result=Mock())
    console.profile_done(empty)
    console.profile_done(captured)
    console.print_results.assert_called_once_with(captured)


@pytest.mark.parametrize("module", ["api", "pipeline", "profiler"])
def test_orchestration_has_no_presentation_imports(module):
    path = Path(__file__).parents[1] / "src" / "helia_profiler" / f"{module}.py"
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            imports = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imports = [node.module or ""]
            if not node.module:
                imports.extend(alias.name for alias in node.names)
        else:
            continue
        assert not any(
            "console" in name.split(".") or "rich" in name.split(".") for name in imports
        )


def test_shared_execution_renders_after_release_with_real_console(tmp_path, monkeypatch):
    from helia_profiler.api import _execute_profile
    from helia_profiler.results import FirmwareMeta, PmuResult
    from tests.pipeline_context_helpers import set_profile_result

    events = []
    console = HpxConsole()
    spinner = Mock()
    monkeypatch.setattr(console.status_console, "status", lambda *args, **kwargs: spinner)

    @contextmanager
    def mutex(_path):
        events.append("acquire")
        yield
        events.append("release")

    monkeypatch.setattr("helia_profiler.pipeline.file_mutex", mutex)
    pmu = PmuResult(meta=FirmwareMeta(), layers=[])

    class Capture(ProgressStage):
        def run(self, ctx):
            super().run(ctx)
            set_profile_result(ctx, pmu)

    def render(ctx):
        assert events == ["acquire", "release"]
        assert ctx.pmu_result is pmu
        events.append("render")

    monkeypatch.setattr(console, "print_results", render)
    runner = PipelineRunner([Capture()], observer=console)
    with patch("helia_profiler.profiler.build_default_pipeline", return_value=runner):
        with profile_presentation(console):
            result = _execute_profile(_make_config(tmp_path), observer=console)
    assert result.pmu is pmu
    assert events == ["acquire", "release", "render"]
    spinner.start.assert_called_once()
    spinner.stop.assert_called_once()
    assert console._spinner is None


def test_explicit_progress_failure_keeps_stage_wrapping_and_cleans_spinner(tmp_path, monkeypatch):
    console = HpxConsole()
    spinner = Mock()
    monkeypatch.setattr(console.status_console, "status", lambda *args, **kwargs: spinner)
    render = Mock()
    monkeypatch.setattr(console, "print_results", render)
    error = RuntimeError("callback failure")

    def progress(_update):
        raise error

    runner = PipelineRunner([ProgressStage()], observer=console, progress_sink=progress)
    with pytest.raises(HpxError) as caught:
        with profile_presentation(console):
            runner.run(_make_config(tmp_path))
    assert caught.value.__cause__ is error
    spinner.stop.assert_called_once()
    render.assert_not_called()
    assert console._spinner is None


def test_pipeline_construction_failure_restores_logging(monkeypatch, tmp_path):
    console = HpxConsole()
    logger = logging.getLogger("hpx")
    before = (list(logger.handlers), logger.level)
    error = RuntimeError("construction failed")
    cleanup = Mock()
    monkeypatch.setattr(console, "_stop_spinner", cleanup)
    with patch("helia_profiler.profiler.build_default_pipeline", side_effect=error):
        with pytest.raises(RuntimeError) as caught:
            with profile_presentation(console):
                run_profile(_make_config(tmp_path), observer=console)
    assert caught.value is error
    cleanup.assert_called_once()
    assert (logger.handlers, logger.level) == before
