"""Pytest configuration for the hardware validation suite.

Responsibilities:

* Register CLI options (``--mlperf-*``) that filter the matrix.
* Parametrise the single ``test_case`` test from the filtered matrix.
* Aggregate per-case :class:`CaseResult` objects into a session report
  (both JSON and Markdown) written under ``--mlperf-output``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helia_profiler.validation import build_matrix
from helia_profiler.validation.runner import CaseResult


# ---------------------------------------------------------------------------
# CLI options
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    grp = parser.getgroup("mlperf-validation")
    grp.addoption(
        "--mlperf-models",
        default="",
        help="Comma-separated model IDs to run (default: all).",
    )
    grp.addoption(
        "--mlperf-engines",
        default="",
        help="Comma-separated engine names (helia-rt,helia-aot).",
    )
    grp.addoption(
        "--mlperf-power",
        default="both",
        choices=("both", "on", "off"),
        help="Power matrix: both|on|off (default: both).",
    )
    grp.addoption(
        "--mlperf-boards",
        default="apollo510_evb",
        help="Comma-separated board IDs (default: apollo510_evb).",
    )
    grp.addoption(
        "--mlperf-output",
        default="validation_results",
        help="Where to write per-case artifacts + summary report.",
    )
    grp.addoption(
        "--mlperf-timeout",
        type=float,
        default=900.0,
        help="Per-case timeout in seconds (default: 900).",
    )


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------


def _split_csv(raw: str) -> list[str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "case" not in metafunc.fixturenames:
        return
    cfg = metafunc.config
    cases = build_matrix(
        models=_split_csv(cfg.getoption("--mlperf-models")),
        engines=_split_csv(cfg.getoption("--mlperf-engines")),
        power=cfg.getoption("--mlperf-power"),
        boards=_split_csv(cfg.getoption("--mlperf-boards")),
    )
    metafunc.parametrize(
        "case",
        cases,
        ids=[c.case_id for c in cases],
    )


# ---------------------------------------------------------------------------
# Session state — collects CaseResult per test, dumps report at end
# ---------------------------------------------------------------------------


_RESULTS_KEY = pytest.StashKey[list[CaseResult]]()


@pytest.fixture(scope="session")
def validation_output_dir(request: pytest.FixtureRequest) -> Path:
    path = Path(request.config.getoption("--mlperf-output")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def repo_root() -> Path:
    # tests/validation/conftest.py → up two = helia-profiler root.
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def results_accumulator(request: pytest.FixtureRequest) -> list[CaseResult]:
    bucket = request.session.stash.setdefault(_RESULTS_KEY, [])
    return bucket


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    results: list[CaseResult] = session.stash.get(_RESULTS_KEY, [])
    if not results:
        return

    out_dir = Path(session.config.getoption("--mlperf-output")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # JSON
    (out_dir / "validation_report.json").write_text(
        json.dumps(
            {
                "cases": [r.to_dict() for r in results],
                "summary": _summary_stats(results),
            },
            indent=2,
            default=str,
        )
    )

    # Markdown
    (out_dir / "validation_report.md").write_text(_render_markdown(results))


def _summary_stats(results: list[CaseResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "pass": sum(1 for r in results if r.status == "pass"),
        "fail": sum(1 for r in results if r.status == "fail"),
        "skip": sum(1 for r in results if r.status == "skip"),
    }


def _render_markdown(results: list[CaseResult]) -> str:
    stats = _summary_stats(results)
    lines = [
        "# heliaPROFILER — Hardware Validation Report",
        "",
        f"- total: **{stats['total']}**",
        f"- pass: **{stats['pass']}**",
        f"- fail: **{stats['fail']}**",
        f"- skip: **{stats['skip']}**",
        "",
        "| Case | Status | Duration (s) | Layers | Cycles | Energy (µJ) | Avg (mA) | Peak (mA) | Notes |",
        "|------|--------|-------------:|-------:|-------:|------------:|---------:|----------:|-------|",
    ]
    for r in results:
        status_badge = {
            "pass": "✅ pass",
            "fail": "❌ fail",
            "skip": "⏭ skip",
        }.get(r.status, r.status)
        note = r.error or ""
        lines.append(
            "| {cid} | {st} | {dur:.1f} | {layers} | {cyc} | {energy} | {avg} | {peak} | {note} |".format(
                cid=r.case_id,
                st=status_badge,
                dur=r.duration_s,
                layers=r.layers if r.layers is not None else "-",
                cyc=r.total_cycles if r.total_cycles is not None else "-",
                energy=f"{r.energy_uj:.1f}" if r.energy_uj is not None else "-",
                avg=f"{r.avg_current_ma:.2f}" if r.avg_current_ma is not None else "-",
                peak=f"{r.peak_current_ma:.2f}" if r.peak_current_ma is not None else "-",
                note=note.replace("|", r"\|") if note else "",
            )
        )
    return "\n".join(lines) + "\n"
