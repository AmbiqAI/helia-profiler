"""Unit tests for the validation runner retry behavior."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from helia_profiler.engines import EngineType
from helia_profiler.validation.matrix import BOARDS, MODELS, CaseSpec
from helia_profiler.validation.runner import _build_config, run_case


def test_build_config_aot_prefers_explicit_cmsis_nn_env(
    tmp_path: Path,
    fake_cmsis_nn: Path,
    monkeypatch,
):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"
    monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_AOT,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["engine"]["config"]["cmsis_nn_path"] == str(fake_cmsis_nn)


def test_build_config_aot_discovers_neuralspotx_monorepo_checkout(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    repo_root = workspace_root / "tools" / "helia-profiler"
    repo_root.mkdir(parents=True)
    output_dir = tmp_path / "out"

    cmsis_nn = workspace_root / "neuralspotx" / "nsx-modules" / "ns-cmsis-nn"
    cmsis_nn.mkdir(parents=True)
    (cmsis_nn / "nsx").mkdir()
    (cmsis_nn / "nsx" / "nsx-module.yaml").write_text(
        "schema_version: 1\nmodule:\n  name: nsx-cmsis-nn\n"
    )

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_AOT,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["engine"]["config"]["cmsis_nn_path"] == str(cmsis_nn)


def test_run_case_retries_once_on_transient_joulescope_lock(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_root = tmp_path / "out"
    calls = {"count": 0}

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=True,
        board=BOARDS["apollo510_evb"],
    )

    def fake_run(cmd, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        calls["count"] += 1
        if calls["count"] == 1:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stdout="Joulescope u/js110/004204 is already in use by another process\n",
                stderr="",
            )

        config_path = Path(cmd[-1])
        config = yaml.safe_load(config_path.read_text())
        assert config["target"]["board"] == "apollo510_evb"
        assert "sync_gpio_pin" not in config["power"]
        case_dir = Path(config["output"]["dir"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "summary.json").write_text(json.dumps({
            "layers": 13,
            "total_cycles": 123456,
            "power": {"energy_j": 0.1, "avg_current_a": 0.005, "peak_current_a": 0.02},
        }))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("helia_profiler.validation.runner.subprocess.run", fake_run)
    monkeypatch.setattr("helia_profiler.validation.runner.time.sleep", lambda _: None)

    result = run_case(case=case, repo_root=repo_root, output_root=output_root, timeout_s=30)

    assert result.status == "pass"
    assert result.energy_uj == 100000.0
    assert calls["count"] == 2
