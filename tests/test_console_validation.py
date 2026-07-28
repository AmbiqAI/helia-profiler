"""Tests for the completed validation Rich view."""

from __future__ import annotations

from rich.console import Console

from helia_profiler.console import HpxConsole
from helia_profiler.validation.report import ValidationReport, ValidationSummary
from helia_profiler.validation.runner import CaseResult


def _case(
    case_id: str,
    model: str,
    cycles: int,
    binary: int,
    *,
    engine: str = "helia-rt",
    comparison_group: str | None = None,
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        status="pass",
        duration_s=1.0,
        engine=engine,
        model_id=model,
        comparison_group=comparison_group,
        board="apollo510_evb",
        power=False,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        total_cycles=cycles,
        latency_avg_us=cycles / 100,
        binary_total_bytes=binary,
        allocated_arena_bytes=32_768,
    )


def test_print_validation_compares_models_and_marks_decision_candidates() -> None:
    report = ValidationReport(
        cases=(
            _case("fast", "keyword-small", 80, 140_000),
            _case("small", "keyword-tiny", 120, 90_000, engine="helia-aot"),
            _case("dominated", "vision", 160, 160_000),
        ),
        summary=ValidationSummary(total=3, passed=3, failed=0, skipped=0),
    )
    hpx_console = HpxConsole()
    output = Console(record=True, highlight=False, width=80)
    hpx_console._console = output

    hpx_console.print_validation(report)
    rendered = output.export_text()

    assert "Validation Results" in rendered
    assert "keyword-small" in rendered
    assert "keyword-tiny" in rendered
    assert "vision" in rendered
    assert "fastest" in rendered
    assert "smallest" in rendered
    assert "Binary" in rendered
    assert "Decision" in rendered
    assert "Performance and Size Candidates" in rendered


def test_print_validation_calculates_decisions_within_comparison_groups() -> None:
    report = ValidationReport(
        cases=(
            _case("kws-fast", "kws-base", 100, 100_000, comparison_group="kws"),
            _case("kws-slow", "kws-pruned", 120, 80_000, comparison_group="kws"),
            _case("vww", "vww", 500, 500_000, comparison_group="vww"),
        ),
        summary=ValidationSummary(total=3, passed=3, failed=0, skipped=0),
    )
    hpx_console = HpxConsole()
    output = Console(record=True, highlight=False, width=100)
    hpx_console._console = output

    hpx_console.print_validation(report)
    rendered = output.export_text()

    assert "Comparison groups" in rendered
    assert "kws, vww" in rendered
    assert rendered.count("fastest") == 2
    assert "Group" in rendered
