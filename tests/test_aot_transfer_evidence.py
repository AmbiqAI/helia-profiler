"""heliaAOT firmware with an emitted transfer provider: what each instrumented run reports.

The rendered globals (the operator callback, the per-run start and report, the
loop check) are compiled on the host against stand-ins for the PMU, nsx-psram
and the provider, driven through one run's callback states, and run. The
assertions are on the lines the firmware prints.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.contracts.fixture_compile_cases import _AOT_TRANSFER_STUB, aot_model_stub
from tests.test_template_render import _render_aot

pytestmark = pytest.mark.host_integration

_PREAMBLE = r"""
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#define NSX_MEM_SRAM_BSS
#define NSX_STATUS_SUCCESS 0
#define __CORTEX_M 55U
#define ARM_PMU_L1D_CACHE_REFILL 0x0003
struct { uint32_t CYCCNT; } hpx_dwt;
#define DWT (&hpx_dwt)
static uint32_t ARM_PMU_Get_CNTR_OVS(void) { return 0U; }
static void ARM_PMU_Set_CNTR_OVS(uint32_t) {}
typedef struct { bool enabled; uint32_t eventId; int counterSize; } nsx_pmu_event_t;
typedef struct { bool added; uint32_t mapIndex; uint32_t counterValue; } nsx_pmu_counter_t;
typedef struct { const void *api; nsx_pmu_event_t events[8]; nsx_pmu_counter_t counter[8]; } nsx_pmu_config_t;
#define NSX_PMU_EVENT_COUNTER_SIZE_32 1
static const int nsx_pmu_V1_0_0 = 0;
static uint32_t pmu_counts[8];
static uint32_t nsx_pmu_get_counters(nsx_pmu_config_t *cfg) {
    for (int i = 0; i < 8; i++) cfg->counter[i].counterValue = pmu_counts[i];
    return 0U;
}
static void nsx_pmu_reset_counters(void) {}
static void nsx_pmu_reset_config(nsx_pmu_config_t *cfg) { std::memset(cfg, 0, sizeof(*cfg)); }
static void nsx_pmu_event_create(nsx_pmu_event_t *event, uint32_t id, int size) {
    event->enabled = true;
    event->eventId = id;
    event->counterSize = size;
}
static uint32_t nsx_pmu_init(nsx_pmu_config_t *) { return 0U; }

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
static void hpx_rtt_set_blocking(void) {}

// nsx-psram: one sample is a status and a DMA-in-progress bit, then the
// pending count. A failed read leaves the bit unchanged, as the API states.
struct Sample { uint32_t status; bool dma; uint32_t pending; };
static Sample samples[16];
static int dma_reads = 0, pending_reads = 0;
static uint32_t nsx_psram_dma_in_progress(bool *in_progress) {
    const Sample &s = samples[dma_reads++];
    if (s.status == 0U) *in_progress = s.dma;
    return s.status;
}
static uint32_t nsx_psram_pending(void) { return samples[pending_reads++].pending; }
"""

_PROVIDER = r"""
static fake_nsx_transfer_stats_t provider_stats;
static fake_nsx_transfer_last_t provider_last;
static bool provider_can_sleep = true;
int32_t fake_nsx_transfer_bind(fake_model_context_t *, const fake_nsx_transfer_options_t *) { return 0; }
int32_t fake_nsx_transfer_reset(void) { return 0; }
bool fake_nsx_transfer_can_sleep(void) { return provider_can_sleep; }
const fake_nsx_transfer_last_t *fake_nsx_transfer_last(void) { return &provider_last; }
const fake_nsx_transfer_stats_t *fake_nsx_transfer_stats(void) { return &provider_stats; }
#define HPX_AOT_TRANSFER 1
"""

#: One instrumented run: units 0 to 2 issued, a wait for a dma unit, operator
#: 0 run, as heliaAOT's callback states order them.
_RUN = r"""
static void state(int32_t op, fake_operator_state_t s) { model_operator_cb(op, s, 0, nullptr); }

int main() {
    static const uint32_t ids[] = { 0x0004, EVENT };
    profiler_init_custom(ids, 2);
    g_profiler_enabled = true;
    // DMA running and reads pending at both ends of unit 0; the DMA stops
    // inside unit 1; the read at unit 2's end fails, which is no evidence.
    const Sample run[] = {
        {0U, true, 2U}, {0U, true, 1U},
        {0U, true, 1U}, {0U, false, 0U},
        {0U, true, 1U}, {1U, true, 1U},
        {0U, true, 1U}, {0U, true, 0U},
    };
    std::memcpy(samples, run, sizeof(run));
    provider_stats.issues = 10U;
    provider_stats.waits = 4U;
    hpx_transfer_run_start();
    state(0, fake_op_state_transfer_issue_started);
    pmu_counts[1] = 5U;
    state(0, fake_op_state_transfer_issue_finished);
    state(1, fake_op_state_transfer_issue_started);
    pmu_counts[1] = 108U;
    state(1, fake_op_state_transfer_issue_finished);
    state(2, fake_op_state_transfer_issue_started);
    pmu_counts[1] = 3U;
    state(2, fake_op_state_transfer_issue_finished);
    provider_can_sleep = WAIT_CAN_SLEEP;
    state(0, fake_op_state_transfer_wait_started);
    state(0, fake_op_state_run_started);
    pmu_counts[1] = 42U;
    state(0, fake_op_state_run_finished);
    provider_stats.issues = 13U;
    provider_stats.waits = 5U;
    provider_stats.peak_in_flight = 2U;
    provider_stats.wait_checks_max = 7U;
    provider_can_sleep = AFTER_CAN_SLEEP;
    hpx_transfer_report();
    hpx_aot_status = STATUS;
    provider_last.error = 319;
    provider_last.driver_status = 0x1205U;
    provider_last.ticket = 2U;
    hpx_aot_check_runs();
    hpx_printf("checked\n");
    return 0;
}
"""


def _globals(source: str) -> str:
    start = source.index("static constexpr int kMaxLayers")
    end = source.index("static void hpx_aot_check_runs(void) {")
    end = source.index("\n}\n", end) + 3
    return source[start:end]


def _run(
    tmp_path: Path,
    *,
    event: str = "ARM_PMU_L1D_CACHE_REFILL",
    wait_can_sleep: bool = False,
    after_can_sleep: bool = True,
    status: int = 0,
):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render_aot(transport="rtt", clean_window_timer="stimer")
    (tmp_path / "fake_model.h").write_text(aot_model_stub(external_arenas=True, transfer=True))
    (tmp_path / "fake_nsx_transfer.h").write_text(_AOT_TRANSFER_STUB)
    program = (
        _PREAMBLE
        + '#include "fake_nsx_transfer.h"\n'
        + _PROVIDER
        + _globals(source)
        + _RUN.replace("EVENT", event)
        .replace("STATUS", str(status))
        .replace("WAIT_CAN_SLEEP", "true" if wait_can_sleep else "false")
        .replace("AFTER_CAN_SLEEP", "true" if after_can_sleep else "false")
    )
    path = tmp_path / "evidence.cc"
    path.write_text(program, encoding="utf-8")
    executable = tmp_path / "evidence.exe"
    subprocess.run(
        [
            compiler,
            "-std=gnu++17",
            "-Wall",
            "-Werror",
            "-Wno-unused-function",
            "-I",
            str(tmp_path),
            str(path),
            "-o",
            str(executable),
        ],
        check=True,
    )
    return subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)


def test_one_run_reports_refills_overlap_and_counts(tmp_path: Path):
    result = _run(
        tmp_path, event="ARM_PMU_L1D_CACHE_REFILL", wait_can_sleep=False, after_can_sleep=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "HPX_TRANSFER_REFILLS=0:5,1:108,2:3,3:0;0:42",
        "HPX_TRANSFER_OVERLAP=0:1/1,1:0/0,2:0/1,3:0/0;0:1/0",
        "HPX_TRANSFER_STATS=3,1,0,2,7",
        "checked",
    ]


def test_a_pass_without_the_refill_event_prints_no_refills(tmp_path: Path):
    result = _run(tmp_path, event="0x0011", wait_can_sleep=False, after_can_sleep=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert not any(line.startswith("HPX_TRANSFER_REFILLS=") for line in lines)
    assert "HPX_TRANSFER_OVERLAP=0:1/1,1:0/0,2:0/1,3:0/0;0:1/0" in lines


@pytest.mark.parametrize(("wait_can_sleep", "after_can_sleep"), [(True, True), (False, False)])
def test_a_broken_power_clause_stops_the_capture(tmp_path: Path, wait_can_sleep, after_can_sleep):
    result = _run(
        tmp_path,
        event="ARM_PMU_L1D_CACHE_REFILL",
        wait_can_sleep=wait_can_sleep,
        after_can_sleep=after_can_sleep,
    )
    assert result.returncode == 3
    assert result.stdout.splitlines()[-1] == "HPX_ERROR=transfer_power_clause"


def test_a_failed_run_names_the_providers_failure(tmp_path: Path):
    result = _run(tmp_path, status=301)
    assert result.returncode == 3
    assert result.stdout.splitlines()[-1] == (
        "HPX_ERROR=model_run_failed:301,provider=319,driver=4613,ticket=2"
    )


def test_the_provider_is_bound_before_model_init_when_the_module_ships_it():
    source = _render_aot(transport="rtt")
    guard = source.index('#if __has_include("fake_nsx_transfer.h")')
    assert source.index('#include "fake_nsx_transfer.h"', guard) < source.index("#endif", guard)
    bind = source.index("    hpx_transfer_bind(nullptr);")
    assert source.rindex("#ifdef HPX_AOT_TRANSFER", 0, bind) > source.rindex("#endif", 0, bind)
    assert bind < source.index("fake_model_init(&fake_model_ctx);")
    function = source[source.index("static void hpx_transfer_bind(") :]
    function = function[: function.index("\n}\n")]
    assert 'hpx_printf("HPX_ERROR=transfer_bind_failed:%ld\\n", (long)status);' in function
    assert "hpx_park();" in function


def test_the_power_binary_ends_with_its_terminal_on_a_refused_bind():
    source = _render_aot(transport="rtt", power_only=True, power_window_timer="stimer")
    function = source[source.index("static void hpx_transfer_bind(") :]
    function = function[: function.index("\n}\n")]
    assert 'hpx_power_terminal_fail("transfer_bind", 8U);' in function
    assert "HPX_TRANSFER_" not in source
