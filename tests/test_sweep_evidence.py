from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.results import load_result_manifest
from helia_profiler.sweep_evidence import _Timing, _timing, admit_measurement
from tests.calibration_bundles import bundle
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
    run = bundle(tmp_path / "model-only", cycles={})
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
    hpx_sweep_report(96000U, 33U, true);
    hpx_sweep_report(96000U, 1466050U, true);
}
"""
    )
    cpp = tmp_path / "clock.cc"
    cpp.write_text(harness)
    exe = tmp_path / "clock.exe"
    subprocess.run([compiler, str(cpp), "-o", str(exe)], check=True)
    output = subprocess.check_output([str(exe)], text=True).splitlines()
    assert output[0].startswith("HPX_AOT_RUN_EVIDENCE=96000,0,")
    assert output[1].startswith("HPX_AOT_RUN_EVIDENCE=0,0,")
