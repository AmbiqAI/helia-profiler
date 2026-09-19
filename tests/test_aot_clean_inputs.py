"""AOT clean-loop caller ownership and workload reporting."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from helia_profiler.config import EngineType
from helia_profiler.evaluation import compare_runs
from helia_profiler.firmware.workload import measured_clean_workload
from helia_profiler.report.summary import _write_summary
from helia_profiler.results import PowerRun, PowerRunPlan
from helia_profiler.results.run_summary import RunSummary, load_run_summary
from tests.test_compare import _write_run
from tests.test_report import _gated_power_ctx
from tests.test_template_render import _render_aot


def _loop(source: str) -> str:
    start = source.index("for (int iter = 0; iter < clean_iters_n; iter++)")
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


@pytest.mark.parametrize("timer", ["dwt", "stimer"])
@pytest.mark.parametrize("power", [False, True])
def test_rendered_clean_loop_restores_every_input(tmp_path: Path, timer: str, power: bool):
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    source = _render_aot(clean_window_timer=timer, power_window_timer=timer, power_only=power)
    loop = _loop(source)
    invoke = "fake_model_run(&fake_model_ctx);"
    reset_start = loop.index("for (int i = 0; i < fake_num_inputs; i++)")
    reset_end = loop.index("}", reset_start) + 1
    assert reset_end < loop.index(invoke)
    if timer == "dwt":
        assert loop.index("uint32_t t0") < reset_start
    gate_start = source.index("hpx_sync_window_begin();")
    assert gate_start < source.index(loop) < source.index("hpx_sync_window_end();", gate_start)
    preamble = r"""
#include <cstdint>
#include <cstring>
struct Clock { uint32_t CYCCNT=0; } clock_state;
Clock* DWT=&clock_state;
struct Tensor { void* data; unsigned size; };
struct Context { Tensor inputs[2]; };
unsigned char storage[8];
const int fake_num_inputs=2;
Context fake_model_ctx={{{storage,3},{storage+3,5}}};
int calls=0, bad=0, refills=0;
bool aliases;
void* refill(void* dst, int value, unsigned size) {
    ++refills;
    DWT->CYCCNT += 7;
    return std::memset(dst,value,size);
}
void fake_model_run(Context* ctx) {
    for (auto input: ctx->inputs)
        for(unsigned i=0;i<input.size;++i) bad += ((unsigned char*)input.data)[i] != 0;
    ++calls;
    if(aliases) std::memset(storage,91,sizeof(storage));
    DWT->CYCCNT += 100;
}
int main(int argc,char**) {
    aliases=argc>1;
    std::memset(storage,37,sizeof(storage));
    uint32_t clean_count=0, clean_stalled_iters=0, clean_partial_iters=0;
    const uint32_t clean_low_cyc=1;
    uint64_t clean_cycles=0;
    const int clean_iters_n=3;
#define memset refill
"""
    for omit_reset in (False, True):
        body = loop[:reset_start] + loop[reset_end:] if omit_reset else loop
        expected_cycles = "342" if timer == "dwt" else "0"
        code = (
            preamble
            + body
            + (
                "\nreturn bad || calls!=3 || clean_count!=3 || refills!=6 || "
                f"clean_cycles!={expected_cycles};\n}}\n"
            )
        )
        path = tmp_path / f"loop-{omit_reset}.cc"
        path.write_text(code, encoding="utf-8")
        executable = path.with_suffix(".exe")
        subprocess.run(
            [compiler, "-std=c++17", str(path), "-o", str(executable)],
            check=True,
            capture_output=True,
        )
        for alias in (False, True):
            result = subprocess.run([str(executable)] + (["alias"] if alias else []), timeout=10)
            assert (result.returncode == 0) is (not omit_reset)


@pytest.mark.parametrize("mode", ["auto", "fixed"])
@pytest.mark.parametrize("timer", ["dwt", "stimer"])
def test_clean_calibration_includes_refill_but_profile_does_not(mode: str, timer: str):
    source = _render_aot(window_mode=mode, clean_window_timer=timer)
    start = source.index("uint32_t wt0 = DWT->CYCCNT;")
    end = source.index("uint32_t wc =", start)
    assert "memset(" in source[start:end]
    start = source.index("uint32_t infer_start_cyc = DWT->CYCCNT;")
    end = source.index("profiled_infer_cycles +=", start)
    assert "memset(" not in source[start:end]
    reset = source.rindex("memset(", 0, start)
    assert "fake_model_run(" not in source[reset:start]


@pytest.mark.parametrize("firmware_mode", ["dedicated", "shared"])
def test_workload_written_from_selected_render(
    tmp_path: Path, firmware_mode: Literal["dedicated", "shared"]
):
    ctx = _gated_power_ctx(tmp_path, clean_infer_count=3, clean_infer_avg_us=100, duration_s=0.0003)
    ctx.config = replace(ctx.config, engine=replace(ctx.config.engine, type=EngineType.HELIA_AOT))
    existing = ctx.power_run
    ctx.power_run = PowerRun(
        plan=PowerRunPlan(
            firmware_mode=firmware_mode, inference_count=3, count_source="configured"
        ),
        observation=existing.observation if existing else None,
    )
    ctx.firmware_dir = tmp_path / "firmware"
    src = ctx.firmware_dir / "src"
    src.mkdir(parents=True)
    (src / "main.cc").write_text(_render_aot(), encoding="utf-8")
    (src / "main_power.cc").write_text(_render_aot(power_only=True), encoding="utf-8")
    data = json.loads(_write_summary(ctx, tmp_path).read_text())
    identity = "aot_raw_zero_refill_included_v1"
    assert data["latency"]["clean_workload"] == identity
    assert data["power"]["clean_workload"] == identity
    assert RunSummary.from_dict(data).to_dict()["power"]["clean_workload"] == identity
    assert RunSummary.from_dict(data).to_dict()["latency"]["clean_workload"] == identity
    (src / ("main_power.cc" if firmware_mode == "dedicated" else "main.cc")).write_text(
        _render_aot(clean_window_probe="busy_loop", power_only=firmware_mode == "dedicated"),
        encoding="utf-8",
    )
    assert measured_clean_workload(ctx, power=True) is None
    (src / "main.cc").unlink()
    assert measured_clean_workload(ctx) is None


@pytest.mark.parametrize(
    "left,right,comparable",
    [
        (None, None, True),
        (123, None, True),
        (["bad"], None, True),
        ({"bad": True}, "aot_raw_zero_refill_included_v1", False),
        ("aot_raw_zero_refill_included_v1", None, False),
        (None, "aot_raw_zero_refill_included_v1", False),
        ("aot_raw_zero_refill_included_v1", "other", False),
        ("aot_raw_zero_refill_included_v1", "aot_raw_zero_refill_included_v1", True),
    ],
)
def test_power_workload_comparison_preserves_profile_metrics(
    tmp_path: Path, left, right, comparable: bool
):
    for name, identity in [("old", left), ("new", right)]:
        power = {
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "energy_j": 0.01,
        }
        if identity is not None:
            power["clean_workload"] = identity
        _write_run(
            tmp_path / name,
            toolchain="gcc",
            total_cycles=1000,
            avg_us=10,
            layer_cycles=[1000],
            power=power,
        )
        summary_path = tmp_path / name / "summary.json"
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        data["schema_version"] = 5 if name == "old" else 6
        summary_path.write_text(json.dumps(data), encoding="utf-8")
        assert load_run_summary(summary_path).schema_version == data["schema_version"]
    result = compare_runs(tmp_path / "old", tmp_path / "new")
    schema_row = next(row for row in result.config_rows if row.key == "run_summary_schema_version")
    assert (schema_row.baseline, schema_row.candidate, schema_row.status) == (5, 6, "diff")
    assert result.comparability.power_metrics_comparable is comparable
    assert result.comparability.run_metrics_comparable
    assert result.comparability.layers_comparable
    assert any(not metric.name.startswith("power.") for metric in result.metrics)
    assert any(metric.name.startswith("power.") for metric in result.metrics) is comparable


@pytest.mark.parametrize(
    "identity", [None, "aot_raw_zero_refill_included_v1", 123, ["bad"], {"bad": True}]
)
def test_validation_preserves_measured_workload(tmp_path: Path, monkeypatch, identity):
    from helia_profiler.validation import report, runner
    from helia_profiler.validation.matrix import BOARDS, MODELS, CaseSpec

    case = CaseSpec(
        model=MODELS["kws"], engine=EngineType.HELIA_AOT, power=False, board=BOARDS["apollo510_evb"]
    )
    case_dir = tmp_path / case.case_id
    case_dir.mkdir()
    (case_dir / "aot_operator_manifest.json").write_text(json.dumps([{"op": "REVERSE_V2"}]))
    latency = {"device_clean_infer_avg_cycles": 123, "device_clean_infer_avg_us": 4}
    power = {"energy_j": 0.001}
    if identity is not None:
        latency["clean_workload"] = identity
        power["clean_workload"] = identity
    (case_dir / "summary.json").write_text(
        json.dumps({"layers": 1, "total_cycles": 100, "latency": latency, "power": power})
    )
    expected = identity if isinstance(identity, str) else None
    summary = load_run_summary(case_dir / "summary.json")
    assert summary.latency is not None and summary.power is not None
    assert summary.latency.clean_workload == expected
    assert summary.power.clean_workload == expected
    monkeypatch.setattr(
        runner,
        "_run_profile_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(["inert"], 0, stdout="", stderr=""),
    )
    result = runner.run_case(
        case=case, repo_root=tmp_path, output_root=tmp_path, timeout_s=1, in_process=False
    )
    assert result.total_cycles == 123
    assert result.latency_avg_us == 4
    assert result.clean_workload == expected
    assert result.power_workload == expected
    plain = result.to_dict()
    manifest = report.build_manifest([result], tmp_path, repo_root=tmp_path)
    markdown = report.render_markdown([result])
    if expected is None:
        assert "clean_workload" not in plain
        assert "power_workload" not in plain
        assert "refill" not in markdown
    else:
        assert plain["clean_workload"] == expected
        assert manifest["cases"][0]["metrics"]["clean_workload"] == expected
        assert manifest["cases"][0]["metrics"]["power_workload"] == expected
        assert "clean timing includes input refill" in markdown
        assert "power includes input refill" in markdown

    report_path = tmp_path / "validation.json"
    report_path.write_text(json.dumps({"cases": [plain]}))
    loaded = report.load_validation_report(report_path)
    assert loaded.cases[0].clean_workload == expected
    assert loaded.cases[0].power_workload == expected
    from rich.console import Console
    from helia_profiler.console import HpxConsole

    console = HpxConsole()
    console._console = Console(record=True, width=240)
    console.print_validation(loaded)
    text = console._console.export_text()
    assert ("refill included" in text) is (expected is not None)
