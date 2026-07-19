"""Tests for the Typer-based ``hpx`` CLI surface (argparse -> Typer migration).

These exercise the top-level app via ``typer.testing.CliRunner`` and verify
that the thin Typer command adapters build the same ``SimpleNamespace``
contract the existing ``_cmd_*`` implementations expect.
"""

from __future__ import annotations

from types import SimpleNamespace

from click import unstyle
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


def test_profile_accepts_tflm_engine(monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}

    def fake_cmd_profile(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(profile_cmd, "_cmd_profile", fake_cmd_profile)

    result = runner.invoke(app, ["profile", "model.tflite", "--engine", "tflm"])

    assert result.exit_code == 0, result.output
    assert seen["args"].engine == "tflm"


def test_profile_uses_canonical_placement_options(monkeypatch) -> None:
    import helia_profiler.cli.profile_cmd as profile_cmd

    seen: dict[str, object] = {}
    monkeypatch.setattr(profile_cmd, "_cmd_profile", lambda args: seen.setdefault("args", args))

    result = runner.invoke(
        app,
        ["profile", "model.tflite", "--arena-location", "sram", "--weights-location", "mram"],
    )

    assert result.exit_code == 0, result.output
    assert seen["args"].runtime_arena_location == "sram"
    assert seen["args"].runtime_weights_location == "mram"
    removed = runner.invoke(
        app,
        ["profile", "model.tflite", "--runtime-arena-location", "sram"],
    )
    assert removed.exit_code == 2


def test_compare_validation_option_reaches_command_adapter(monkeypatch) -> None:
    import helia_profiler.cli.compare_cmd as compare_cmd

    seen: dict[str, object] = {}

    def fake_cmd_compare(args: SimpleNamespace) -> None:
        seen["args"] = args

    monkeypatch.setattr(compare_cmd, "_cmd_compare", fake_cmd_compare)

    result = runner.invoke(
        app,
        [
            "compare",
            "baseline",
            "candidate",
            "--validation",
            "--output-dir",
            "comparison",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen["args"].validation is True
    assert str(seen["args"].output_dir) == "comparison"


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


def test_probes_no_subcommand_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, ["probes"])
    assert result.exit_code == 0
    assert "Usage: hpx probes" in unstyle(result.output)


def test_target_no_subcommand_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, ["target"])
    assert result.exit_code == 0
    assert "Usage: hpx target" in unstyle(result.output)


def test_cache_no_subcommand_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, ["cache"])
    assert result.exit_code == 0
    assert "Usage: hpx cache" in unstyle(result.output)


def test_ports_no_subcommand_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, ["ports"])
    assert result.exit_code == 0
    assert "Usage: hpx ports" in unstyle(result.output)


def test_bare_invocation_prints_help_and_exits_zero() -> None:
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Profile LiteRT models on Ambiq silicon." in result.output
