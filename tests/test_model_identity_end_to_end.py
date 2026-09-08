"""A malformed reported model size must survive the whole output path.

The #281 warning is worthless if the artifacts it belongs in cannot be written
or rendered. Two independent reviews found exactly that: the raw string reached
``summary.json``'s strict round-trip and the console's numeric memory panel, and
each raised instead of publishing. These drive the real chain -- wire text
through the parser, evaluation, ``write_report`` and the full console render --
because every seam that broke sits downstream of hand-built metadata.
"""

from __future__ import annotations

from pathlib import Path

from tests.pipeline_context_helpers import set_profile_result

import json
import pytest
from rich.console import Console

from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.config import load_config
from helia_profiler.console import HpxConsole
from helia_profiler.console.results import print_results
from helia_profiler.evaluation import evaluate_run
from helia_profiler.pipeline import PipelineContext
from helia_profiler.report import write_report
from helia_profiler.results import ModelInfo, RunMetadata
from helia_profiler.results.issues import IssueCode
from helia_profiler.wire import HPX_END_SENTINEL, HPX_START_SENTINEL

SENT_BYTES = 53_744


def _context(tmp_path: Path, wire: str | None) -> PipelineContext:
    config = load_config(
        None,
        {
            "model": {"path": "test.tflite"},
            "engine": {"type": "helia-rt"},
            "output": {"dir": tmp_path, "model_explorer": False},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.run_metadata = RunMetadata(
        hpx_version="0.1.0",
        run_id="run-1",
        timestamp="2026-09-08T00:00:00+00:00",
        model=ModelInfo(name="kws.tflite", size_bytes=SENT_BYTES, sha256="a" * 64),
    )
    lines = [HPX_START_SENTINEL]
    if wire is not None:
        lines.append(f"HPX_MODEL_SIZE={wire}")
    lines.extend(
        [
            "HPX_ARENA_SIZE=32768",
            "HPX_ALLOCATED_ARENA=16384",
            "HPX_PRESETS=basic_cpu",
            "--- HPX_PRESET basic_cpu ---",
            "--- HPX_ITER 0 ---",
            "Layer,Op,ARM_PMU_CPU_CYCLES",
            "0,CONV_2D,1000",
            HPX_END_SENTINEL,
        ]
    )
    set_profile_result(ctx, parse_firmware_output(lines))
    lock = tmp_path / "workspace" / "nsx.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    return ctx


def _publish_and_render(ctx: PipelineContext, tmp_path: Path) -> tuple[dict, str]:
    ctx.run_evaluation = evaluate_run(ctx)
    write_report(ctx)
    console = HpxConsole(verbosity=0)
    recorder = Console(record=True, highlight=False, width=200)
    console._console = recorder
    print_results(console, ctx)
    return json.loads((tmp_path / "summary.json").read_text()), recorder.export_text()


@pytest.mark.parametrize("wire", ["abc", "1024.0", "null"])
def test_a_malformed_size_still_publishes_and_renders(wire: str, tmp_path: Path):
    summary, rendered = _publish_and_render(_context(tmp_path, wire), tmp_path)

    codes = [issue["code"] for issue in summary["issues"]]
    assert IssueCode.FIRMWARE_MODEL_IDENTITY_UNVERIFIABLE in codes
    # The numeric field carries measurements only; the raw device text lives on
    # the issue, so the diagnostic is preserved without corrupting the schema.
    assert "model_size" not in summary.get("memory", {})
    context = next(
        issue["context"]
        for issue in summary["issues"]
        if issue["code"] == IssueCode.FIRMWARE_MODEL_IDENTITY_UNVERIFIABLE
    )
    assert context["reported_model_size"] == wire
    assert "unavailable" in rendered


def test_a_matching_size_publishes_the_number_and_stays_clean(tmp_path: Path):
    summary, rendered = _publish_and_render(_context(tmp_path, str(SENT_BYTES)), tmp_path)

    assert summary["memory"]["model_size"] == SENT_BYTES
    codes = [issue["code"] for issue in summary["issues"]]
    assert IssueCode.FIRMWARE_MODEL_IDENTITY_UNVERIFIABLE not in codes
    assert IssueCode.FIRMWARE_MODEL_MISMATCH not in codes
    assert "unavailable" not in rendered


def test_a_mismatched_size_publishes_the_error_and_the_number(tmp_path: Path):
    summary, _ = _publish_and_render(_context(tmp_path, "1244"), tmp_path)

    assert summary["memory"]["model_size"] == 1244
    assert IssueCode.FIRMWARE_MODEL_MISMATCH in [issue["code"] for issue in summary["issues"]]


def test_an_absent_size_publishes_nothing_about_identity(tmp_path: Path):
    summary, rendered = _publish_and_render(_context(tmp_path, None), tmp_path)

    codes = [issue["code"] for issue in summary["issues"]]
    assert IssueCode.FIRMWARE_MODEL_IDENTITY_UNVERIFIABLE not in codes
    assert IssueCode.FIRMWARE_MODEL_MISMATCH not in codes
    assert "model_size" not in summary.get("memory", {})
    assert "unavailable" not in rendered
