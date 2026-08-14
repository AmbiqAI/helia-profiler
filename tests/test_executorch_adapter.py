from __future__ import annotations

from pathlib import Path

import jinja2
import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.executorch import ExecuTorchAdapter
from helia_profiler.errors import EngineError


def _source_tree(tmp_path: Path) -> Path:
    root = tmp_path / "nsx-executorch"
    (root / "nsx").mkdir(parents=True)
    (root / "version.txt").write_text("1.3.0\n", encoding="utf-8")
    (root / "nsx" / "nsx-module.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    return root


def _config(tmp_path: Path, source: Path, **engine_config):
    model = tmp_path / "model.pte"
    model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 32)
    values = {
        "source_path": str(source),
        "method_arena_size": 1024,
        "planned_arena_size": 2048,
        "temporary_arena_size": 512,
        "input_size": 64,
        "output_size": 16,
        "cortex_m_ops": ["cortex_m::quantized_conv2d.out"],
        "portable_ops": ["aten::clamp.out"],
    }
    values.update(engine_config)
    return load_config(
        None,
        {
            "model": {"path": str(model), "arena_size": 2048},
            "engine": {"type": "executorch", "backend": "arm", "config": values},
        },
    )


def test_adapter_creates_named_source_alias_and_profiled_module(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source), tmp_path / "work"
    )

    alias = tmp_path / "work" / "engine" / "executorch"
    assert alias.is_symlink()
    assert alias.resolve() == source.resolve()
    assert artifacts.cmake_vars == {
        "NSX_EXECUTORCH_ENABLE_PROFILING": "ON",
        "NSX_EXECUTORCH_CMSIS_NN_PROVIDER": "arm",
        "NSX_EXECUTORCH_CORTEX_M_SELECT_OPS_LIST": (
            "cortex_m::quantized_conv2d.out"
        ),
        "NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST": "aten::clamp.out",
    }
    assert artifacts.executorch_planned_arena_size == 2048
    wrapper = artifacts.extra_modules[-1].path / "CMakeLists.txt"
    assert f'add_subdirectory("{alias.as_posix()}/nsx"' in wrapper.read_text()


def test_adapter_rejects_incomplete_io_contract(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, output_size=0)
    with pytest.raises(EngineError, match="output_size"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_executorch_template_has_counter_health_and_true_overflow_mask():
    env = jinja2.Environment(
        loader=jinja2.PackageLoader("helia_profiler.firmware", "templates"),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=jinja2.StrictUndefined,
    )
    out = env.get_template("main_executorch.cc.j2").render(
        cmsis_device_header="apollo510.h",
        pmu_max_ops=4096,
        executorch_planned_arena_size=2048,
        executorch_method_arena_size=1024,
        executorch_temporary_arena_size=512,
        executorch_input_size=64,
        executorch_output_size=16,
        arena_region="tcm",
        weights_region="mram",
        transport="rtt",
        power_sync_enabled=False,
        extreme_mode=False,
        manages_shared_ssram_power=True,
        ssram_full_power_enum="AM_HAL_PWRCTRL_SRAM_3M",
        perf_mode_symbol="NSX_PERF_LOW",
        perf_mode_mhz=96,
        iterations=3,
        warmup=1,
        clean_iters=3,
        pmu_passes=[
            {
                "name": "cpu_0",
                "event_ids": ["0x0011U", "0x0008U"],
                "counter_names": ["ARM_PMU_CPU_CYCLES", "ARM_PMU_INST_RETIRED"],
                "num_counters": 2,
            }
        ],
        pmu_pass_names=["cpu_0"],
        psram_clock_hz=48_000_000,
    )

    assert "HPX_PMU_INIT_STATUS" in out
    assert "HPX_PMU_SELFTEST_CPU_CYCLES" in out
    assert 'hpx_printf("HPX_READY\\n")' in out
    assert "HPX_ERROR=operator_count_exceeds_capacity" in out
    assert '#include "am_hal_pwrctrl.h"' in out
    assert "g_logical_overflow_mask |= 1UL << (2 * i + 1)" in out
    end_operator = out[out.index("static void end_operator") :]
    assert end_operator.index("ARM_PMU_Get_CNTR_OVS()") < end_operator.index(
        "nsx_pmu_get_counters(&g_pmu_cfg)"
    )
    assert "result.execution_cycles" in out
