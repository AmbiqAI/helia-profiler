"""Execute the actual rendered recorder against hand-authored provider schedules."""

import pytest

from helia_profiler.capture.parser import parse_firmware_output
from tests.contracts.fixture_compile_cases import _AOT_TRANSFER_OBSERVER_STUB
from tests.test_aot_transfer_evidence import _PROVIDER, _build_and_run
from tests.test_template_render import _render_aot
from tests.test_transfer_evidence import _session

pytestmark = pytest.mark.host_integration

_PROVIDER_OBSERVER = r"""
static fake_nsx_transfer_observer_fn observer;
static void *observer_context;
""" + _PROVIDER.replace(
    "return bind_results[binds++];",
    """const int32_t status = bind_results[binds++];
    if (status == 0) {
        observer = options ? options->observe : nullptr;
        observer_context = options ? options->observer_ctx : nullptr;
    }
    return status;""",
)

_MAIN = r"""
static void state(int id, fake_operator_state_t s, uint32_t offset, int status = 0) {
    DWT->CYCCNT = ORIGIN + offset;
    hpx_transfer_diagnostic_cb(id, s, status, nullptr);
}
int main() {
    hpx_transfer_trace_prepare();
    hpx_transfer_run_start();
    hpx_transfer_diagnostic_reset();
    hpx_transfer_trace_start(ORIGIN);
    for (int u = 0; u < fake_num_transfer_units; ++u) {
        state(u, fake_op_state_transfer_issue_started, 10U + 20U * u);
        if (u < fake_num_dma_units && u != REFUSED) {
            const fake_nsx_transfer_piece_t piece = {(uint32_t)u, (uint32_t)u, 64U, PIECE_STATUS};
            DWT->CYCCNT = ORIGIN + 15U + 20U * u;
            observer(observer_context, &piece);
        }
        state(u, fake_op_state_transfer_issue_finished, 20U + 20U * u, u == REFUSED ? -1 : 0);
    }
    state(0, fake_op_state_run_started, 90);
    state(0, fake_op_state_run_finished, 100);
    for (int u = 0; u < fake_num_dma_units; ++u) {
        state(u, fake_op_state_transfer_wait_started, 110U + 20U * u);
        state(u, fake_op_state_transfer_wait_finished, 120U + 20U * u);
    }
    MUTATION
    provider_stats.issues = 3;
    provider_stats.waits = 3;
    g_trace_quiescent = QUIESCENT;
    if (g_trace_quiescent) __atomic_store_n(&g_trace.recording, 0U, __ATOMIC_RELEASE);
    hpx_transfer_diagnostic_report(0, 96000, 33, 0);
    hpx_printf("queries=%d,%d\n", dma_reads, pending_reads);
    return 0;
}
"""


def _execute(tmp_path, main, *, cap=4):
    preamble = "#define HPX_STIMER_HZ 32768U\nstatic uint32_t SystemCoreClock = 96000000U;\n"
    source = _render_aot(
        transfer_diagnostics="trace",
        transfer_trace_max_pieces=cap,
        transfer_diagnostic_iterations=1,
        clean_window_timer="stimer",
    )
    return _build_and_run(
        tmp_path,
        source,
        main,
        extra_preamble=preamble,
        provider_header=_AOT_TRANSFER_OBSERVER_STUB,
        provider_source=_PROVIDER_OBSERVER,
    )


def _run(tmp_path, *, cap=4, status=0, refused=-1, mutation="", quiescent=True, origin=1000):
    main = (
        _MAIN.replace("ORIGIN", str(origin) + "U")
        .replace("PIECE_STATUS", str(status) + "U")
        .replace("REFUSED", str(refused))
        .replace("MUTATION", mutation)
        .replace("QUIESCENT", "true" if quiescent else "false")
    )
    run = _execute(tmp_path, main, cap=cap)
    assert run.returncode == 0, run.stderr
    record = parse_firmware_output(_session(*run.stdout.splitlines())).transfer_diagnostics[0]
    return record, run.stdout


def test_records_actual_boundaries_and_completion_before_submit_returns(tmp_path):
    record, raw = _run(tmp_path)
    assert record.complete
    assert len(record.trace.completions) == 3
    assert record.trace.completions[0].cycles == 15
    assert record.trace.spans[0].start_cycles == 10 and record.trace.spans[0].end_cycles == 20
    assert record.trace.binding_epoch == 1 and record.trace.sequence_start == 0
    assert record.trace.storage_bytes > 4 * 20
    assert raw.splitlines()[-1] == "queries=0,0"


def test_dwt_origin_wrap_keeps_unsigned_relative_offsets(tmp_path):
    record, _ = _run(tmp_path, origin=0xFFFFFFF0)
    assert record.complete
    assert record.trace.completions[2].cycles == 55


def test_overflow_never_overwrites_first_records_or_qualifies(tmp_path):
    record, _ = _run(tmp_path, cap=1)
    assert not record.complete
    assert record.trace.observed_count == 3 and record.trace.dropped_count == 2
    assert [p.sequence for p in record.trace.completions] == [0]


@pytest.mark.parametrize("status,refused", [(7, -1), (0, 2)])
def test_failed_completion_or_refused_piece_cannot_qualify(tmp_path, status, refused):
    record, _ = _run(tmp_path, status=status, refused=refused)
    assert not record.complete


def test_failed_drain_does_not_read_piece_storage_or_claim_quiescence(tmp_path):
    record, _ = _run(tmp_path, quiescent=False)
    assert not record.complete and not record.trace.quiescent
    assert record.trace.completions == () and record.trace.dropped_count == 3


def test_late_completion_outside_invocation_stays_unqualified(tmp_path):
    record, _ = _run(tmp_path, mutation="g_trace.pieces[2].cycles = 96001U;")
    assert not record.complete and record.trace.completions[2].cycles == 96001


def test_thread_and_isr_do_not_share_an_append_cursor_or_add_queries():
    source = _render_aot(
        transfer_diagnostics="trace", transfer_diagnostic_iterations=1, clean_window_timer="stimer"
    )
    observer = source[
        source.index("static void hpx_transfer_completion") : source.index(
            "static void hpx_transfer_trace_prepare"
        )
    ]
    callback = source[
        source.index("static void hpx_transfer_diagnostic_cb") : source.index(
            "static bool hpx_trace_span_valid"
        )
    ]
    for text in [observer, callback]:
        for forbidden in [
            "hpx_printf",
            "nsx_psram_",
            "transfer_sample",
            "can_sleep",
            "irq_save",
            "malloc",
            "new ",
        ]:
            assert forbidden not in text
    assert "trace->observed" in observer and ".observed" not in callback.replace(
        "span->observed", ""
    )
    loop = source[
        source.index("for (int diagnostic_iter") : source.index(
            "callback = model_operator_cb;", source.index("for (int diagnostic_iter")
        )
    ]
    assert loop.index("hpx_transfer_trace_prepare();") < loop.index(
        "diagnostic_start = DWT->CYCCNT"
    )
    assert loop.index("diagnostic_ticks =") < loop.index("fake_nsx_transfer_reset();")
    assert (
        loop.index("hpx_transfer_diagnostic_report(")
        < loop.index("hpx_aot_check_runs();")
        < loop.index("hpx_transfer_bind(nullptr);")
    )


def test_ordinary_and_direct_timing_have_no_completion_recorder():
    for mode in ["off", "timing", "overlap"]:
        source = _render_aot(
            transfer_diagnostics=mode, transfer_diagnostic_iterations=1, clean_window_timer="stimer"
        )
        assert "hpx_transfer_completion" not in source
        assert "HpxTransferTrace" not in source


def test_fresh_binding_starts_new_epoch_without_old_events(tmp_path):
    result = _execute(
        tmp_path,
        r"""
int main() {
    hpx_transfer_trace_prepare();
    hpx_transfer_trace_start(100U);
    fake_nsx_transfer_piece_t piece = {0U, 0U, 64U, 0U};
    DWT->CYCCNT = 120U;
    observer(observer_context, &piece);
    (void)fake_nsx_transfer_reset();
    __atomic_store_n(&g_trace.recording, 0U, __ATOMIC_RELEASE);
    hpx_transfer_bind(nullptr);
    hpx_transfer_trace_prepare();
    hpx_transfer_trace_start(200U);
    DWT->CYCCNT = 230U;
    observer(observer_context, &piece);
    hpx_printf("epoch=%lu count=%lu offset=%lu\n", (unsigned long)g_trace_epoch,
        (unsigned long)g_trace.observed, (unsigned long)g_trace.pieces[0].cycles);
}
""",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "epoch=2 count=1 offset=30"


def test_recorder_cannot_reset_while_old_callbacks_may_arrive(tmp_path):
    result = _execute(
        tmp_path,
        r"""
static void before_park() { hpx_printf("retained=%lu\n", (unsigned long)g_trace.observed); }
int main() {
    hpx_transfer_trace_prepare();
    hpx_transfer_trace_start(100U);
    const fake_nsx_transfer_piece_t piece = {0U, 0U, 64U, 0U};
    DWT->CYCCNT = 120U;
    observer(observer_context, &piece);
    park_report = before_park;
    hpx_transfer_trace_prepare();
}
""",
    )
    assert result.returncode == 3
    assert "retained=1" in result.stdout
    assert "transfer_bind_failed" in result.stdout


def test_failed_detach_keeps_static_context_alive_until_park(tmp_path):
    result = _execute(
        tmp_path,
        r"""
static void before_park() {
    hpx_printf("observer=%u context=%u count=%lu\n", observer != nullptr,
        observer_context == &g_trace, (unsigned long)g_trace.observed);
}
int main() {
    hpx_transfer_trace_prepare();
    hpx_transfer_trace_start(100U);
    const fake_nsx_transfer_piece_t piece = {0U, 0U, 64U, 0U};
    DWT->CYCCNT = 120U;
    observer(observer_context, &piece);
    (void)fake_nsx_transfer_reset();
    __atomic_store_n(&g_trace.recording, 0U, __ATOMIC_RELEASE);
    bind_results[1] = -5;
    park_report = before_park;
    hpx_transfer_bind(nullptr);
}
""",
    )
    assert result.returncode == 3
    assert "observer=1 context=1 count=1" in result.stdout
