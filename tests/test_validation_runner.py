"""Unit tests for the validation runner retry behavior."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import yaml

from helia_profiler.config import Toolchain, Transport
from helia_profiler.engines import EngineType
from helia_profiler.validation.matrix import BOARDS, MODELS, CaseSpec, MemoryProfile
from helia_profiler.validation.runner import (
    CaseResult,
    _build_config,
    run_case,
    validation_health_issues,
)


def test_power_health_rejects_suspect_gate_duration():
    result = CaseResult(
        case_id="apollo510-power",
        status="pass",
        duration_s=1.0,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=True,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        layers=13,
        total_cycles=123456,
        energy_uj=100.0,
        gated_window_duration_suspect=True,
    )

    assert validation_health_issues(result) == (
        "GPIO-gated power window duration is suspect",
    )


def test_power_health_rejects_invalid_gate_integrity():
    result = CaseResult(
        case_id="apollo510-power",
        status="pass",
        duration_s=1.0,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=True,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        layers=13,
        total_cycles=123456,
        energy_uj=100.0,
        gate_duration_integrity_valid=False,
    )

    assert validation_health_issues(result) == (
        "GPIO-gated power window failed duration integrity",
    )


def test_power_health_rejects_degraded_observation():
    result = CaseResult(
        case_id="apollo510-power",
        status="pass",
        duration_s=1.0,
        engine="helia-rt",
        model_id="kws",
        board="apollo510_evb",
        power=True,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        layers=13,
        total_cycles=123456,
        energy_uj=100.0,
        power_observation_mode="free_form",
        power_observation_integrity="degraded",
        power_gate_failure_kind="no_gate_rise",
    )

    assert validation_health_issues(result) == (
        "power observation is degraded (no_gate_rise)",
    )


def test_build_config_includes_reliability_axes(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=False,
        board=BOARDS["apollo4p_blue_kxr_evb"],
        toolchain=Toolchain.ATFE,
        transport=Transport.UART,
        memory=MemoryProfile.SRAM,
        jlink_serial="1160001481",
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["target"]["board"] == "apollo4p_blue_kxr_evb"
    assert cfg["target"]["toolchain"] == "atfe"
    assert cfg["target"]["transport"] == "uart"
    assert cfg["target"]["jlink_serial"] == "1160001481"
    assert cfg["model"]["arena_location"] == "sram"
    assert cfg["model"]["weights_location"] == "sram"
    assert cfg["power"]["enabled"] is False
    assert cfg["output"]["dir"] == str(output_dir)
    assert cfg["work_dir"] == str(output_dir / "work")


def test_build_config_pins_power_serial_for_multi_instrument_bench(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"
    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=True,
        board=BOARDS["apollo510_evb"],
        power_serial="25QG",
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["power"]["enabled"] is True
    assert cfg["power"]["serial"] == "25QG"


def test_build_config_pins_explicit_power_gpio_wiring(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"
    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=True,
        board=BOARDS["apollo330mP_evb"],
        power_gpio_pins=(5, 6, 7),
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["power"]["sync_gpio_pin"] == 5
    assert cfg["power"]["state_gpio_pin"] == 6
    assert cfg["power"]["go_gpio_pin"] == 7


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


def test_build_config_tflm_selects_upstream_cmsis_nn_backend(tmp_path: Path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_dir = tmp_path / "out"
    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.TFLM,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    cfg = _build_config(case, repo_root=repo_root, output_dir=output_dir)

    assert cfg["engine"] == {"type": "tflm", "backend": "cmsis_nn"}


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
        (case_dir / "summary.json").write_text(
            json.dumps(
                {
                    "layers": 13,
                    "total_cycles": 123456,
                    "power": {"energy_j": 0.1, "avg_current_a": 0.005, "peak_current_a": 0.02},
                }
            )
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("helia_profiler.validation.runner.subprocess.run", fake_run)
    monkeypatch.setattr("helia_profiler.validation.runner.time.sleep", lambda _: None)

    result = run_case(case=case, repo_root=repo_root, output_root=output_root, timeout_s=30)

    assert result.status == "pass"
    assert result.energy_uj == 100000.0
    assert calls["count"] == 2


def test_run_case_uses_current_python_for_subprocess(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_root = tmp_path / "out"
    seen: dict[str, object] = {}
    fake_python = tmp_path / "venv" / "bin" / "python"
    fake_hpx = fake_python.parent / "hpx"
    fake_hpx.parent.mkdir(parents=True)
    fake_python.write_text("")
    fake_hpx.write_text("")

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    def fake_run(cmd, cwd, capture_output, text, timeout, check, env):
        del capture_output, text, timeout, check, env
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        config_path = Path(cmd[-1])
        case_dir = Path(yaml.safe_load(config_path.read_text())["output"]["dir"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "summary.json").write_text(json.dumps({"layers": 13, "total_cycles": 123456}))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("helia_profiler.validation.runner.subprocess.run", fake_run)
    monkeypatch.setattr("helia_profiler.validation.runner.sys.executable", str(fake_python))

    result = run_case(case=case, repo_root=repo_root, output_root=output_root, timeout_s=30)

    assert result.status == "pass"
    assert seen["cwd"] == str(repo_root)
    assert seen["cmd"] == [
        str(fake_hpx),
        "profile",
        "--config",
        str(output_root / case.case_id / "config.yml"),
    ]


def test_run_case_writes_full_child_log(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_root = tmp_path / "out"

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    def fake_run(cmd, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        config_path = Path(cmd[-1])
        case_dir = Path(yaml.safe_load(config_path.read_text())["output"]["dir"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "summary.json").write_text(json.dumps({"layers": 13, "total_cycles": 123456}))
        return subprocess.CompletedProcess(
            cmd, 0, stdout="child stdout marker\n", stderr="child stderr marker\n"
        )

    monkeypatch.setattr("helia_profiler.validation.runner.subprocess.run", fake_run)

    result = run_case(case=case, repo_root=repo_root, output_root=output_root, timeout_s=30)

    log_file = output_root / case.case_id / "hpx_profile.log"
    assert result.log_path == str(log_file)
    assert log_file.exists()
    text = log_file.read_text()
    assert text.startswith("$ ")
    assert "--- stdout ---" in text
    assert "child stdout marker" in text
    assert "--- stderr ---" in text
    assert "child stderr marker" in text


def test_run_case_verbose_appends_v_flag(tmp_path: Path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output_root = tmp_path / "out"
    seen: dict[str, object] = {}

    case = CaseSpec(
        model=MODELS["kws"],
        engine=EngineType.HELIA_RT,
        power=False,
        board=BOARDS["apollo510_evb"],
    )

    def fake_run(cmd, cwd, capture_output, text, timeout, check, env):
        del cwd, capture_output, text, timeout, check, env
        seen["cmd"] = cmd
        config_path = Path(cmd[cmd.index("--config") + 1])
        case_dir = Path(yaml.safe_load(config_path.read_text())["output"]["dir"])
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "summary.json").write_text(json.dumps({"layers": 13, "total_cycles": 123456}))
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr("helia_profiler.validation.runner.subprocess.run", fake_run)

    result = run_case(
        case=case, repo_root=repo_root, output_root=output_root, timeout_s=30, verbose=True
    )

    assert result.status == "pass"
    assert seen["cmd"][-1] == "-v"
