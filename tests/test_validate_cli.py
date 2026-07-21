"""Unit tests for the hpx validate CLI surface (no hardware required)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

HPX = shutil.which("hpx")

requires_hpx = pytest.mark.skipif(
    HPX is None,
    reason="`hpx` console script not on PATH (install heliaPROFILER first)",
)


def _run_hpx(*args: str) -> subprocess.CompletedProcess:
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
        assert "720 case(s) would run" in proc.stdout
        assert "kws" in proc.stdout
        assert "vww" in proc.stdout
        assert "ic" in proc.stdout
        assert "ad" in proc.stdout

    def test_list_engine_alias_aot(self):
        proc = _run_hpx("validate", "--list", "--engines", "aot", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "240 case(s)" in proc.stdout
        assert "helia-aot" in proc.stdout

    def test_list_power_off(self):
        proc = _run_hpx("validate", "--list", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "720 case(s)" in proc.stdout

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
        assert "apollo3p_evb-kws-rt-arm-none-eabi-gcc-rtt-auto-run01" in proc.stdout
        assert "apollo3p_evb-kws-rt-arm-none-eabi-gcc-rtt-auto-run02" in proc.stdout

    def test_list_unknown_model_fails(self):
        proc = _run_hpx("validate", "--list", "--models", "nope")
        assert proc.returncode != 0
        assert "Unknown model" in proc.stderr

    def test_list_unknown_engine_fails(self):
        proc = _run_hpx("validate", "--list", "--engines", "tflite")
        assert proc.returncode != 0
        assert "unknown engine" in proc.stderr.lower()

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

    def test_explicit_axis_wins_over_smoke_default(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--models", "vww", "--suite", "smoke", "--output-dir", str(tmp_path)
        )
        assert args[args.index("--mlperf-models") + 1] == "vww"
        # Other unset axes still get smoke defaults.
        assert args[args.index("--mlperf-engines") + 1] == "helia-rt"

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

    def test_complete_defaults_to_all_engines_two_board_gcc_atfe_sweep(self, monkeypatch, tmp_path):
        args = self._captured_pytest_args(
            monkeypatch, "--suite", "complete", "--output-dir", str(tmp_path)
        )

        assert args[args.index("--mlperf-models") + 1] == "kws,vww,ic,ad"
        assert args[args.index("--mlperf-engines") + 1] == "helia-rt,helia-aot,tflm"
        assert args[args.index("--mlperf-boards") + 1] == "apollo510_evb,apollo330mP_evb"
        assert args[args.index("--mlperf-toolchains") + 1] == "arm-none-eabi-gcc,atfe"
