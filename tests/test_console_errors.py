"""Unit tests for HpxConsole error rendering."""

from __future__ import annotations

from rich.console import Console

from helia_profiler.console import HpxConsole
from helia_profiler.errors import BuildError, HpxError


def _render_error(exc: Exception) -> str:
    hpx_console = HpxConsole(verbosity=0)
    recorder = Console(record=True, highlight=False, width=200)
    hpx_console._console = recorder
    hpx_console.print_error(exc)
    return recorder.export_text()


def test_print_error_renders_details():
    exc = BuildError(
        "NSX build failed",
        hint="run with -v for full output",
        details="ninja: error: something broke\nsecond diagnostic line",
    )
    out = _render_error(exc)
    assert "Error: NSX build failed" in out
    assert "hint: run with -v for full output" in out
    assert "details: ninja: error: something broke" in out
    assert "second diagnostic line" in out


def test_print_error_without_details_has_no_details_line():
    exc = HpxError("plain failure", hint="a hint")
    out = _render_error(exc)
    assert "Error: plain failure" in out
    assert "details:" not in out


def test_print_error_with_empty_details_has_no_details_line():
    exc = BuildError("build failed", details="")
    out = _render_error(exc)
    assert "Error: build failed" in out
    assert "details:" not in out
