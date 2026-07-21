"""Unit tests for HpxConsole error rendering."""

from __future__ import annotations

from rich.console import Console

from helia_profiler.console import HpxConsole
from helia_profiler.errors import BuildError, HpxError


def _render_error(exc: Exception) -> tuple[str, str]:
    hpx_console = HpxConsole(verbosity=0)
    stdout = Console(record=True, highlight=False, width=200)
    stderr = Console(record=True, highlight=False, width=200)
    hpx_console._console = stdout
    hpx_console._status_console = stderr
    hpx_console.print_error(exc)
    return stdout.export_text(), stderr.export_text()


def test_print_error_renders_details():
    exc = BuildError(
        "NSX build failed",
        hint="run with -v for full output",
        details="ninja: error: something broke\nsecond diagnostic line",
    )
    stdout, stderr = _render_error(exc)
    assert stdout == ""
    assert "Error: NSX build failed" in stderr
    assert "hint: run with -v for full output" in stderr
    assert "details: ninja: error: something broke" in stderr
    assert "second diagnostic line" in stderr


def test_print_error_without_details_has_no_details_line():
    exc = HpxError("plain failure", hint="a hint")
    _, stderr = _render_error(exc)
    assert "Error: plain failure" in stderr
    assert "details:" not in stderr


def test_print_error_with_empty_details_has_no_details_line():
    exc = BuildError("build failed", details="")
    _, stderr = _render_error(exc)
    assert "Error: build failed" in stderr
    assert "details:" not in stderr
