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

// Calls since the last model_run returned, so a test can see what ran between
// a run and a sample.
static int calls_since_run = 0, pending_gap = -1;
static void hpx_printf(const char *fmt, ...) {
    ++calls_since_run;
    va_list ap;
    va_start(ap, fmt);
    std::vprintf(fmt, ap);
    va_end(ap);
}
[[noreturn]] static void hpx_park(void) {
    std::fflush(stdout);
    std::exit(3);
}
static void hpx_rtt_set_blocking(void) { ++calls_since_run; }
static void hpx_rtt_set_nonblocking(void) {}

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
static uint32_t nsx_psram_pending(void) {
    if (pending_gap < 0) pending_gap = calls_since_run;
    return samples[pending_reads++].pending;
}
"""

_PROVIDER = r"""
static fake_nsx_transfer_stats_t provider_stats;
static fake_nsx_transfer_last_t provider_last;
static bool provider_can_sleep = true;
// Results by call; the stream pool is hpx_arena_buf_2, and each run notes
// whether it found the pool poisoned, then overwrites it as landed pieces would.
static int32_t bind_results[4], init_result = 0, run_results[4];
static int binds = 0, default_binds = 0, inits = 0, runs = 0;
static uint8_t pool[48];
static uint8_t *hpx_arena_buf_2 = pool;
static bool poisoned_at_run[4];
int32_t fake_nsx_transfer_bind(fake_model_context_t *, const fake_nsx_transfer_options_t *options) {
    ++calls_since_run;
    if (options == nullptr) ++default_binds;
    return bind_results[binds++];
}
int32_t fake_model_init(fake_model_context_t *) { ++calls_since_run; ++inits; return init_result; }
int32_t fake_model_run(fake_model_context_t *) {
    poisoned_at_run[runs] = pool[0] == 0x5A && pool[47] == 0x5A;
    std::memset(pool, 1, sizeof(pool));
    calls_since_run = 0;
    return run_results[runs++];
}
int32_t fake_nsx_transfer_reset(void) { return 0; }
bool fake_nsx_transfer_can_sleep(void) { return provider_can_sleep; }
const fake_nsx_transfer_last_t *fake_nsx_transfer_last(void) {
    ++calls_since_run;
    return &provider_last;
}
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
            "-Wno-unused-variable",
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
        "HPX_TRANSFER_OVERLAP=0:1/1,1:0/0,2:x/1,3:0/0;0:1/0",
        "HPX_TRANSFER_STATS=3,1,0,2,7,1",
        "checked",
    ]


def test_a_pass_without_the_refill_event_prints_no_refills(tmp_path: Path):
    result = _run(tmp_path, event="0x0011", wait_can_sleep=False, after_can_sleep=True)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert not any(line.startswith("HPX_TRANSFER_REFILLS=") for line in lines)
    assert "HPX_TRANSFER_OVERLAP=0:1/1,1:0/0,2:x/1,3:0/0;0:1/0" in lines


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
    function = source[source.index("static void hpx_transfer_bind_failed(") :]
    function = function[: function.index("\n}\n")]
    assert 'hpx_printf("HPX_ERROR=transfer_bind_failed:%ld\\n", (long)status);' in function
    assert "hpx_park();" in function
    assert "hpx_transfer_bind_failed(status);" in source


def test_the_power_binary_ends_with_its_terminal_on_a_refused_bind():
    source = _render_aot(transport="rtt", power_only=True, power_window_timer="stimer")
    function = source[source.index("static void hpx_transfer_bind_failed(") :]
    function = function[: function.index("\n}\n")]
    assert 'hpx_power_terminal_fail("transfer_bind", 8U);' in function
    assert "HPX_TRANSFER_" not in source


_PROBE_REGIONS: list[dict[str, object]] = [
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
_POOL_REGIONS = _PROBE_REGIONS


def _probe_block(source: str) -> str:
    start = source.index(
        "#ifdef HPX_AOT_TRANSFER\n    {\n        static const fake_nsx_transfer_options_t"
    )
    end = source.index("\n#endif\n", start) + len("\n#endif\n")
    return source[start:end]


_PROBE = (
    r"""
int main() {
    samples[0].pending = 7U;
    std::memset(pool, 0, sizeof(pool));
    provider_last.error = 321;
    const int32_t runs_by_call[] = {RUNS};
    const int32_t binds_by_call[] = {BINDS};
    std::memcpy(run_results, runs_by_call, sizeof(runs_by_call));
    std::memcpy(bind_results, binds_by_call, sizeof(binds_by_call));
    init_result = INIT;
    // Output 0 is the pool's bytes, which each fake run overwrites with 1s.
    fake_model_ctx.outputs[0].data = pool;
    fake_model_ctx.outputs[0].size = sizeof(pool);
"""
    + "#define HPX_AOT_TRANSFER 1\n"
    + r"""
BLOCK
    hpx_printf("status=%ld binds=%d default=%d inits=%d runs=%d poisoned=%d,%d gap=%d\n",
               (long)hpx_aot_status, binds, default_binds, inits, runs,
               (int)poisoned_at_run[0], (int)poisoned_at_run[1], pending_gap);
    return 0;
}
"""
)


def _probe(tmp_path: Path, *, runs=(301, 0), binds=(0, 0), init=0):
    source = _render_aot(
        transport="rtt",
        clean_window_timer="stimer",
        transfer_timeout_probe=True,
        arena_regions=_PROBE_REGIONS,
    )
    main = (
        _PROBE.replace("RUNS", ", ".join(map(str, runs)))
        .replace("BINDS", ", ".join(map(str, binds)))
        .replace("INIT", str(init))
        .replace("BLOCK", _probe_block(source))
    )
    return _build_and_run(tmp_path, source, main)


def test_a_probe_timeout_reports_the_probe_and_the_recovery(tmp_path: Path):
    result = _probe(tmp_path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines == [
        "HPX_TRANSFER_PROBE=301,321",
        "HPX_TRANSFER_RECOVERY=0,0,0,7",
        # The recovery run's bytes, under their own key.
        f"HPX_TRANSFER_RECOVERY_OUTPUT_0_FNV1A64={_fnv1a64(bytes([1]) * 48):016x}",
        # One-check bind, then the default; both runs found the pool
        # poisoned; nothing ran between the probe run and the pending sample.
        "status=0 binds=2 default=1 inits=1 runs=2 poisoned=1,1 gap=0",
    ]
    assert sum(line.startswith("HPX_TRANSFER_RECOVERY_OUTPUT_") for line in lines) == 1
    assert not any(line.startswith("HPX_OUTPUT_") for line in lines)


def test_a_probe_run_without_a_timeout_still_recovers(tmp_path: Path):
    result = _probe(tmp_path, runs=(0, 0))
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[:2] == [
        "HPX_TRANSFER_PROBE=0,321",
        "HPX_TRANSFER_RECOVERY=0,0,0,7",
    ]


@pytest.mark.parametrize(
    ("runs", "binds", "error"),
    [
        ((302, 0), (0, 0), "HPX_ERROR=model_run_failed:302,provider=321,driver=0,ticket=0"),
        ((301, 14), (0, 0), "HPX_ERROR=model_run_failed:14,provider=321,driver=0,ticket=0"),
        ((301, 0), (0, 323), "HPX_ERROR=transfer_bind_failed:323"),
        ((301, 0), (0, 0), "HPX_ERROR=model_init_failed:5"),
    ],
    ids=["probe-302", "recovery-run", "recovery-bind", "recovery-init"],
)
def test_a_failed_probe_or_recovery_fails_the_capture(tmp_path: Path, runs, binds, error):
    init = 5 if error.startswith("HPX_ERROR=model_init_failed") else 0
    result = _probe(tmp_path, runs=runs, binds=binds, init=init)
    assert result.returncode == 3
    lines = result.stdout.splitlines()
    assert lines[0].startswith("HPX_TRANSFER_PROBE=")
    assert lines[1].startswith("HPX_TRANSFER_RECOVERY=")
    assert lines[-1] == error


def _two_pass_probe_render() -> str:
    from tests.test_template_render import _sample_pmu_passes

    passes = _sample_pmu_passes() + [{**_sample_pmu_passes()[0], "name": "Second"}]
    return _render_aot(
        transport="rtt",
        transfer_timeout_probe=True,
        arena_regions=_PROBE_REGIONS,
        pmu_passes=passes,
    )


def test_the_probe_is_one_extra_run_before_the_first_pass():
    source = _two_pass_probe_render()
    block = _probe_block(source)
    assert source.count("hpx_probe_options") == 2  # declared and bound once
    assert source.count("HPX_TRANSFER_PROBE=") == 1
    first_pass = source.index('hpx_printf("\\n--- HPX_PRESET ')
    assert source.index(block) < first_pass
    # Not inside any timing bracket or counted loop, and the profiler is off.
    assert "DWT->CYCCNT" not in block and "profiled_infer" not in block
    assert source.rindex("for (int iter", 0, source.index(block)) < source.index(
        "hpx_sync_window_end();"
    )
    assert "g_profiler_enabled = true" not in source[: source.index(block)]
    # The pending sample is the statement after the probe run.
    assert block.index("const int32_t probe = fake_model_run(&fake_model_ctx);") < block.index(
        "const uint32_t pending = nsx_psram_pending();"
    )
    between = block[
        block.index("const int32_t probe = fake_model_run") : block.index("const uint32_t pending")
    ]
    assert between.count(";") == 1
    # The recovery run's digests only, under their own key; none for the probe run.
    assert "hpx_print_output_digests" not in block and "HPX_OUTPUT_" not in block
    assert block.count('"HPX_TRANSFER_RECOVERY_OUTPUT_%d_FNV1A64=') == 1
    assert source.count("HPX_TRANSFER_RECOVERY_OUTPUT_") == 1
    plain = _render_aot(transport="rtt", arena_regions=_PROBE_REGIONS)
    assert "HPX_TRANSFER_PROBE" not in plain and "HPX_TRANSFER_RECOVERY_OUTPUT_" not in plain


_STREAM_EVIDENCE = "// profiling.stream_evidence: every run starts from a poisoned stream"


def _evidence_block(source: str) -> str:
    start = source.index(_STREAM_EVIDENCE)
    return source[start : source.index("uint32_t infer_start_cyc", start)]


def test_stream_evidence_poisons_the_pool_and_invalidates_psram_constants_per_run():
    source = _render_aot(
        transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS, stream_evidence=True
    )
    block = _evidence_block(source)
    assert source.count(_STREAM_EVIDENCE) == 1
    timed = source.index("uint32_t infer_start_cyc = DWT->CYCCNT;")
    assert source.rindex("g_hb_ops_since = 0;", 0, timed) < source.index(block) < timed
    assert "memset(hpx_arena_buf_2, 0x5A, 48U);" in block
    assert block.count("memset(") == 1
    assert block.count("am_hal_cachectrl_dcache_invalidate(") == 1
    assert "hpx_arena_buf_1, 4100U" in block
    assert '#ifndef fake_num_dma_units\n#error "profiling.stream_evidence' in source
    assert 'hpx_printf("HPX_STREAM_EVIDENCE=1\\n");' in source


def test_stream_evidence_on_an_unstreamed_module_invalidates_its_psram_constants():
    """The reference capture has no pool: it takes the same cold-constant regime,
    with nothing to poison and no stream facts required."""
    regions = [r for r in _POOL_REGIONS if r["role"] != "pool"]
    source = _render_aot(
        transport="rtt", arena_region="psram", arena_regions=regions, stream_evidence=True
    )
    block = _evidence_block(source)
    assert "memset(" not in block and "0x5A" not in source
    assert block.count("am_hal_cachectrl_dcache_invalidate(") == 1
    assert "#ifndef fake_num_dma_units" not in source
    assert 'hpx_printf("HPX_STREAM_EVIDENCE=1\\n");' in source


def test_without_stream_evidence_no_run_is_poisoned_or_invalidated():
    source = _render_aot(transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS)
    assert _STREAM_EVIDENCE not in source
    assert "am_hal_cachectrl_dcache_invalidate(&hpx_range" not in source
    assert "0x5A" not in source
    assert "HPX_STREAM_EVIDENCE" not in source


def test_the_stream_evidence_block_runs_what_it_says(tmp_path: Path):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render_aot(
        transport="rtt", arena_region="psram", arena_regions=_POOL_REGIONS, stream_evidence=True
    )
    program = (
        r"""
#include <cstdint>
#include <cstdio>
#include <cstring>
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
        + _evidence_block(source)
        + r"""
    int poisoned = 0;
    for (uint8_t byte : pool) poisoned += byte == 0x5A;
    std::printf("%d %d %d %d\n", poisoned, invalidations,
                (int)(invalidated[0].ui32StartAddr == (uint32_t)(uintptr_t)constants),
                (int)invalidated[0].ui32Size);
    return 0;
}
"""
    )
    path = tmp_path / "evidence.cc"
    path.write_text(program, encoding="utf-8")
    executable = tmp_path / "evidence.exe"
    subprocess.run([compiler, "-std=gnu++17", str(path), "-o", str(executable)], check=True)
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "48 1 1 4100"


def _callback(source: str) -> str:
    start = source.index("static void model_operator_cb(")
    return source[start : source.index("\n}\n", start)]


def test_evidence_samples_sit_outside_every_counter_window():
    """Each operator's and unit's counters run from hpx_pmu_restart to
    hpx_pmu_read_layer; the DMA samples are taken before the restart and after
    the read, so no sample is counted."""
    cb = _callback(_render_aot(transport="rtt", arena_regions=_PROBE_REGIONS))
    issue = cb[cb.index("if (state == fake_op_state_transfer_issue_started) {") :]
    issue_start, issue_finish = issue.split("} else {", 1)
    assert issue_start.index("hpx_transfer_sample()") < issue_start.index("hpx_pmu_restart();")
    assert (
        issue_finish.index("hpx_pmu_read_layer(")
        < issue_finish.index("g_transfer_unit_refills[op] =")
        < issue_finish.index("hpx_transfer_sample()")
    )
    run_sample = cb.index("g_transfer_op_overlap[g_num_layers] = hpx_transfer_sample();")
    run_started = cb.index("if (state == fake_op_state_run_started) {")
    assert run_sample < run_started < cb.index("hpx_pmu_restart();", run_started)
    finished = cb.index("} else if (state == fake_op_state_run_finished) {")
    assert (
        finished
        < cb.index("hpx_pmu_read_layer(", finished)
        < cb.index("g_layers[idx].counters[i] = g_pmu_cfg.counter[i].counterValue;", finished)
        < cb.index("hpx_transfer_ends(g_transfer_op_overlap[idx], hpx_transfer_sample())", finished)
    )


def _probe_capture() -> list[str]:
    """A probe capture in the order the rendered firmware prints it."""
    lines = [
        "--- HPX_START ---",
        "HPX_ENGINE=helia_aot",
        "HPX_ITERATIONS=2",
        "HPX_NUM_PRESETS=1",
        "HPX_PRESETS=Cache",
        "HPX_INPUT_FILL=lcg",
        "HPX_TRANSFER_PROBE=301,321",
        "HPX_TRANSFER_RECOVERY=0,0,0,0",
        "--- HPX_PRESET Cache ---",
    ]
    for iteration, cycles in enumerate(("100", "102")):
        lines += [
            f"--- HPX_ITER {iteration} ---",
            '"Layer","Op","ARM_PMU_CPU_CYCLES","overflow"',
            f"0,FULLY_CONNECTED:0,{cycles},0",
            f"1,FULLY_CONNECTED:1,{cycles},0",
            "HPX_TRANSFER_OVERLAP=0:x/1,1:0/0;0:1/1,1:0/0",
            "HPX_TRANSFER_STATS=3,1,0,2,7,1",
            "HPX_OUTPUT_0_FNV1A64=0012345678901234",
        ]
    lines += ["HPX_OUTPUT_0_HEX=00ff", "HPX_INPUT_0_FNV1A64=00000000000000a1", "--- HPX_END ---"]
    return lines


def test_the_firmware_prints_the_probe_lines_before_the_first_pass():
    source = _two_pass_probe_render()
    first_pass = source.index('hpx_printf("\\n--- HPX_PRESET ')
    assert source.index("--- HPX_START ---") < source.index("HPX_TRANSFER_PROBE=") < first_pass
    assert source.index("HPX_TRANSFER_RECOVERY=") < first_pass


def test_a_probe_capture_parses_with_every_counted_iteration_intact():
    from helia_profiler.capture.parser import parse_firmware_output

    result = parse_firmware_output(_probe_capture())
    (preset,) = result.presets.values()
    assert len(preset.iterations) == 2
    assert [layer.cycles for layer in result.layers] == [101, 101]
