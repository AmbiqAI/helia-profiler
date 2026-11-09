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
from tests.test_aot_io_digests import _fnv1a64
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
static int32_t bind_result = 0, init_result = 0, run_result = 0;
static int binds = 0, default_binds = 0, inits = 0, runs = 0;
int32_t fake_nsx_transfer_bind(fake_model_context_t *, const fake_nsx_transfer_options_t *options) {
    ++binds;
    if (options == nullptr) ++default_binds;
    return bind_result;
}
int32_t fake_model_init(fake_model_context_t *) { ++inits; return init_result; }
int32_t fake_model_run(fake_model_context_t *) { ++runs; return run_result; }
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
    """The rendered globals, through the last function of the block."""
    start = source.index("static constexpr int kMaxLayers")
    last = "static void hpx_transfer_probe_recover("
    end = source.index(last if last in source else "static void hpx_aot_check_runs(void) {")
    end = source.index("\n}\n", end) + 3
    if source.startswith("#endif\n", end):
        end += len("#endif\n")
    return source[start:end]


def _model_stub() -> str:
    """The module stand-in with model_init and model_run left to the harness."""
    stub = aot_model_stub(external_arenas=True, transfer=True)
    for name in ("init", "run"):
        definition = (
            f"static inline int32_t fake_model_{name}(fake_model_context_t *ctx) "
            "{ (void)ctx; return 0; }"
        )
        assert definition in stub
        stub = stub.replace(definition, f"int32_t fake_model_{name}(fake_model_context_t *ctx);")
    return stub


def _build_and_run(tmp_path: Path, source: str, main: str) -> subprocess.CompletedProcess[str]:
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    (tmp_path / "fake_model.h").write_text(_model_stub())
    (tmp_path / "fake_nsx_transfer.h").write_text(_AOT_TRANSFER_STUB)
    program = _PREAMBLE + '#include "fake_nsx_transfer.h"\n' + _PROVIDER + _globals(source) + main
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


def _run(
    tmp_path: Path,
    *,
    event: str = "ARM_PMU_L1D_CACHE_REFILL",
    wait_can_sleep: bool = False,
    after_can_sleep: bool = True,
    status: int = 0,
):
    source = _render_aot(transport="rtt", clean_window_timer="stimer")
    main = (
        _RUN.replace("EVENT", event)
        .replace("STATUS", str(status))
        .replace("WAIT_CAN_SLEEP", "true" if wait_can_sleep else "false")
        .replace("AFTER_CAN_SLEEP", "true" if after_can_sleep else "false")
    )
    return _build_and_run(tmp_path, source, main)


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


_PROBE = r"""
static uint8_t output0[3] = {1, 2, 3};
int main() {
    fake_model_ctx.outputs[0].data = output0;
    fake_model_ctx.outputs[0].size = sizeof(output0);
    g_profiler_enabled = true;
    hpx_aot_status = STATUS;
    provider_last.error = 321;
    bind_result = BIND;
    init_result = BIND == 0 ? 0 : 300;
    run_result = BIND == 0 ? 0 : 300;
    hpx_transfer_probe_recover(7U);
    hpx_printf("status=%ld profiling=%d binds=%d default=%d inits=%d runs=%d\n",
               (long)hpx_aot_status, (int)g_profiler_enabled, binds, default_binds, inits, runs);
    return 0;
}
"""


def _probe(tmp_path: Path, *, status: int, bind: int = 0):
    source = _render_aot(transport="rtt", clean_window_timer="stimer", transfer_timeout_probe=True)
    main = _PROBE.replace("STATUS", str(status)).replace("BIND", str(bind))
    return _build_and_run(tmp_path, source, main)


def test_a_probe_timeout_reports_the_recovery(tmp_path: Path):
    result = _probe(tmp_path, status=301)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "HPX_TRANSFER_RECOVERY=301,321,0,0,0,7",
        f"HPX_OUTPUT_0_FNV1A64={_fnv1a64(bytes([1, 2, 3])):016x}",
        "status=0 profiling=1 binds=1 default=1 inits=1 runs=1",
    ]


def test_a_probe_recovery_whose_bind_fails_reports_it(tmp_path: Path):
    result = _probe(tmp_path, status=301, bind=323)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "HPX_TRANSFER_RECOVERY=301,321,323,300,300,7"


def test_a_probe_run_without_a_timeout_rebinds_the_default_budget(tmp_path: Path):
    result = _probe(tmp_path, status=0)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "HPX_TRANSFER_RECOVERY=no_timeout",
        "status=0 profiling=1 binds=1 default=1 inits=0 runs=0",
    ]


def test_any_other_probe_failure_stops_the_capture(tmp_path: Path):
    result = _probe(tmp_path, status=302)
    assert result.returncode == 3
    assert result.stdout.splitlines() == [
        "HPX_ERROR=model_run_failed:302,provider=321,driver=0,ticket=0"
    ]


def test_the_probe_brackets_only_the_first_instrumented_run():
    source = _render_aot(transport="rtt", transfer_timeout_probe=True)
    probe_bind = source.index("hpx_transfer_bind(&hpx_probe_options);")
    assert "hpx_probe_options = {1U, 0U};" in source
    timed = source.index("uint32_t infer_start_cyc = DWT->CYCCNT;")
    assert source.rindex("if (iter == 0) {", 0, probe_bind) < probe_bind
    assert probe_bind < source.index("hpx_transfer_run_start();", probe_bind) < timed
    sample = source.index("const uint32_t hpx_probe_pending = nsx_psram_pending();")
    recover = source.index("hpx_transfer_probe_recover(hpx_probe_pending);")
    assert timed < sample < source.index("profiler_print_csv();", timed) < recover
    assert source.index("hpx_print_output_digests();", timed) < recover
    # One pass in this render: the probe belongs to its first iteration only.
    assert source.count("hpx_probe_options = {1U, 0U};") == 1
    assert "hpx_transfer_probe_recover" not in _render_aot(transport="rtt")


_POOL_REGIONS = [
    {
        "region_id": 0,
        "placement": "tcm",
        "alignment": 16,
        "size": 64,
        "blob_filename": None,
        "role": "scratch",
    },
    {
        "region_id": 1,
        "placement": "psram",
        "alignment": 32,
        "size": 4100,
        "blob_filename": "c.bin",
        "role": "constant",
    },
    {
        "region_id": 2,
        "placement": "tcm",
        "alignment": 4096,
        "size": 48,
        "blob_filename": None,
        "role": "pool",
    },
    {
        "region_id": 3,
        "placement": "psram",
        "alignment": 32,
        "size": 256,
        "blob_filename": None,
        "role": "scratch",
    },
]


def _poison_block(source: str) -> str:
    start = source.index("#if fake_num_dma_units > 0")
    return source[start : source.index("#endif\n", start) + len("#endif\n")]


def test_each_instrumented_run_poisons_the_pool_and_invalidates_psram_constants():
    source = _render_aot(transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS)
    block = _poison_block(source)
    assert source.count("#if fake_num_dma_units > 0") == 1
    timed = source.index("uint32_t infer_start_cyc = DWT->CYCCNT;")
    assert source.rindex("g_hb_ops_since = 0;", 0, timed) < source.index(block) < timed
    assert "memset(hpx_arena_buf_2, 0x5A, 48U);" in block
    assert block.count("memset(") == 1
    assert block.count("am_hal_cachectrl_dcache_invalidate(") == 1
    assert "hpx_arena_buf_1, 4100U" in block
    without_pool = _render_aot(
        transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS[:2] + _POOL_REGIONS[3:]
    )
    assert "fake_num_dma_units" not in without_pool
    assert "0x5A" not in without_pool


@pytest.mark.parametrize("dma_units", [0, 3])
def test_the_poison_and_invalidate_run_only_with_dma_units(tmp_path: Path, dma_units: int):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render_aot(transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS)
    program = (
        r"""
#include <cstdint>
#include <cstdio>
#include <cstring>
"""
        + f"#define fake_num_dma_units {dma_units}\n"
        + r"""
typedef struct { uint32_t ui32StartAddr; uint32_t ui32Size; } am_hal_cachectrl_range_t;
static am_hal_cachectrl_range_t invalidated[4];
static int invalidations = 0;
static uint32_t am_hal_cachectrl_dcache_invalidate(am_hal_cachectrl_range_t *range, bool clean) {
    if (clean) invalidated[invalidations++] = *range;
    return 0U;
}
static uint8_t pool[48], constants[4100];
static uint8_t *hpx_arena_buf_1 = constants, *hpx_arena_buf_2 = pool;
int main() {
"""
        + _poison_block(source)
        + r"""
    int poisoned = 0;
    for (uint8_t byte : pool) poisoned += byte == 0x5A;
    std::printf("%d %d %d %d\n", poisoned, invalidations,
                invalidations ? (int)(invalidated[0].ui32StartAddr == (uint32_t)(uintptr_t)constants) : 0,
                invalidations ? (int)invalidated[0].ui32Size : 0);
    return 0;
}
"""
    )
    path = tmp_path / "poison.cc"
    path.write_text(program, encoding="utf-8")
    executable = tmp_path / "poison.exe"
    subprocess.run([compiler, "-std=gnu++17", str(path), "-o", str(executable)], check=True)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    expected = "48 1 1 4100" if dma_units else "0 0 0 0"
    assert result.stdout.strip() == expected
