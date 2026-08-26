"""Unit tests for the hpx validate CLI surface (no hardware required)."""

from __future__ import annotations

import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

HPX = shutil.which("hpx")

requires_hpx = pytest.mark.skipif(
    HPX is None,
    reason="`hpx` console script not on PATH (install heliaPROFILER first)",
)


def _run_hpx(*args: str) -> subprocess.CompletedProcess:
    assert HPX is not None  # requires_hpx skips these tests otherwise
    return subprocess.run(
        [HPX, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@requires_hpx
class TestValidateList:
    def test_list_default_shows_full_matrix(self):
        proc = _run_hpx("validate", "--list")
        assert proc.returncode == 0, proc.stderr
        assert "976 case(s) would run" in proc.stdout
        assert "kws" in proc.stdout
        assert "vww" in proc.stdout
        assert "ic" in proc.stdout
        assert "ad" in proc.stdout

    def test_list_engine_alias_aot(self):
        proc = _run_hpx("validate", "--list", "--engines", "aot", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "240 case(s)" in proc.stdout
        assert "helia-aot" in proc.stdout

    def test_list_executorch_alias_expands_both_providers(self):
        proc = _run_hpx(
            "validate",
            "--list",
            "--boards",
            "apollo330mP_evb",
            "--engines",
            "et",
            "--power",
            "off",
            "--toolchains",
            "gcc",
            "--interfaces",
            "rtt",
            "--memories",
            "auto",
        )
        assert proc.returncode == 0, proc.stderr
        assert "8 case(s) would run" in proc.stdout
        assert "executorch/arm" in proc.stdout
        assert "executorch/ns" in proc.stdout

    def test_list_executorch_provider_can_be_selected_independently(self):
        proc = _run_hpx(
            "validate",
            "--list",
            "--boards",
            "apollo330mP_evb",
            "--models",
            "kws",
            "--engines",
            "executorch",
            "--executorch-backends",
            "ns",
            "--power",
            "off",
            "--toolchains",
            "gcc",
            "--interfaces",
            "rtt",
            "--memories",
            "auto",
        )

        assert proc.returncode == 0, proc.stderr
        assert "1 case(s) would run" in proc.stdout
        assert "executorch/ns" in proc.stdout
        assert "executorch/arm" not in proc.stdout

    def test_list_power_off(self):
        proc = _run_hpx("validate", "--list", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "976 case(s)" in proc.stdout

    def test_list_power_boards_keeps_apollo330_unpowered(self):
        proc = _run_hpx(
            "validate",
            "--list",
            "--suite",
            "smoke",
            "--boards",
            "apollo510_evb,apollo330mP_evb",
            "--power",
            "on",
            "--power-boards",
            "apollo510_evb",
        )
        assert proc.returncode == 0, proc.stderr
        assert "2 case(s) would run" in proc.stdout
        assert "apollo510_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-power" in proc.stdout
        assert "apollo330mP_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-power" not in proc.stdout
        assert "apollo330mP_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto" in proc.stdout

    def test_list_axis_filters_for_two_pass_board_smoke(self):
        proc = _run_hpx(
            "validate",
            "--list",
            "--boards",
            "apollo3p_evb",
            "--models",
            "kws",
            "--engines",
            "rt",
            "--power",
            "off",
            "--toolchains",
            "gcc",
            "--interfaces",
            "rtt",
            "--memories",
            "auto",
            "--repeat",
            "2",
        )
        assert proc.returncode == 0, proc.stderr
        assert "2 case(s)" in proc.stdout
        assert "apollo3p_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-run01" in proc.stdout
        assert "apollo3p_evb-kws-rt-ns-arm-none-eabi-gcc-rtt-auto-run02" in proc.stdout

    def test_list_unknown_model_fails(self):
        proc = _run_hpx("validate", "--list", "--models", "nope")
        assert proc.returncode != 0
        assert "Unknown model" in proc.stderr

    def test_list_unknown_engine_fails(self):
        proc = _run_hpx("validate", "--list", "--engines", "tflite")
        assert proc.returncode != 0
        assert "unknown engine" in proc.stderr.lower()

    def test_list_accepts_yaml_model_registry(self, tmp_path):
        model = tmp_path / "candidate.tflite"
        model.write_bytes(b"candidate")
        registry = tmp_path / "models.yml"
        registry.write_text(
            f"""
models:
  kws-candidate:
    path: {model}
    comparison_group: kws
    arena_size: 65536
"""
        )

        proc = _run_hpx(
            "validate",
            "--list",
            "--suite",
            "smoke",
            "--models-file",
            str(registry),
        )

        assert proc.returncode == 0, proc.stderr
        assert "1 case(s) would run" in proc.stdout
        assert "apollo510_evb-kws-candidate-rt" in proc.stdout

    def test_list_accepts_direct_model_paths(self, tmp_path):
        first = tmp_path / "kws-base.tflite"
        second = tmp_path / "kws-pruned.tflite"
        first.write_bytes(b"base")
        second.write_bytes(b"pruned")

        proc = _run_hpx(
            "validate",
            "--list",
            "--suite",
            "smoke",
            "--model-paths",
            f"{first},{second}",
            "--comparison-group",
            "kws",
        )

        assert proc.returncode == 0, proc.stderr
        assert "2 case(s) would run" in proc.stdout
        assert "kws-base" in proc.stdout
        assert "kws-pruned" in proc.stdout

    def test_help_mentions_validate(self):
        proc = _run_hpx("--help")
        assert proc.returncode == 0
        assert "validate" in proc.stdout


class TestSuiteSmoke:
    """--suite smoke fills in unset axes without touching hardware (pytest.main mocked)."""

    def _captured_pytest_args(self, monkeypatch: pytest.MonkeyPatch, *argv: str) -> list[str]:
        from helia_profiler import cli

        captured: dict = {}

        def fake_pytest_main(args):
            captured["args"] = list(args)
            return 0

        monkeypatch.setattr(pytest, "main", fake_pytest_main)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["validate", *argv])
        assert excinfo.value.code == 0
        return captured["args"]

    def test_smoke_defaults_unset_axes(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--suite", "smoke", "--output-dir", str(tmp_path)
        )

        def value_of(flag: str) -> str:
            return args[args.index(flag) + 1]

        assert value_of("--mlperf-models") == "kws"
        assert value_of("--mlperf-engines") == "helia-rt"
        assert value_of("--mlperf-toolchains") == "arm-none-eabi-gcc"
        assert value_of("--mlperf-transports") == "rtt"
        assert value_of("--mlperf-memories") == "auto"
        assert value_of("--mlperf-suite") == "smoke"

    def test_resolved_cmsis_nn_commit_is_forwarded_to_pytest(self, monkeypatch, tmp_path):
        commit = "edc4edbd81af2f3baa9354ea1e30cca50dfcfd99"
        args = self._captured_pytest_args(
            monkeypatch,
            "--suite",
            "smoke",
            "--ns-cmsis-nn-ref",
            commit,
            "--output-dir",
            str(tmp_path),
        )

        assert args[args.index("--mlperf-ns-cmsis-nn-ref") + 1] == commit

    def test_power_boards_are_forwarded_to_pytest(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch,
            "--suite",
            "smoke",
            "--power",
            "on",
            "--power-boards",
            "apollo510_evb",
            "--output-dir",
            str(tmp_path),
        )

        assert args[args.index("--mlperf-power-boards") + 1] == "apollo510_evb"

    def test_explicit_axis_wins_over_smoke_default(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--models", "vww", "--suite", "smoke", "--output-dir", str(tmp_path)
        )
        assert args[args.index("--mlperf-models") + 1] == "vww"
        # Other unset axes still get smoke defaults.
        assert args[args.index("--mlperf-engines") + 1] == "helia-rt"

    def test_custom_model_options_are_forwarded_to_pytest(self, monkeypatch, tmp_path):
        model = tmp_path / "candidate.tflite"
        model.write_bytes(b"candidate")
        args = self._captured_pytest_args(
            monkeypatch,
            "--suite",
            "smoke",
            "--model-paths",
            str(model),
            "--comparison-group",
            "kws",
            "--model-arena-size",
            "65536",
            "--output-dir",
            str(tmp_path / "out"),
        )

        assert args[args.index("--mlperf-models") + 1] == "candidate"
        assert args[args.index("--mlperf-model-paths") + 1] == str(model)
        assert args[args.index("--mlperf-comparison-group") + 1] == "kws"
        assert args[args.index("--mlperf-model-arena-size") + 1] == "65536"

    def test_models_rt_defaults_to_two_board_gcc_atfe_model_sweep(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--suite", "models-rt", "--output-dir", str(tmp_path)
        )

        def value_of(flag: str) -> str:
            return args[args.index(flag) + 1]

        assert value_of("--mlperf-models") == "kws,vww,ic,ad"
        assert value_of("--mlperf-engines") == "helia-rt"
        assert value_of("--mlperf-boards") == "apollo510_evb,apollo330mP_evb"
        assert value_of("--mlperf-toolchains") == "arm-none-eabi-gcc,atfe"
        assert value_of("--mlperf-transports") == "rtt"
        assert value_of("--mlperf-memories") == "auto"

    def test_models_aot_defaults_to_two_board_gcc_atfe_model_sweep(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--suite", "models-aot", "--output-dir", str(tmp_path)
        )

        assert args[args.index("--mlperf-models") + 1] == "kws,vww,ic,ad"
        assert args[args.index("--mlperf-engines") + 1] == "helia-aot"
        assert args[args.index("--mlperf-boards") + 1] == "apollo510_evb,apollo330mP_evb"
        assert args[args.index("--mlperf-toolchains") + 1] == "arm-none-eabi-gcc,atfe"
        assert args[args.index("--mlperf-executorch-backends") + 1] == "arm,ns"

    def test_complete_defaults_to_all_engines_two_board_gcc_atfe_sweep(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--suite", "complete", "--output-dir", str(tmp_path)
        )

        assert args[args.index("--mlperf-models") + 1] == "kws,vww,ic,ad"
        assert args[args.index("--mlperf-engines") + 1] == "helia-rt,helia-aot,tflm,executorch"
        assert args[args.index("--mlperf-boards") + 1] == "apollo510_evb,apollo330mP_evb"
        assert args[args.index("--mlperf-toolchains") + 1] == "arm-none-eabi-gcc,atfe"


def test_completed_validation_renders_rich_report(monkeypatch, tmp_path):
    from helia_profiler import cli
    from helia_profiler.validation.runner import CaseResult

    case = CaseResult(
        case_id="candidate",
        status="pass",
        duration_s=1.0,
        engine="helia-rt",
        model_id="customer-model",
        board="apollo510_evb",
        power=False,
        toolchain="arm-none-eabi-gcc",
        transport="rtt",
        memory="auto",
        total_cycles=123,
        binary_total_bytes=456,
    )

    def fake_pytest_main(args):
        output_dir = tmp_path
        (output_dir / "validation_report.json").write_text(
            json.dumps(
                {
                    "cases": [case.to_dict()],
                    "summary": {"total": 1, "pass": 1, "fail": 0, "skip": 0},
                }
            )
        )
        (output_dir / "validation_report.md").write_text("# report\n")
        (output_dir / "validation_manifest.json").write_text("{}\n")
        return 0

    monkeypatch.setattr(pytest, "main", fake_pytest_main)
    with (
        patch("helia_profiler.console.HpxConsole.print_validation") as render,
        pytest.raises(SystemExit) as exc_info,
    ):
        cli.main(["validate", "--suite", "smoke", "--output-dir", str(tmp_path)])

    assert exc_info.value.code == 0
    report = render.call_args.args[0]
    assert report.cases[0].model_id == "customer-model"
