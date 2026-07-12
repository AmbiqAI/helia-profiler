"""Tests for the Typer-based ``hpx`` CLI surface (argparse -> Typer migration).

These exercise the top-level app via ``typer.testing.CliRunner`` and verify
that the thin Typer command adapters build the same ``SimpleNamespace``
contract the existing ``_cmd_*`` implementations expect.
"""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from helia_profiler._version import __version__
from helia_profiler.cli.app import app

runner = CliRunner()


def test_version_prints_and_exits_zero() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"hpx {__version__}" in result.output


def test_unknown_flag_exits_with_usage_error() -> None:
    result = runner.invoke(app, ["profile", "--bogus-flag"])
    assert result.exit_code == 2


def test_pmu_counters_repeatable_option_builds_list(monkeypatch) -> None:
    """Multiple --pmu-counters occurrences replace argparse's old nargs='+' form."""
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}

    def fake_cmd_profile(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(profile_cmd, "_cmd_profile", fake_cmd_profile)

    result = runner.invoke(
        app,
        [
            "profile",
            "model.tflite",
            "--pmu-counters",
            "cpu:default",
            "--pmu-counters",
            "mve:all",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["args"].pmu_counters == ["cpu:default", "mve:all"]


def test_pmu_presets_repeatable_option_builds_list(monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}

    def fake_cmd_profile(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(profile_cmd, "_cmd_profile", fake_cmd_profile)

    result = runner.invoke(
        app,
        ["profile", "model.tflite", "--pmu-presets", "basic_cpu", "--pmu-presets", "mve_all"],
    )

    assert result.exit_code == 0, result.output
    assert seen["args"].pmu_presets == ["basic_cpu", "mve_all"]


def test_profile_accepts_tflm_engine(monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}

    def fake_cmd_profile(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(profile_cmd, "_cmd_profile", fake_cmd_profile)

    result = runner.invoke(app, ["profile", "model.tflite", "--engine", "tflm"])

    assert result.exit_code == 0, result.output
    assert seen["args"].engine == "tflm"


def test_per_layer_tri_state_true_false_and_absent(monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}

    def fake_cmd_profile(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(profile_cmd, "_cmd_profile", fake_cmd_profile)

    result = runner.invoke(app, ["profile", "model.tflite", "--per-layer"])
    assert result.exit_code == 0, result.output
    assert seen["args"].per_layer is True

    result = runner.invoke(app, ["profile", "model.tflite", "--no-per-layer"])
    assert result.exit_code == 0, result.output
    assert seen["args"].per_layer is False

    result = runner.invoke(app, ["profile", "model.tflite"])
    assert result.exit_code == 0, result.output
    assert seen["args"].per_layer is None


def test_probes_no_subcommand_prints_usage_and_exits_one() -> None:
    result = runner.invoke(app, ["probes"])
    assert result.exit_code == 1
    assert "Usage: hpx probes {list|match}" in result.output


def test_target_no_subcommand_prints_usage_and_exits_one() -> None:
    result = runner.invoke(app, ["target"])
    assert result.exit_code == 1
    assert "Usage: hpx target {reset}" in result.output


def test_cache_no_subcommand_prints_usage_and_exits_one() -> None:
    result = runner.invoke(app, ["cache"])
    assert result.exit_code == 1
    assert "Usage: hpx cache {purge|info}" in result.output


def test_ports_no_subcommand_prints_usage_and_exits_one() -> None:
    result = runner.invoke(app, ["ports"])
    assert result.exit_code == 1
    assert "Usage: hpx ports {list}" in result.output


def test_bare_invocation_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Profile LiteRT models on Ambiq silicon." in result.output
