from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import jinja2
import pytest

import helia_profiler.engines.executorch as executorch_mod
from helia_profiler.config import load_config
from helia_profiler.engines.executorch import ExecuTorchAdapter
from helia_profiler.errors import EngineError

# Captured before the autouse fixture below monkeypatches the module
# attribute, so tests that exercise the real clone/subprocess logic can call
# it directly without going through the fake.
_real_clone_provider_at_ref = executorch_mod._clone_provider_at_ref
_real_provider_cache_root = executorch_mod._provider_cache_root


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
def _fake_cmsis_nn_cache(tmp_path_factory: pytest.TempPathFactory, monkeypatch):
    """Redirect the CMSIS-NN provider cache and skip real git clones.

    Every test in this module runs the ExecuTorchAdapter, which materializes
    the selected CMSIS-NN provider outside NSX's own module bootstrap (see
    executorch.py's module docstring for why). Point the cache at a scratch
    directory and fake the clone step so tests never touch the real
    ~/.cache/helia-profiler or the network.
    """
    cache_root = tmp_path_factory.mktemp("cmsis-nn-cache")
    monkeypatch.setattr(executorch_mod, "_provider_cache_root", lambda: cache_root)
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
                    else "0a0d5a1633f595b86dfd156f3c2859bebdf2a470"
                )
            )
        ),
    )
    monkeypatch.setattr(
        executorch_mod,
        "_gitlink_commit",
        lambda _path, _submodule: "3a97429b0ce0c192861fc3e3729fb81432fd22cf",
    )

    calls: list[tuple[str, str, Path]] = []

    def _fake_clone(url: str, ref: str, dest: Path) -> None:
        calls.append((url, ref, dest))
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "CMakeLists.txt").write_text(
            "# fake CMSIS-NN provider checkout\n", encoding="utf-8"
        )

    monkeypatch.setattr(executorch_mod, "_clone_provider_at_ref", _fake_clone)
    return calls


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
    # HPX-generated apps add ExecuTorch as a subproject and never install()
    # it (nor finalize NSX's own board-flags "nsxTargets" export set), so
    # ExecuTorch's stock install(EXPORT ExecuTorchTargets ...) validation
    # must be skipped via its own documented standalone-consumer opt-out.
    assert artifacts.cmake_vars["EXECUTORCH_BAREMETAL_SKIP_INSTALL"] == "ON"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_CMSIS_NN_PROVIDER"] == "arm"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_PORTABLE_SELECT_OPS_LIST"] == ("aten::clamp.out")
    assert artifacts.cmake_vars["Python3_EXECUTABLE"] == Path(sys.executable).absolute().as_posix()
    # The selected provider is materialized outside NSX's module bootstrap
    # and handed to nsx-executorch via its own explicit root-override var —
    # never declared as a normal NSX_APP_MODULE (see executorch.py docstring
    # for why that would double-add_subdirectory() the same source tree).
    arm_root = Path(artifacts.cmake_vars["NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT"])
    assert (arm_root / "CMakeLists.txt").is_file()
    assert "NSX_EXECUTORCH_NS_CMSIS_NN_ROOT" not in artifacts.cmake_vars
    assert artifacts.executorch_planned_arena_size == 2048

    module_names = [module.name for module in artifacts.extra_modules]
    assert module_names == ["nsx-executorch"]

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
    provider = tmp_path / "ns-provider"
    provider.mkdir()
    (provider / "CMakeLists.txt").write_text("# provider\n", encoding="utf-8")
    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, backend="ns", cmsis_nn_path=str(provider)),
        tmp_path / "work",
    )

    module_names = [module.name for module in artifacts.extra_modules]
    assert module_names == ["nsx-executorch"]

    assert artifacts.cmake_vars["NSX_EXECUTORCH_CMSIS_NN_PROVIDER"] == "ns"
    ns_root = Path(artifacts.cmake_vars["NSX_EXECUTORCH_NS_CMSIS_NN_ROOT"])
    assert (ns_root / "CMakeLists.txt").is_file()
    assert "NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT" not in artifacts.cmake_vars


@pytest.mark.parametrize("cmsis_nn_path", [None, ""])
def test_adapter_rejects_unqualified_default_ns_provider(
    tmp_path: Path, cmsis_nn_path: str | None
):
    source = _source_tree(tmp_path)
    engine_config = {} if cmsis_nn_path is None else {"cmsis_nn_path": cmsis_nn_path}

    with pytest.raises(EngineError, match="requires engine.config.cmsis_nn_path"):
        ExecuTorchAdapter().prepare(
            _config(tmp_path, source, backend="ns", **engine_config), tmp_path / "work"
        )


def test_adapter_materializes_cmsis_nn_provider_at_pinned_baseline_ref(
    tmp_path: Path, _fake_cmsis_nn_cache
):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, backend="arm")
    baseline_project = config.compatibility_baseline.project("arm-cmsis-nn")

    artifacts = ExecuTorchAdapter().prepare(config, tmp_path / "work")

    assert len(_fake_cmsis_nn_cache) == 1
    url, ref, dest = _fake_cmsis_nn_cache[0]
    assert url == baseline_project.url
    assert ref == baseline_project.ref
    assert dest.name == f"arm-cmsis-nn-{baseline_project.ref}"
    assert artifacts.cmake_vars["NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT"] == dest.as_posix()


def test_adapter_reuses_cached_cmsis_nn_provider_checkout(tmp_path: Path, _fake_cmsis_nn_cache):
    source = _source_tree(tmp_path)
    config = _config(tmp_path, source, backend="arm")

    ExecuTorchAdapter().prepare(config, tmp_path / "work")
    ExecuTorchAdapter().prepare(config, tmp_path / "work2")

    # Second prepare() reuses the already-materialized checkout — no
    # redundant clone of the same pinned commit.
    assert len(_fake_cmsis_nn_cache) == 1


def test_adapter_honors_explicit_provider_checkout(tmp_path: Path):
    source = _source_tree(tmp_path)
    provider = tmp_path / "custom-arm-cmsis-nn"
    provider.mkdir()
    (provider / "CMakeLists.txt").write_text("# provider\n", encoding="utf-8")

    artifacts = ExecuTorchAdapter().prepare(
        _config(tmp_path, source, cmsis_nn_path=str(provider)), tmp_path / "work"
    )

    assert artifacts.cmake_vars["NSX_EXECUTORCH_ARM_CMSIS_NN_ROOT"] == provider.as_posix()


def test_provider_cache_honors_nsx_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NSX_CACHE_DIR", str(tmp_path / "nsx-cache"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "ignored-xdg"))

    assert _real_provider_cache_root() == tmp_path / "nsx-cache" / "hpx-provider-sources"


def test_clone_provider_at_ref_runs_clone_checkout_and_submodule_update(
    tmp_path: Path, monkeypatch
):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
        assert kwargs.get("check") is True
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(executorch_mod.subprocess, "run", _fake_run)

    dest = tmp_path / "arm-cmsis-nn-deadbeef"
    _real_clone_provider_at_ref("https://github.com/AmbiqAI/arm-cmsis-nn.git", "deadbeef", dest)

    assert [c[:2] for c in calls] == [
        ["git", "clone"],
        ["git", "-C"],
        ["git", "-C"],
    ]
    clone_dest = calls[0][-1]
    assert calls[0][-3:-1] == ["--no-checkout", "https://github.com/AmbiqAI/arm-cmsis-nn.git"]
    assert calls[1][2:] == [clone_dest, "checkout", "--quiet", "--detach", "deadbeef"]
    assert calls[2][2:] == [
        clone_dest,
        "submodule",
        "update",
        "--init",
        "--recursive",
    ]
    assert dest.is_dir()


def test_clone_provider_at_ref_cleans_up_and_raises_engine_error_on_failure(
    tmp_path: Path, monkeypatch
):
    import subprocess as real_subprocess

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True, exist_ok=True)
            raise real_subprocess.CalledProcessError(1, cmd)
        return None

    monkeypatch.setattr(executorch_mod.subprocess, "run", _fake_run)

    dest = tmp_path / "arm-cmsis-nn-deadbeef"
    with pytest.raises(EngineError, match="Failed to materialize CMSIS-NN provider"):
        _real_clone_provider_at_ref("https://github.com/AmbiqAI/arm-cmsis-nn.git", "deadbeef", dest)
    assert not dest.exists()


def test_clone_provider_reuses_concurrent_valid_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    dest = tmp_path / "arm-cmsis-nn-deadbeef"
    dest.mkdir()
    (dest / "CMakeLists.txt").write_text("# winner\n", encoding="utf-8")
    monkeypatch.setattr(executorch_mod, "_checkout_commit", lambda _path: "deadbeef")

    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="")

    monkeypatch.setattr(executorch_mod.subprocess, "run", _fake_run)
    _real_clone_provider_at_ref("https://github.com/AmbiqAI/arm-cmsis-nn.git", "deadbeef", dest)

    assert (dest / "CMakeLists.txt").read_text(encoding="utf-8") == "# winner\n"
    assert len(calls) == 3


def test_adapter_rejects_wrong_nsx_executorch_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_tree(tmp_path)
    monkeypatch.setattr(executorch_mod, "_checkout_commit", lambda _path: "f" * 40)

    with pytest.raises(EngineError, match="expected 0a0d5a"):
        ExecuTorchAdapter().prepare(_config(tmp_path, source), tmp_path / "work")


def test_adapter_rejects_wrong_executorch_submodule_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_tree(tmp_path)

    def _commit(path: Path) -> str:
        if path.parent.name == "external":
            return "f" * 40
        return "0a0d5a1633f595b86dfd156f3c2859bebdf2a470"

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
    with pytest.raises(EngineError, match="Invalid nsx-executorch source_path"):
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
