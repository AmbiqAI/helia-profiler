"""Focused tests for phase-aware, verbosity-sensitive progress rendering."""

from __future__ import annotations

from helia_profiler.console.progress import _format_eta, _phase_for_stage, progress_update
from helia_profiler.pipeline import ProgressUpdate


class _FakeStatus:
    def __init__(self, text: str) -> None:
        self.text = text
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def update(self, text: str) -> None:
        self.text = text


class _FakeRichConsole:
    def __init__(self) -> None:
        self.printed: list[str] = []
        self.statuses: list[_FakeStatus] = []

    def print(self, text: str) -> None:
        self.printed.append(text)

    def status(self, text: str, **kwargs) -> _FakeStatus:
        del kwargs
        status = _FakeStatus(text)
        self.statuses.append(status)
        return status


class _FakeHpxConsole:
    def __init__(self, verbosity: int) -> None:
        self.verbosity = verbosity
        self._console = _FakeRichConsole()
        self._spinner = None
        self._stage_name = "capture_power"
        self._stage_index = 17
        self._stage_total = 18
        self._phase_name = "Power"


def test_stage_phase_mapping() -> None:
    assert _phase_for_stage("resolve_platform") == "Setup"
    assert _phase_for_stage("capture_pmu") == "Profile"
    assert _phase_for_stage("capture_power") == "Power"
    assert _phase_for_stage("generate_report") == "Report"


def test_eta_formatting() -> None:
    assert _format_eta(8.4) == "8s"
    assert _format_eta(125) == "2m 05s"


def test_progress_update_shows_phase_count_and_eta() -> None:
    console = _FakeHpxConsole(verbosity=0)

    progress_update(
        console,
        ProgressUpdate(
            message="Running fixed inference window",
            completed=50,
            total=100,
            unit="iterations",
            eta_s=5,
        ),
    )

    assert console._spinner is not None
    text = console._spinner.text
    assert "Power" in text
    assert "50/100 iterations" in text
    assert "about 5s remaining" in text
    assert "17/18" in text


def test_minimum_verbosity_filters_detail() -> None:
    console = _FakeHpxConsole(verbosity=0)

    progress_update(
        console,
        ProgressUpdate(message="Compiler detail", min_verbosity=1),
    )

    assert console._spinner is None


def test_verbose_checkpoint_is_durable_line() -> None:
    console = _FakeHpxConsole(verbosity=1)

    progress_update(
        console,
        ProgressUpdate(message="Profile captured", kind="checkpoint"),
    )

    assert console._spinner is None
    assert any("Profile captured" in line for line in console._console.printed)
