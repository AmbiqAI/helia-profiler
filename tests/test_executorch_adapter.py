from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from helia_profiler.firmware import _jinja_env
from helia_profiler.firmware.context import resolve_window_timer
import pytest

import helia_profiler.engines.executorch as executorch_mod
from helia_profiler.config import load_config
from helia_profiler.engines.executorch import ExecuTorchAdapter
from helia_profiler.errors import EngineError


def _render_executorch_template(**overrides) -> str:
    """Render ``main_executorch.cc.j2`` through the production env (#119).

    Since #154 phase 4 that template is a child of ``_main_base.cc.j2``, so a
    render needs the shared skeleton's whole variable set, not just the
    ExecuTorch-specific half these tests care about.  The defaults below are
    the Apollo5/Cortex-M55 shape ExecuTorch actually ships on (it is the only
    family the engine supports); callers override whatever their assertion is
    about.

    This helper deliberately does NOT build its own Jinja environment — the
    production ``_jinja_env`` is the only legal way to render these templates
    (see ``test_no_test_builds_a_look_alike_env_over_production_templates``).
    The full per-SoC x transport matrix lives in
    ``tests/contracts/test_firmware_render_snapshots.py``; this is the
    minimum context for the feature-level assertions here.
    """
    kwargs: dict = {
        # ExecuTorch engine inputs (EngineContext half of the render context).
        "cmsis_device_header": "apollo510.h",
        "pmu_max_ops": 4096,
        "executorch_planned_arena_size": 2048,
        "executorch_method_arena_size": 1024,
        "executorch_temporary_arena_size": 512,
        "executorch_input_size": 64,
        "executorch_output_size": 16,
        "executorch_planned_arena_region": "tcm",
        "executorch_method_arena_region": "tcm",
        "executorch_temporary_arena_region": "tcm",
        "executorch_io_region": "tcm",
        "engine_wire_name": "executorch",
        "printf_linkage": "",
        # Shared skeleton inputs.
        "arena_region": "tcm",
        "weights_region": "mram",
        "transport": "rtt",
        "usb_serial_marker": None,
        "usb_serial_product": "NSX HPX Profiler",
        "perf_mode_symbol": "NSX_PERF_LOW",
        "perf_mode_mhz": 96,
        "apollo3_burst": False,
        "iterations": 3,
        "warmup": 1,
        "clean_warmup": 1,
        "clean_iters": 3,
        "window_mode": "fixed",
        "window_target_ms": 1000,
        "window_min": 10,
        "window_max": 2000,
        "clean_window_probe": "infer",
        "clean_window_trace": False,
        "pmu_passes": [],
        "pmu_pass_names": [],
        "power_sync_enabled": False,
        "sync_gpio_pin": 22,
        "lockstep": False,
        "state_gpio_pin": 23,
        "go_gpio_pin": 24,
        "extreme_mode": False,
        "heartbeat_enabled": True,
        "heartbeat_every_n_ops": 4,
        "heartbeat_every_ms": 0,
        "psram_clock_hz": 48_000_000,
        "force_shared_sram": False,
        # Apollo5 capability shape (ExecuTorch is Cortex-M55 only).
        "has_armv8m_pmu": True,
        "has_dcache": True,
        "has_radio_subsystem": False,
        "manages_shared_ssram_power": True,
        "ssram_full_power_enum": "AM_HAL_PWRCTRL_SRAM_3M",
        "clean_window_timer": "stimer",
        "power_window_timer": "stimer",
        "clean_window_needs_probe_attach": False,
        "gate_debug_domain_in_window": True,
        "broad_peripheral_shutdown": False,
        "crypto_otp_shutdown": True,
    }
    kwargs.update(overrides)
    kwargs.update(
        resolve_window_timer(
            clean_window_probe=str(kwargs["clean_window_probe"]),
            power_only=False,
            power_window_timer=str(kwargs["power_window_timer"]),
            clean_window_timer=str(kwargs["clean_window_timer"]),
        )
    )
    return _jinja_env.get_template("main_executorch.cc.j2").render(**kwargs)


def _source_tree(tmp_path: Path) -> Path:
    # Mirrors nsx-executorch's real checkout-root layout: nsx-module.yaml
    # and CMakeLists.txt live at the root (not a retired nsx/ subdirectory).
    root = tmp_path / "nsx-executorch"
    root.mkdir(parents=True)
    (root / "version.txt").write_text("0.1.0\n", encoding="utf-8")
    (root / "nsx-module.yaml").write_text(
        "schema_version: 1\nmodule:\n  name: nsx-executorch\n", encoding="utf-8"
    )
    (root / "CMakeLists.txt").write_text(
        "if(TARGET nsx::executorch)\n  return()\nendif()\n", encoding="utf-8"
    )
    (root / "external" / "executorch").mkdir(parents=True)
    (root / "external" / "executorch" / "version.txt").write_text("1.3.0\n", encoding="utf-8")
    (root / "tools" / "python" / "torchgen").mkdir(parents=True)
    (root / "tools" / "python" / "torchgen" / "__init__.py").write_text("", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _fake_source_refs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        executorch_mod,
        "_checkout_commit",
        lambda path: (
            "3a97429b0ce0c192861fc3e3729fb81432fd22cf"
            if path.parent.name == "external" and path.name == "executorch"
            else (
                "6d21a6f821fb72541173a6c4d05d83329fa74f7c"
                if path.name.startswith("arm-cmsis-nn-")
                else (
                    "631726420b04860a5c4236956a3741ff5a96bd7f"
                    if path.name.startswith("ns-cmsis-nn-")
                    else "62b22f96dc49e2c28eb20aee0f15ebb7ad1c1d59"
                )
            )
        ),
    )
    monkeypatch.setattr(
        executorch_mod,
        "_gitlink_commit",
        lambda _path, _submodule: "3a97429b0ce0c192861fc3e3729fb81432fd22cf",
    )


def _config(tmp_path: Path, source: Path, *, backend: str = "arm", **engine_config):
    model = tmp_path / "model.pte"
    model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 32)
    values = {
        "source_path": str(source),
        "method_arena_size": 1024,
        "planned_arena_size": 2048,
        "temporary_arena_size": 512,
        "input_size": 64,
        "output_size": 16,
        "portable_ops": ["aten::clamp.out"],
    }
    values.update(engine_config)
    if values.get("source_path") is None:
        values.pop("source_path", None)
    return load_config(
        None,
        {
            "model": {"path": str(model), "arena_size": 2048},
            "engine": {"type": "executorch", "backend": backend, "config": values},
        },
    )


def test_adapter_wraps_root_layout_checkout_for_arm_provider(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")

    assert artifacts.cmake_vars["NSX_EXECUTORCH_ENABLE_PROFILING"] == "ON"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_CMSIS_NN_PROVIDER"] == "arm"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST"] == ("aten::clamp.out")
    assert artifacts.cmake_vars["Python3_EXECUTABLE"] == Path(sys.executable).absolute().as_posix()
    assert "NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT" not in artifacts.cmake_vars
    assert "NSX_EXECUTORCH_NS_CMSIS_NN_ROOT" not in artifacts.cmake_vars
    assert artifacts.executorch_planned_arena_size == 2048

    module_names = [module.name for module in artifacts.extra_modules]
    assert module_names == ["arm-cmsis-nn", "nsx-executorch"]
    provider = artifacts.extra_modules[0]
    assert provider.project == "arm-cmsis-nn"
    assert provider.local is False

    wrapper = artifacts.extra_modules[-1].path
    cmake_text = (wrapper / "CMakeLists.txt").read_text()
    # add_subdirectory(), not include(): the runtime's own CMakeLists.txt
    # references sources with paths relative to CMAKE_CURRENT_SOURCE_DIR
    # (e.g. add_library(... src/nsx_executorch.cpp)), which only resolves
    # when CMake actually descends into source_root as its own scope.
    assert f'"{source.as_posix()}"' in cmake_text
    assert "add_subdirectory(" in cmake_text
    assert "include(" not in cmake_text
    assert (wrapper / "nsx-module.yaml").read_text() == (source / "nsx-module.yaml").read_text()
    # No more work-dir symlink alias — the wrapper delegates straight to the
    # checkout's own real root layout.
    assert not (tmp_path / "work" / "engine" / "executorch").exists()


def test_adapter_selects_only_ns_provider_module(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, backend="ns"),
        tmp_path / "work",
    )

    module_names = [module.name for module in artifacts.extra_modules]
    assert module_names == ["nsx-cmsis-nn", "nsx-executorch"]

    assert artifacts.cmake_vars["NSX_EXECUTORCH_CMSIS_NN_PROVIDER"] == "ns"
    assert "NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT" not in artifacts.cmake_vars
    assert "NSX_EXECUTORCH_NS_CMSIS_NN_ROOT" not in artifacts.cmake_vars
    provider = artifacts.extra_modules[0]
    assert provider.project == "ns-cmsis-nn"
    assert provider.local is False


def test_adapter_enables_ns_ops_for_ns_provider(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, backend="ns", ns_ops=True),
        tmp_path / "work",
    )
    assert artifacts.cmake_vars["NSX_EXECUTORCH_ENABLE_NS_OPS"] == "ON"


def test_adapter_defaults_ns_ops_off(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")
    assert artifacts.cmake_vars["NSX_EXECUTORCH_ENABLE_NS_OPS"] == "OFF"


def test_adapter_rejects_ns_ops_on_arm_provider(tmp_path: Path):
    source = _source_tree(tmp_path)
    with pytest.raises(EngineError, match="ns_ops requires engine.backend 'ns'"):
        ExecuTorchAdapter().prepare(
            _config(tmp_path, source, backend="arm", ns_ops=True),
            tmp_path / "work",
        )


def test_adapter_rejects_non_boolean_ns_ops(tmp_path: Path):
    source = _source_tree(tmp_path)
    with pytest.raises(EngineError, match="ns_ops must be a boolean"):
        ExecuTorchAdapter().prepare(
            _config(tmp_path, source, ns_ops="yes"),
            tmp_path / "work",
        )


def _write_sidecar(model_path: Path, **overrides) -> Path:
    import hashlib
    import json

    manifest = {
        "schema": "nsx-executorch.pte-manifest/1",
        "pte_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "kernel_provider": "ns",
        "requires_ns_ops": True,
        "planned_arena_size": 98304,
        "inputs": [{"shape": [1, 16, 32, 32], "dtype": "FLOAT", "size_bytes": 65536}],
        "outputs": [{"shape": [1, 10], "dtype": "FLOAT", "size_bytes": 40}],
        "operators": {
            "cortex_m": ["cortex_m::quantized_conv2d.out"],
            "cortex_m_ns": ["cortex_m_ns::quantized_sub.out"],
            "portable": ["aten::mean.out"],
        },
    }
    manifest.update(overrides)
    sidecar = Path(f"{model_path}.json")
    sidecar.write_text(json.dumps(manifest), encoding="utf-8")
    return sidecar


def _sidecar_config(tmp_path: Path, source: Path, *, backend=None, **engine_config):
    """A config with no explicit sizes/ops — the sidecar must provide them."""
    model = tmp_path / "model.pte"
    model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 32)
    values = {"source_path": str(source)}
    values.update(engine_config)
    engine: dict = {"type": "executorch", "config": values}
    if backend is not None:
        engine["backend"] = backend
    return load_config(None, {"model": {"path": str(model)}, "engine": engine})


def test_adapter_self_configures_from_pte_sidecar(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source)
    _write_sidecar(config.model.path)

    artifacts = ExecuTorchAdapter().prepare(config, tmp_path / "work")

    assert artifacts.cmake_vars["NSX_EXECUTORCH_CMSIS_NN_PROVIDER"] == "ns"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_ENABLE_NS_OPS"] == "ON"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST"] == "aten::mean.out"
    assert artifacts.executorch_planned_arena_size == 98304
    assert artifacts.executorch_input_size == 65536
    assert artifacts.executorch_output_size == 40


def test_adapter_explicit_config_overrides_sidecar(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, backend="ns", ns_ops=True)
    _write_sidecar(config.model.path, planned_arena_size=1)

    artifacts = ExecuTorchAdapter().prepare(config, tmp_path / "work")

    # Explicit engine.config values win over the sidecar.
    assert artifacts.executorch_planned_arena_size == 2048
    assert artifacts.cmake_vars["NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST"] == "aten::clamp.out"


def test_adapter_rejects_sidecar_for_different_pte(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source)
    _write_sidecar(config.model.path, pte_sha256="f" * 64)

    with pytest.raises(EngineError, match="describes a different PTE"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_rejects_disabling_ns_ops_for_ns_pte(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source, backend="ns", ns_ops=False)
    _write_sidecar(config.model.path)

    with pytest.raises(EngineError, match="ns_ops is disabled"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_honors_explicit_provider_checkout(tmp_path: Path):
    source = _source_tree(tmp_path)
    provider = tmp_path / "custom-arm-cmsis-nn"
    provider.mkdir()
    (provider / "CMakeLists.txt").write_text("# provider\n", encoding="utf-8")
    (provider / "nsx-module.yaml").write_text(
        "schema_version: 1\nmodule:\n  name: arm-cmsis-nn\n", encoding="utf-8"
    )

    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, cmsis_nn_path=str(provider)), tmp_path / "work"
    )

    provider_ref = artifacts.extra_modules[0]
    assert provider_ref.name == "arm-cmsis-nn"
    assert provider_ref.path == provider
    assert provider_ref.local is True


def test_adapter_honors_explicit_ns_provider_checkout(tmp_path: Path):
    source = _source_tree(tmp_path)
    provider = tmp_path / "custom-ns-cmsis-nn"
    (provider / "Include").mkdir(parents=True)
    (provider / "Source").mkdir()
    (provider / "nsx").mkdir()
    (provider / "CMakeLists.txt").write_text("# provider\n", encoding="utf-8")
    (provider / "nsx" / "CMakeLists.txt").write_text("# nsx provider\n", encoding="utf-8")
    (provider / "nsx" / "nsx-module.yaml").write_text(
        "schema_version: 1\nmodule:\n  name: nsx-cmsis-nn\n", encoding="utf-8"
    )

    artifacts = ExecuTorchAdapter().prepare(
        _config(
            tmp_path,
            source,
            backend="ns",
            cmsis_nn_path=str(provider),
        ),
        tmp_path / "work",
    )

    provider_ref = artifacts.extra_modules[0]
    assert provider_ref.name == "nsx-cmsis-nn"
    assert provider_ref.project == "ns-cmsis-nn"
    assert provider_ref.local is True
    assert (provider_ref.path / "nsx-module.yaml").is_file()


def test_adapter_rejects_wrong_nsx_executorch_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_tree(tmp_path)
    monkeypatch.setattr(executorch_mod, "_checkout_commit", lambda _path: "f" * 40)

    with pytest.raises(EngineError, match="expected 62b22f"):
        ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")


def test_adapter_rejects_wrong_executorch_submodule_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_tree(tmp_path)

    def _commit(path: Path) -> str:
        if path.parent.name == "external":
            return "f" * 40
        return "62b22f96dc49e2c28eb20aee0f15ebb7ad1c1d59"

    monkeypatch.setattr(executorch_mod, "_checkout_commit", _commit)

    with pytest.raises(EngineError, match="external/executorch is at"):
        ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")


def test_adapter_rejects_retired_nsx_subdirectory_layout(tmp_path: Path):
    # A checkout that only ships the old nsx/ subdirectory layout (no root
    # nsx-module.yaml/CMakeLists.txt) must be rejected with a clear hint.
    root = tmp_path / "nsx-executorch"
    (root / "nsx").mkdir(parents=True)
    (root / "version.txt").write_text("0.1.0\n", encoding="utf-8")
    (root / "nsx" / "nsx-module.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    config = _config(tmp_path, root)
    with pytest.raises(EngineError, match="Invalid nsx-executorch checkout"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_rejects_invalid_backend(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, backend="cmsis-nn")
    with pytest.raises(EngineError, match="backend must be 'arm' or 'ns'"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_rejects_incomplete_io_contract(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, output_size=0)
    with pytest.raises(EngineError, match="output_size"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_executorch_template_has_counter_health_and_true_overflow_mask():
    # Production's env, not a look-alike -- see issue #119.
    out = _render_executorch_template(
        pmu_passes=[
            {
                "name": "cpu_0",
                "event_ids": ["0x0011U", "0x0008U"],
                "counter_names": ["ARM_PMU_CPU_CYCLES", "ARM_PMU_INST_RETIRED"],
                "num_counters": 2,
            }
        ],
        pmu_pass_names=["cpu_0"],
    )

    assert "HPX_PMU_INIT_STATUS" in out
    assert "HPX_PMU_SELFTEST_CPU_CYCLES" in out
    assert 'hpx_printf("HPX_READY\\n")' in out
    assert "HPX_ERROR=operator_count_exceeds_capacity" in out
    # g_layers is SRAM-resident, so the AP5 shared SSRAM domain must be powered
    # on for it -- with the HAL declarations for that call actually in scope
    # via the narrow header, not merely by accident of some other guard pulling
    # in the umbrella.
    #
    # This assertion was briefly weakened to `am_mcu_apollo.h OR
    # am_hal_pwrctrl.h` when the template became a child of _main_base.cc.j2,
    # on the theory that the narrow guard "deliberately does not fire a second
    # time". That was wrong: the extraction had moved
    # `{% set pmu_profiler_sram_resident %}` 160 lines BELOW the
    # _system_includes.j2 include that reads it, so the guard's third disjunct
    # was simply testing an undefined name -- dead, not deliberate, and
    # invisible under StrictUndefined because the guard spells it
    # `| default(false)`. The flag is set at the top of the base now, so the
    # narrow include is emitted again for every SRAM-resident render.
    assert "am_hal_pwrctrl_sram_config(&sramCfg)" in out
    assert '#include "am_hal_pwrctrl.h"' in out
    assert "g_logical_overflow_mask |= 1UL << (2 * i + 1)" in out
    end_operator = out[out.index("static void end_operator") :]
    assert end_operator.index("ARM_PMU_Get_CNTR_OVS()") < end_operator.index(
        "nsx_pmu_get_counters(&g_pmu_cfg)"
    )
    assert "result.execution_cycles" in out


def test_executorch_template_places_complete_workspace_in_sram():
    # Production's env, not a look-alike -- see issue #119.
    out = _render_executorch_template(
        cmsis_device_header="apollo330P.h",
        pmu_max_ops=512,
        executorch_planned_arena_size=138240,
        executorch_method_arena_size=65536,
        executorch_temporary_arena_size=32768,
        executorch_input_size=110592,
        executorch_output_size=8,
        arena_region="sram",
        executorch_planned_arena_region="sram",
        executorch_method_arena_region="sram",
        executorch_temporary_arena_region="sram",
        executorch_io_region="sram",
        ssram_full_power_enum="AM_HAL_PWRCTRL_SRAM_1P75M",
        iterations=5,
        warmup=2,
        clean_iters=5,
    )

    for name in (
        "g_planned_arena",
        "g_method_arena",
        "g_temporary_arena",
        "g_input",
        "g_output",
    ):
        assert f"NSX_MEM_SRAM_BSS alignas(16) static uint8_t {name}" in out


def test_adapter_resolves_per_buffer_regions(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(
        _config(
            tmp_path,
            source,
            planned_arena_location="tcm",
            method_arena_location="sram",
            temporary_arena_location="sram",
            io_location="sram",
        ),
        tmp_path / "work",
    )
    assert artifacts.executorch_planned_arena_region == "tcm"
    assert artifacts.executorch_method_arena_region == "sram"
    assert artifacts.executorch_temporary_arena_region == "sram"
    assert artifacts.executorch_io_region == "sram"


def test_adapter_defaults_buffer_regions_to_follow_arena(tmp_path: Path):
    source = _source_tree(tmp_path)
    artifacts = ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")
    assert artifacts.executorch_planned_arena_region is None
    assert artifacts.executorch_io_region is None


def test_adapter_rejects_non_ram_buffer_region(tmp_path: Path):
    source = _source_tree(tmp_path)
    with pytest.raises(EngineError, match="planned_arena_location must be 'tcm' or 'sram'"):
        ExecuTorchAdapter().prepare(
            _config(tmp_path, source, planned_arena_location="psram"),
            tmp_path / "work",
        )


def test_adapter_rejects_non_ram_arena_location(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = load_config(
        None,
        {
            "model": {
                "path": str(tmp_path / "model.pte"),
                "arena_size": 2048,
                "arena_location": "psram",
            },
            "engine": {
                "type": "executorch",
                "backend": "arm",
                "config": {
                    "source_path": str(source),
                    "input_size": 64,
                    "output_size": 16,
                },
            },
        },
    )
    (tmp_path / "model.pte").write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 32)
    with pytest.raises(EngineError, match="not\\s+valid for ExecuTorch runtime buffers"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_executorch_template_splits_buffer_regions():
    # Production's env, not a look-alike -- see issue #119.
    out = _render_executorch_template(
        executorch_method_arena_region="sram",
        executorch_temporary_arena_region="sram",
        executorch_io_region="sram",
    )
    assert "NSX_MEM_FAST_BSS alignas(16) static uint8_t g_planned_arena[2048]" in out
    assert "NSX_MEM_SRAM_BSS alignas(16) static uint8_t g_method_arena[1024]" in out
    assert "NSX_MEM_SRAM_BSS alignas(16) static uint8_t g_temporary_arena[512]" in out
    assert "NSX_MEM_SRAM_BSS alignas(16) static uint8_t g_input[64]" in out
    assert "NSX_MEM_SRAM_BSS alignas(16) static uint8_t g_output[16]" in out


def test_adapter_rejects_sidecar_with_non_boolean_requires_ns_ops(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source)
    _write_sidecar(config.model.path, requires_ns_ops="false")

    with pytest.raises(EngineError, match="requires_ns_ops.*JSON boolean"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_rejects_sidecar_with_malformed_inputs(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source)
    _write_sidecar(config.model.path, inputs=["not-a-tensor-object"])

    with pytest.raises(EngineError, match="'inputs' must be a list of tensor objects"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")


def test_adapter_rejects_sidecar_with_bad_planned_size(tmp_path: Path):
    source = _source_tree(tmp_path)
    config = _sidecar_config(tmp_path, source)
    _write_sidecar(config.model.path, planned_arena_size="98304")

    with pytest.raises(EngineError, match="planned_arena_size.*positive integer"):
        ExecuTorchAdapter().prepare(config, tmp_path / "work")

# ---------------------------------------------------------------------------
# Auto-clone resolution (#160) — source_path absent clones the pinned baseline
# ---------------------------------------------------------------------------


def test_adapter_auto_clones_pinned_checkout_when_source_path_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_tree(tmp_path)
    seen: dict[str, str] = {}

    def fake_auto_clone(url: str, ref: str) -> Path:
        seen["url"] = url
        seen["ref"] = ref
        return source

    monkeypatch.setattr(executorch_mod, "_auto_clone_nsx_executorch", fake_auto_clone)
    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, source_path=None), tmp_path / "work"
    )

    # URL and ref come from the compatibility baseline's nsx-executorch project.
    assert seen["url"] == "https://github.com/AmbiqAI/nsx-executorch.git"
    assert seen["ref"] == "62b22f96dc49e2c28eb20aee0f15ebb7ad1c1d59"
    # The cloned checkout then flows through the unchanged wrapper/verify path.
    wrapper = artifacts.extra_modules[-1].path
    assert f'"{source.as_posix()}"' in (wrapper / "CMakeLists.txt").read_text()


def test_adapter_rejects_blank_source_path(tmp_path: Path):
    source = _source_tree(tmp_path)
    with pytest.raises(EngineError, match="source_path must be a non-empty filesystem path"):
        ExecuTorchAdapter().prepare(
            _config(tmp_path, source, source_path="   "), tmp_path / "work"
        )


class _GitRecorder:
    """Fake _run_git capturing (subcommand-args, cwd) and scripting rev-parse."""

    def __init__(self, heads: list[str]):
        self.heads = list(heads)
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, args, cwd, *, timeout, url, ref):
        self.calls.append((tuple(args), Path(cwd)))
        out = ""
        if args[0] == "rev-parse":
            head = self.heads.pop(0)
            if head == "FAIL":
                raise EngineError("corrupt cache")
            out = head + "\n"

        class _Result:
            stdout = out

        return _Result()

    def subcommands(self) -> list[str]:
        return [args[0] for args, _cwd in self.calls]


_PINNED = "62b22f96dc49e2c28eb20aee0f15ebb7ad1c1d59"
_URL = "https://github.com/AmbiqAI/nsx-executorch.git"


def _patch_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cache = tmp_path / "cache" / "nsx-executorch"
    monkeypatch.setattr(executorch_mod, "_EXECUTORCH_CACHE_DIR", cache)
    return cache


def test_auto_clone_fresh_cache_clones_and_inits_submodules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = _patch_cache(monkeypatch, tmp_path)
    git = _GitRecorder(heads=[_PINNED])
    monkeypatch.setattr(executorch_mod, "_run_git", git)

    result = executorch_mod._auto_clone_nsx_executorch(_URL, _PINNED)

    assert result == cache
    assert git.subcommands() == ["clone", "rev-parse", "submodule", "submodule"]
    clone_args, clone_cwd = git.calls[0]
    assert clone_args == ("clone", _URL, str(cache))
    top_args, top_cwd = git.calls[2]
    assert top_args == ("submodule", "update", "--init", "external/executorch")
    assert top_cwd == cache
    nested_args, nested_cwd = git.calls[3]
    assert nested_args[3:] == executorch_mod._EXECUTORCH_MINIMAL_SUBMODULES
    assert nested_cwd == cache / "external" / "executorch"


def test_auto_clone_cache_hit_skips_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = _patch_cache(monkeypatch, tmp_path)
    (cache / ".git").mkdir(parents=True)
    git = _GitRecorder(heads=[_PINNED])
    monkeypatch.setattr(executorch_mod, "_run_git", git)

    executorch_mod._auto_clone_nsx_executorch(_URL, _PINNED)

    # No clone/fetch/checkout — but submodule init still runs (idempotent).
    assert git.subcommands() == ["rev-parse", "submodule", "submodule"]


def test_auto_clone_resyncs_cache_on_baseline_ref_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cache = _patch_cache(monkeypatch, tmp_path)
    (cache / ".git").mkdir(parents=True)
    git = _GitRecorder(heads=["0000000000000000000000000000000000000000"])
    monkeypatch.setattr(executorch_mod, "_run_git", git)

    executorch_mod._auto_clone_nsx_executorch(_URL, _PINNED)

    assert git.subcommands() == ["rev-parse", "fetch", "checkout", "submodule", "submodule"]
    assert ("fetch", "origin", _PINNED) == git.calls[1][0][:3]
    assert ("checkout", "--detach", _PINNED) == git.calls[2][0]


def test_auto_clone_recovers_from_corrupt_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = _patch_cache(monkeypatch, tmp_path)
    (cache / ".git").mkdir(parents=True)
    (cache / "junk.txt").write_text("stale", encoding="utf-8")
    git = _GitRecorder(heads=["FAIL", _PINNED])
    monkeypatch.setattr(executorch_mod, "_run_git", git)

    executorch_mod._auto_clone_nsx_executorch(_URL, _PINNED)

    # The unusable cache is removed and recloned from scratch.
    assert git.subcommands() == ["rev-parse", "clone", "rev-parse", "submodule", "submodule"]
    assert not (cache / "junk.txt").exists()


def test_auto_clone_failure_hints_manual_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _patch_cache(monkeypatch, tmp_path)

    def failing_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(128, cmd, stderr="fatal: could not resolve host")

    monkeypatch.setattr(executorch_mod.subprocess, "run", failing_run)

    with pytest.raises(EngineError) as excinfo:
        executorch_mod._auto_clone_nsx_executorch(_URL, _PINNED)
    message = str(excinfo.value)
    assert "could not resolve host" in message
    assert "source_path" in message
    assert _URL in message
    assert _PINNED in message
