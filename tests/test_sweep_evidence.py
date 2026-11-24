from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.results import load_result_manifest
from helia_profiler.sweep_evidence import _Timing, _timing, admit_measurement
from tests.calibration_bundles import CONDITIONS, bundle
from tests.result_bundles import publish_manifest

_REFERENCES = ("0123456789abcdef", "0000000000000001")
_RAW = """--- HPX_START ---
HPX_ENGINE=helia_aot
HPX_DCACHE=on
HPX_NUM_OUTPUTS=2
HPX_ITERATIONS=2
HPX_NUM_PRESETS=1
HPX_PRESETS=Cache
--- HPX_PRESET Cache ---
--- HPX_ITER 0 ---
HPX_AOT_RUN_EVIDENCE=101,0,cold
HPX_OUTPUT_0_FNV1A64=0123456789abcdef
HPX_OUTPUT_1_FNV1A64=0000000000000001
--- HPX_ITER 1 ---
HPX_AOT_RUN_EVIDENCE=105,0,cold
HPX_OUTPUT_0_FNV1A64=0123456789abcdef
HPX_OUTPUT_1_FNV1A64=0000000000000001
HPX_CLEAN_INFER_AVG_CYCLES=32
HPX_PROFILED_INFER_COUNT=2
--- HPX_END ---"""


@pytest.mark.parametrize("matches", [True, False])
def test_admission_joins_all_timed_runs_to_original_plan(tmp_path: Path, matches: bool):
    pytest.importorskip("helia_aot")
    run = bundle(tmp_path / "run", cycles={0: 45, 1: 23, 2: 12})
    raw = _RAW if matches else _RAW.replace(_REFERENCES[1], "ffffffffffffffff", 1)
    (run / "firmware_output.txt").write_text(raw)
    publish_manifest(run)
    result = admit_measurement(run, plan_sha256="f" * 64, reference_outputs=_REFERENCES)
    assert result["plan_sha256"] == "f" * 64
    assert result["cycles"] == 103
    assert isinstance(result["conditions"], dict)
    assert result["conditions"]["cache_regime"] == "cold"
    assert result["dma_failures"] == 0
    assert result["output_identical"] is matches
    assert result["energy_uj"] is None
    assert result["per_operator"]


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("101,0,cold", "0,0,cold"),
        ("HPX_AOT_RUN_EVIDENCE=101,0,cold", ""),
        ("101,0,cold", "101,0,warm"),
        ("HPX_OUTPUT_1_FNV1A64=0000000000000001", ""),
        ("HPX_ITER 0", "HPX_ITER 1"),
        ("HPX_PROFILED_INFER_COUNT=2", "HPX_PROFILED_INFER_COUNT=1"),
        ("HPX_DCACHE=on", ""),
        ("--- HPX_END ---", ""),
    ],
)
def test_incomplete_or_failed_inference_refused(original: str, replacement: str):
    with pytest.raises(ValueError):
        _timing(_RAW.replace(original, replacement, 1), _REFERENCES, "on")


def test_no_regime_for_disabled_cache():
    assert _timing(_RAW.replace("DCACHE=on", "DCACHE=off"), _REFERENCES, "off") == _Timing(
        103, None, True
    )


def test_static_samples_are_timed_and_checked_without_operator_callbacks(tmp_path: Path):
    static = _RAW.replace("HPX_NUM_PRESETS=1", "HPX_NUM_PRESETS=0").replace(
        "HPX_PRESETS=Cache", "HPX_PRESETS=\nHPX_PER_LAYER_PROFILING=0"
    )
    static = "\n".join(
        line
        for line in static.splitlines()
        if not line.startswith(("--- HPX_ITER", "--- HPX_PRESET"))
    )
    static = static.replace(",cold", ",warm")
    assert _timing(static, _REFERENCES, "on") == _Timing(103, "warm", True)
    with pytest.raises(ValueError):
        _timing(static.replace(_REFERENCES[0], "bad", 1), _REFERENCES, "on")
    run = bundle(
        tmp_path / "model-only", cycles={}, conditions={**CONDITIONS, "weights_memory": None}
    )
    plan_path = run / "hpx_plan.json"
    plan = json.loads(plan_path.read_text())
    plan["tensors"] = [{"residency": "stream", "memory": "PSRAM"}]
    plan_path.write_text(json.dumps(plan))
    (run / "profile_results.csv").unlink()
    summary_path = run / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["per_layer_profiling"] = False
    summary["total_cycles"] = None
    summary_path.write_text(json.dumps(summary))
    (run / "firmware_output.txt").write_text(static)
    path = publish_manifest(run)
    manifest = load_result_manifest(path)
    names = {"summary.json": "hpx.summary", "run_metadata.json": "hpx.run-metadata"}
    artifacts = tuple(
        replace(item, name=names[item.path], optional=False) if item.path in names else item
        for item in manifest.artifacts
    )
    replace(manifest, bundle_type="profile-model-only", artifacts=artifacts).write(path)
    result = admit_measurement(run, plan_sha256="e" * 64, reference_outputs=_REFERENCES)
    assert result["cycles"] == 103
    assert result["per_operator"] == {}
    assert isinstance(result["conditions"], dict)
    assert result["conditions"]["weights_memory"] == "psram"


@pytest.mark.parametrize(
    ("tensors", "expected"),
    [
        ([{"residency": "stream", "memory": "PSRAM"}] * 2, "psram"),
        (
            [
                {"residency": "pin", "memory": "PSRAM", "destination_memory": "DTCM"},
                {"residency": "stream", "memory": "PSRAM"},
                {"residency": "direct", "memory": "PSRAM"},
            ],
            "psram",
        ),
        ([{"residency": "pin", "memory": "MRAM", "destination_memory": "DTCM"}], "mram"),
        (
            [{"residency": "direct", "memory": "MRAM"}, {"residency": "stream", "memory": "PSRAM"}],
            None,
        ),
        ([{"residency": "pin", "memory": "PSRAM"}], None),
        ([{"residency": "stream", "memory": "UNKNOWN"}], None),
        ([], None),
    ],
)
@pytest.mark.parametrize("recorded_memory", [None, "dtcm"])
def test_sweep_records_the_converted_constant_source(tmp_path, tensors, expected, recorded_memory):
    pytest.importorskip("helia_aot")
    run = bundle(
        tmp_path / "table",
        cycles={0: 1, 1: 2, 2: 3},
        conditions={**CONDITIONS, "weights_memory": recorded_memory},
    )
    path = run / "hpx_plan.json"
    plan = json.loads(path.read_text())
    plan["tensors"] = tensors
    path.write_text(json.dumps(plan))
    (run / "firmware_output.txt").write_text(_RAW)
    publish_manifest(run)
    if expected is None:
        with pytest.raises(ValueError, match="Converted plan"):
            admit_measurement(run, plan_sha256="f" * 64, reference_outputs=_REFERENCES)
    else:
        result = admit_measurement(run, plan_sha256="f" * 64, reference_outputs=_REFERENCES)
        assert isinstance(result["conditions"], dict)
        assert result["conditions"]["weights_memory"] == expected


def test_latched_provider_error_is_diagnostic_without_an_exact_failure_count():
    raw = _RAW.replace(",0,cold", ",321,cold")
    with pytest.raises(
        ValueError, match="Cache sample 0.*last-error status 321.*count unavailable"
    ):
        _timing(raw, _REFERENCES, "on")


def test_unstreamed_provider_and_wrapped_dwt_are_distinguished(tmp_path: Path):
    import shutil
    import subprocess

    from tests.contracts.test_firmware_render_snapshots import _render

    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render("apollo4p", "rtt", "helia-aot", overrides={"sweep_evidence": True})
    assert 'hpx_printf("HPX_DCACHE=off' in source
    helper = source[
        source.index("static bool hpx_transfer_clock_valid") : source.index(
            "static void hpx_print_output_digests"
        )
    ]
    harness = """#include <cstdint>
#include <cstdio>
#define HPX_STIMER_HZ 32768U
#define hpx_printf std::printf
static uint32_t SystemCoreClock = 96000000U;
"""
    harness += (
        helper
        + """
int main() {
    hpx_sweep_report(96000U, 33U, true, true);
    hpx_sweep_report(96000U, 1466050U, true, true);
    hpx_sweep_report(1000U, 0U, true, true);
    hpx_sweep_report(1000U, 0U, false, true);
    hpx_sweep_report(96000U, 0U, true, true);
    hpx_sweep_report(0U, 0U, true, true);
}
"""
    )
    cpp = tmp_path / "clock.cc"
    cpp.write_text(harness)
    exe = tmp_path / "clock.exe"
    subprocess.run([compiler, str(cpp), "-o", str(exe)], check=True)
    output = subprocess.check_output([str(exe)], text=True).splitlines()
    assert [line.split(",")[:2] for line in output] == [
        [f"HPX_AOT_RUN_EVIDENCE={cycles}", "0"] for cycles in (96000, 0, 1000, 0, 0, 0)
    ]


def _run_firmware_fragment(tmp_path: Path, program: str) -> None:
    import shutil
    import subprocess

    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = tmp_path / "firmware.cc"
    source.write_text(program)
    binary = tmp_path / "firmware.exe"
    subprocess.run([compiler, str(source), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)


def test_sweep_pmu_callbacks_preserve_whole_run_clock(tmp_path: Path):
    from tests.contracts.test_firmware_render_snapshots import _render

    source = _render("apollo510", "rtt", "helia-aot", overrides={"sweep_evidence": True})
    callbacks = source[
        source.index("static bool g_sweep_pmu_ready;") : source.index("// g_layers is")
    ]
    _run_firmware_fragment(
        tmp_path,
        """
#include <cassert>
#include <cstdint>
struct Counter { uint32_t counterValue; };
struct nsx_pmu_config_t { Counter counter[2]; };
static uint32_t elapsed, overflow, enabled = 0x8000000f;
void ARM_PMU_EVCNTR_ALL_Reset() { elapsed += 7; }
void ARM_PMU_Set_CNTR_OVS(uint32_t mask) { overflow &= ~mask; ++elapsed; }
uint32_t ARM_PMU_Get_CNTR_OVS() { ++elapsed; return overflow; }
void ARM_PMU_CNTR_Disable(uint32_t mask) { enabled &= ~mask; ++elapsed; }
void ARM_PMU_CNTR_Enable(uint32_t mask) { enabled |= mask; ++elapsed; }
uint32_t ARM_PMU_Get_EVCNTR(int i) {
    assert(enabled == 0x80000000);
    ++elapsed;
    return 1 + i;
}
"""
        + callbacks
        + """
int main() {
    nsx_pmu_config_t cfg{};
    g_sweep_pmu_ready = true;
    for (int op = 0; op < 100; ++op) {
        hpx_pmu_restart();
        elapsed += 20;
        overflow = 1; // A carry into a CHAIN register is an ordinary 32-bit count.
        assert(!hpx_pmu_read_layer(&cfg, 2));
        assert(enabled == 0x8000000f);
        assert(cfg.counter[0].counterValue == 0x00020001U);
        assert(cfg.counter[1].counterValue == 0x00040003U);
    }
    assert(elapsed == 3500);
    overflow = 8;
    assert(hpx_pmu_read_layer(&cfg, 2));
    hpx_pmu_restart();
    assert(!hpx_pmu_read_layer(&cfg, 2));
    g_sweep_pmu_ready = false;
    assert(hpx_pmu_read_layer(&cfg, 2));
}
""",
    )


def test_static_sweep_usb_timer_and_progress(tmp_path: Path):
    from tests.contracts.test_firmware_render_snapshots import _render

    source = _render(
        "apollo510",
        "usb_cdc",
        "helia-aot",
        overrides={
            "sweep_evidence": True,
            "per_layer_profiling": False,
            "pmu_passes": [],
            "pmu_pass_names": [],
            "warmup": 5,
            "iterations": 3,
        },
    )
    start = source.rindex(
        "for (int w = 0;", 0, source.index("HPX_HEARTBEAT phase=model_only_warmup iter=")
    )
    loops = source[start : source.index("// The last run's outputs", start)]
    _run_firmware_fragment(
        tmp_path,
        """
#include <cassert>
#include <cstdint>
#include <cstring>
struct Input { void* data; size_t size; };
struct Model { Input inputs[1]; } fake_model_ctx;
constexpr int fake_num_inputs = 0;
static int invokes, reports, beats, fail_at, hpx_aot_status;
static bool polling = true;
static struct { uint32_t CYCCNT; } clock_reg;
#define DWT (&clock_reg)
void usb_timer_pause() { assert(polling); polling = false; }
void usb_timer_resume() { assert(!polling); polling = true; }
uint32_t hpx_stimer_ticks() { assert(!polling); return DWT->CYCCNT; }
int fake_model_run(Model*) {
    assert(beats == (invokes < 5 ? invokes : 5));
    assert(polling == (invokes < 5));
    DWT->CYCCNT += 100;
    return ++invokes == fail_at;
}
void hpx_printf(const char*, int w) { assert(polling && w == beats); ++beats; }
void hpx_aot_check_runs() {
    assert(polling);
    if (hpx_aot_status) throw hpx_aot_status;
}
void hpx_sweep_report(uint32_t cycles, uint32_t ticks, bool, bool) {
    assert(polling && cycles == 100 && ticks == 100);
    ++reports;
}
void hpx_print_output_digests() { assert(polling); }
void measure() {
    uint32_t profiled_infer_cycles = 0, profiled_infer_count = 0;
    const bool sweep_clock_ready = true;
"""
        + loops
        + """
}
int main() {
    measure();
    assert(beats == 5 && reports == 3);
    beats = invokes = reports = 0;
    fail_at = 7;
    try { measure(); assert(false); } catch (int status) { assert(status == 1); }
    assert(beats == 5 && reports == 1 && polling);
}
""",
    )
