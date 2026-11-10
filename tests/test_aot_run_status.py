"""heliaAOT firmware: every model_run status is checked after its loop, outside timing.

The rendered statements are compiled and run on the host with stand-ins for
the module and the transport, so the assertions are about what the firmware
does with a failing run, not about the text it contains.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_template_render import _render_aot, _render_litert

_CHECK = "hpx_aot_check_runs();"


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


def _invoke(source: str) -> str:
    match = re.search(r"\{ int32_t s = fake_model_run\(&fake_model_ctx\);.*?\} \}", source)
    assert match is not None
    return match.group(0)


_PREAMBLE = r"""
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
struct Context {} fake_model_ctx;
static const int32_t results[] = {RESULTS};
static int runs = 0;
int32_t fake_model_run(Context *) { return results[runs++]; }
static int32_t hpx_aot_status = 0;
static void hpx_rtt_set_blocking(void) {}
static void hpx_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::vprintf(fmt, ap);
    va_end(ap);
}
[[noreturn]] static void hpx_park(void) {
    std::fflush(stdout);
    std::exit(3);
}
"""


@pytest.mark.host_integration
@pytest.mark.parametrize(
    ("results", "parked", "line"),
    [
        ((0, 0, 0, 0), False, None),
        ((0, 301, 302, 0), True, "HPX_ERROR=model_run_failed:301"),
        ((0, 0, 0, 14), True, "HPX_ERROR=model_run_failed:14"),
    ],
)
def test_a_loop_stops_at_its_first_failed_run_and_reports_it_after(
    tmp_path: Path, results, parked, line
):
    """Nothing of the loop follows the failed run: no later refill, poison or
    run, and no more of that iteration (here, the statements around the invoke)."""
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render_aot(transport="rtt")
    program = (
        _PREAMBLE.replace("RESULTS", ", ".join(str(r) for r in results))
        + _function(source, "static void hpx_aot_check_runs(void) {")
        + "\nint main() {\n"
        + "    int before = 0, after = 0;\n"
        + f"    for (int i = 0; i < {len(results)}; i++) {{\n"
        + f"        ++before;\n        {_invoke(source)}\n        ++after;\n    }}\n"
        + '    hpx_printf("loop done after %d runs, %d starts, %d completions\\n",'
        + " runs, before, after);\n"
        + f"    {_CHECK}\n    return 0;\n}}\n"
    )
    path = tmp_path / "check.cc"
    path.write_text(program, encoding="utf-8")
    executable = tmp_path / "check.exe"
    subprocess.run([compiler, "-std=c++17", str(path), "-o", str(executable)], check=True)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == (3 if parked else 0)
    lines = result.stdout.splitlines()
    ran = next((i + 1 for i, r in enumerate(results) if r != 0), len(results))
    completed = ran - (1 if parked else 0)
    assert lines[0] == f"loop done after {ran} runs, {ran} starts, {completed} completions"
    assert lines[1:] == ([line] if line else [])


@pytest.mark.parametrize("transport", ["rtt", "swo", "uart", "usb_cdc"])
@pytest.mark.parametrize("clean_window_timer", ["dwt", "stimer"])
def test_every_loop_of_runs_is_checked_outside_timing(transport: str, clean_window_timer: str):
    source = _render_aot(transport=transport, clean_window_timer=clean_window_timer)
    sites = [m.start() for m in re.finditer(re.escape(_CHECK), source)]
    # The clean warm-up, the clean window, and each pass's warm-up and
    # profiled iterations (one pass in this render).
    assert len(sites) == 4
    warm, window, pass_warmup, profiled = sites
    assert source.rindex("fake_model_run(", 0, warm) < warm < source.index("hpx_sync_ready();")
    assert source.rindex("hpx_sync_window_end();", 0, window) < window
    assert source.rindex("HPX_CLEAN_INFER_COUNT", 0, window) < window
    assert source.rindex("fake_model_run(", 0, pass_warmup) < pass_warmup
    assert pass_warmup < source.index("phase=warmup_done")
    assert source.rindex("profiled_infer_count++;", 0, profiled) < profiled
    assert profiled < source.index("HPX_PROFILED_INFER_COUNT")
    assert "HPX_ERROR=model_run_failed:%ld" in _function(
        source, "static void hpx_aot_check_runs(void) {"
    )


def test_power_binary_reports_a_failed_run_through_its_terminal():
    source = _render_aot(transport="rtt", power_only=True, power_window_timer="stimer")
    check = _function(source, "static void hpx_aot_check_runs(void) {")
    assert 'hpx_power_terminal_fail("model_run", 9U);' in check
    assert "HPX_ERROR" not in check
    sites = [m.start() for m in re.finditer(re.escape(_CHECK), source)]
    assert len(sites) == 2
    assert sites[1] < source.index("hpx_power_terminal_report(\n    true,")


def test_other_engines_render_no_run_check():
    assert _CHECK not in _render_litert(transport="rtt")
