from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.sweep_evidence import _timing, admit_measurement
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


def test_admission_joins_all_timed_runs_to_original_plan(tmp_path: Path):
    pytest.importorskip("helia_aot")
    run = bundle(tmp_path / "run", cycles={0: 45, 1: 23, 2: 12})
    (run / "firmware_output.txt").write_text(_RAW)
    publish_manifest(run)
    result = admit_measurement(run, plan_sha256="f" * 64, reference_outputs=_REFERENCES)
    assert result["plan_sha256"] == "f" * 64
    assert result["cycles"] == 103
    assert isinstance(result["conditions"], dict)
    assert result["conditions"]["cache_regime"] == "cold"
    assert result["dma_failures"] == 0
    assert result["output_identical"] is True
    assert result["energy_uj"] is None
    assert result["per_operator"]


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ("101,0,cold", "0,0,cold"),
        ("101,0,cold", "101,321,cold"),
        ("HPX_AOT_RUN_EVIDENCE=101,0,cold", ""),
        ("101,0,cold", "101,0,warm"),
        ("0123456789abcdef", "1123456789abcdef"),
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
    assert _timing(_RAW.replace("DCACHE=on", "DCACHE=off"), _REFERENCES, "off") == (103, None)


def test_static_samples_are_timed_and_checked_without_operator_callbacks():
    static = _RAW.replace("HPX_NUM_PRESETS=1", "HPX_NUM_PRESETS=0").replace(
        "HPX_PRESETS=Cache", "HPX_PRESETS=\nHPX_PER_LAYER_PROFILING=0"
    )
    static = "\n".join(
        line
        for line in static.splitlines()
        if not line.startswith(("--- HPX_ITER", "--- HPX_PRESET"))
    )
    static = static.replace(",cold", ",warm")
    assert _timing(static, _REFERENCES, "on") == (103, "warm")
    with pytest.raises(ValueError):
        _timing(static.replace(_REFERENCES[0], "bad", 1), _REFERENCES, "on")
