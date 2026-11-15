"""Execute the rendered diagnostic callback with deterministic clocks/providers."""

from __future__ import annotations

import pytest

from tests.test_aot_transfer_evidence import _build_and_run
from tests.test_template_render import _render_aot

pytestmark = pytest.mark.host_integration

_MAIN = r"""
int main() {
    hpx_transfer_run_start();
    hpx_transfer_diagnostic_reset();
    for (int i = 0; i < 16; ++i) samples[i] = {0U, true, 1U};
    for (int u = 0; u < fake_num_transfer_units; ++u) {
        DWT->CYCCNT = 0xfffffff0U;
        hpx_transfer_diagnostic_cb(u, fake_op_state_transfer_issue_started, 0, nullptr);
        DWT->CYCCNT = 0x10U;
        hpx_transfer_diagnostic_cb(u, fake_op_state_transfer_issue_finished, ISSUE_STATUS, nullptr);
        if (u < fake_num_dma_units) {
            DWT->CYCCNT = 100U;
            hpx_transfer_diagnostic_cb(u, fake_op_state_transfer_wait_started, 0, nullptr);
            DWT->CYCCNT = 100U + u;
            hpx_transfer_diagnostic_cb(u, fake_op_state_transfer_wait_finished, 0, nullptr);
        }
    }
    hpx_transfer_diagnostic_cb(0, fake_op_state_run_started, 0, nullptr);
    hpx_transfer_diagnostic_cb(0, fake_op_state_run_finished, 0, nullptr);
    MUTATION
    provider_stats.issues = 4;
    provider_stats.waits = 3;
    hpx_transfer_after_run();
    hpx_transfer_diagnostic_report(0, ELAPSED, TICKS, 0);
    hpx_printf("queries=%d,%d\n", dma_reads, pending_reads);
    return 0;
}
"""


def _run(tmp_path, mode="timing", *, mutation="", elapsed=96000, ticks=33, status=0):
    source = _render_aot(
        transport="rtt",
        clean_window_timer="stimer",
        transfer_diagnostics=mode,
        transfer_diagnostic_iterations=2,
    )
    preamble = "#define HPX_STIMER_HZ 32768U\nstatic uint32_t SystemCoreClock = 96000000U;\n"
    main = (
        _MAIN.replace("MUTATION", mutation)
        .replace("ELAPSED", str(elapsed))
        .replace("TICKS", str(ticks))
        .replace("ISSUE_STATUS", str(status))
    )
    # Clock declarations precede the generated global functions in the production template.
    return _build_and_run(tmp_path, preamble + source, main, extra_preamble=preamble)


def test_direct_timing_has_zero_queries_and_retains_zero_and_absent_waits(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "HPX_TRANSFER_DIAGNOSTIC_BEGIN=timing,0,96000000,configured,4,3",
        "HPX_TRANSFER_TIMING=0,32,0,0,0",
        "HPX_TRANSFER_TIMING=1,32,0,1,0",
        "HPX_TRANSFER_TIMING=2,32,0,2,0",
        "HPX_TRANSFER_TIMING=3,32,0,x,x",
        "HPX_TRANSFER_DIAGNOSTIC_STATS=4,3,0,0,0,0",
        "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,1",
        "queries=0,0",
    ]


def test_sampled_overlap_runs_separately_and_uses_actual_status_queries(tmp_path):
    result = _run(tmp_path, "overlap")
    assert result.returncode == 0, result.stderr
    assert "HPX_TRANSFER_TIMING=" not in result.stdout
    assert "HPX_TRANSFER_DIAGNOSTIC_OVERLAP=0:1/1,1:1/1,2:1/1,3:1/1;0:1/1" in result.stdout
    assert result.stdout.splitlines()[-1] == "queries=10,10"


@pytest.mark.parametrize(
    "mutation",
    [
        "hpx_transfer_diagnostic_cb(0, fake_op_state_transfer_issue_started, 0, nullptr);",
        "hpx_transfer_diagnostic_cb(4, fake_op_state_transfer_issue_started, 0, nullptr);",
        "g_transfer_calls[0].observed &= ~2U;",
        "g_transfer_calls[0].observed &= ~12U;",
    ],
)
def test_invalid_callback_evidence_is_not_complete(tmp_path, mutation):
    result = _run(tmp_path, mutation=mutation)
    assert "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,0" in result.stdout


@pytest.mark.parametrize(("elapsed", "ticks"), [(0, 33), (96000, 0), (1, 33), (96000, 2000000)])
def test_invalid_clock_or_full_dwt_span_is_not_complete(tmp_path, elapsed, ticks):
    result = _run(tmp_path, elapsed=elapsed, ticks=ticks)
    assert f"HPX_TRANSFER_DIAGNOSTIC_END={elapsed},0,0" in result.stdout


def test_failed_call_cannot_qualify_timing(tmp_path):
    result = _run(tmp_path, status=-1)
    assert "HPX_TRANSFER_TIMING=0,32,-1,0,0" in result.stdout
    assert "HPX_TRANSFER_DIAGNOSTIC_END=96000,0,0" in result.stdout


def test_timing_callback_has_no_pmu_or_observer_operations():
    source = _render_aot(
        transfer_diagnostics="timing", transfer_diagnostic_iterations=2, clean_window_timer="stimer"
    )
    start = source.index("static void hpx_transfer_diagnostic_cb(")
    callback = source[start : source.index("\n}\n", start)]
    for forbidden in ("pmu", "transfer_sample", "can_sleep", "hpx_printf", "heartbeat", "irq"):
        assert forbidden not in callback
    start = source.index("for (int diagnostic_iter")
    loop = source[start : source.index("callback = model_operator_cb;", start)]
    assert "profiled_infer_count" not in loop
    assert "profiled_infer_cycles" not in loop


@pytest.mark.parametrize("transport", ["rtt", "usb_cdc"])
def test_diagnostic_transport_delivery_is_outside_the_clock_bracket(transport):
    source = _render_aot(
        transport=transport,
        transfer_diagnostics="timing",
        transfer_diagnostic_iterations=2,
        clean_window_timer="stimer",
    )
    start = source.index("for (int diagnostic_iter")
    loop = source[start : source.index("callback = model_operator_cb;", start)]
    elapsed = loop.index("const uint32_t diagnostic_ticks")
    report = loop.index("hpx_transfer_diagnostic_report(")
    if transport == "rtt":
        assert elapsed < loop.index("hpx_rtt_set_blocking();") < report
        assert report < loop.index("hpx_rtt_set_nonblocking();")
    else:
        assert loop.index("usb_timer_pause();") < loop.index("diagnostic_start_tick")
        assert elapsed < loop.index("usb_timer_resume();") < report
