"""Pytest configuration for the hardware validation suite.

Responsibilities:

* Register CLI options (``--mlperf-*``) that filter the matrix.
* Parametrise the single ``test_case`` test from the filtered matrix.
* Aggregate per-case :class:`CaseResult` objects into a session report
  (both JSON and Markdown) written under ``--mlperf-output``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.validation import build_matrix
from helia_profiler.validation.runner import CaseResult
from helia_profiler.validation.report import write_validation_reports


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
        default="off",
        choices=("both", "on", "off"),
        help="Power matrix: both|on|off (default: off).",
    )
    grp.addoption(
        "--mlperf-boards",
        default="apollo510_evb",
        help="Comma-separated board IDs (default: apollo510_evb).",
    )
    grp.addoption(
        "--mlperf-repeat",
        type=int,
        default=1,
        help="Repeat each selected case N times for stress testing (default: 1).",
    )
    grp.addoption(
        "--mlperf-toolchains",
        default="",
        help="Comma-separated toolchains to run (default: board defaults).",
    )
    grp.addoption(
        "--mlperf-transports",
        default="",
        help="Comma-separated transports/interfaces to run (default: board defaults).",
    )
    grp.addoption(
        "--mlperf-memories",
        default="",
        help="Comma-separated model placement presets to run (default: board defaults).",
    )
    grp.addoption(
        "--mlperf-jlink-serials",
        default="",
        help="Comma-separated board=serial entries for multi-board validation.",
    )
    grp.addoption(
        "--mlperf-output",
        default="results/validation",
        help="Where to write per-case artifacts + summary report.",
    )
    grp.addoption(
        "--mlperf-timeout",
        type=float,
        default=900.0,
        help="Per-case timeout in seconds (default: 900).",
    )
    grp.addoption(
        "--mlperf-suite",
        default="",
        help="Optional named validation suite selected by hpx validate.",
    )


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------


def _split_csv(raw: str) -> list[str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def _split_serial_map(raw: str) -> dict[str, str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, serial = item.partition("=")
        if not sep or not board.strip() or not serial.strip():
            raise ValueError(
                "--mlperf-jlink-serials entries must use board=serial, "
                f"got {item!r}."
            )
        mapping[board.strip()] = serial.strip()
    return mapping


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "case" not in metafunc.fixturenames:
        return
    cfg = metafunc.config
    cases = build_matrix(
        models=_split_csv(cfg.getoption("--mlperf-models")),
        engines=_split_csv(cfg.getoption("--mlperf-engines")),
        power=cfg.getoption("--mlperf-power"),
        boards=_split_csv(cfg.getoption("--mlperf-boards")),
        toolchains=_split_csv(cfg.getoption("--mlperf-toolchains")),
        transports=_split_csv(cfg.getoption("--mlperf-transports")),
        memories=_split_csv(cfg.getoption("--mlperf-memories")),
        jlink_serials=_split_serial_map(cfg.getoption("--mlperf-jlink-serials")),
        repeat=cfg.getoption("--mlperf-repeat"),
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

    write_validation_reports(
        results,
        out_dir,
        validation_options=_validation_options(session.config),
        repo_root=Path(__file__).resolve().parents[2],
    )

def _validation_options(config: pytest.Config) -> dict[str, object]:
    return {
        "suite": config.getoption("--mlperf-suite"),
        "models": config.getoption("--mlperf-models"),
        "engines": config.getoption("--mlperf-engines"),
        "power": config.getoption("--mlperf-power"),
        "boards": config.getoption("--mlperf-boards"),
        "repeat": config.getoption("--mlperf-repeat"),
        "toolchains": config.getoption("--mlperf-toolchains"),
        "transports": config.getoption("--mlperf-transports"),
        "memories": config.getoption("--mlperf-memories"),
        "jlink_serials": config.getoption("--mlperf-jlink-serials"),
        "output_dir": config.getoption("--mlperf-output"),
        "timeout_s": config.getoption("--mlperf-timeout"),
    }
