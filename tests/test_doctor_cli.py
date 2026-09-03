"""CLI tests for ``hpx doctor``: --json, --bundle, and option wiring.

Mirrors the existing Typer adapter test pattern in
``tests/test_cli_typer_app.py`` (monkeypatch the ``_cmd_*`` implementation
and assert the keyword arguments it receives) plus true end-to-end
``CliRunner`` invocations for the JSON and bundle paths.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from helia_profiler.cli import inspect_cmds as cli
from helia_profiler.cli.app import app

runner = CliRunner()


def test_doctor_bare_invocation_prints_table_and_exits_zero() -> None:
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Toolchain Check" in result.output


def test_doctor_json_flag_emits_valid_json_to_stdout() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert "ok" in payload
    assert "checks" in payload
    assert "versions" in payload
    assert any(check["name"] == "hpx" for check in payload["versions"])


def test_doctor_command_builds_expected_kwargs_for_json(monkeypatch) -> None:
    seen: dict[str, dict] = {}

    def fake_cmd_doctor(**kwargs) -> None:
        seen["kwargs"] = kwargs

    monkeypatch.setattr("helia_profiler.cli.inspect_cmds._cmd_doctor", fake_cmd_doctor)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    kwargs = seen["kwargs"]
    assert kwargs["json_"] is True
    assert kwargs["bundle"] is None
    assert kwargs["workspace"] is None
    assert kwargs["config"] is None
    assert kwargs["no_probes"] is False
    assert kwargs["no_ports"] is False
    assert kwargs["raw_probe_ids"] is False


def test_doctor_command_builds_expected_kwargs_for_bundle(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, dict] = {}

    def fake_cmd_doctor(**kwargs) -> None:
        seen["kwargs"] = kwargs

    monkeypatch.setattr("helia_profiler.cli.inspect_cmds._cmd_doctor", fake_cmd_doctor)
    workspace = tmp_path / "profiler_app"
    config = tmp_path / "hpx.yml"

    result = runner.invoke(
        app,
        [
            "doctor",
            "--bundle",
            str(tmp_path / "out"),
            "--workspace",
            str(workspace),
            "--config",
            str(config),
            "--toolchain",
            "armclang",
            "--transport",
            "usb_cdc",
            "--engine",
            "helia-aot",
            "--no-probes",
            "--no-ports",
            "--raw-probe-ids",
        ],
    )

    assert result.exit_code == 0, result.output
    kwargs = seen["kwargs"]
    assert kwargs["bundle"] == str(tmp_path / "out")
    assert kwargs["workspace"] == str(workspace)
    assert kwargs["config"] == str(config)
    assert kwargs["toolchain"] == "armclang"
    assert kwargs["transport"] == "usb_cdc"
    assert kwargs["engine"] == "helia-aot"
    assert kwargs["no_probes"] is True
    assert kwargs["no_ports"] is True
    assert kwargs["raw_probe_ids"] is True


def test_doctor_bundle_end_to_end_writes_archive(tmp_path: Path) -> None:
    out_dir = tmp_path / "bundle-out"
    result = runner.invoke(app, ["doctor", "--bundle", str(out_dir), "--no-probes", "--no-ports"])

    assert result.exit_code == 0, result.output
    assert "Support bundle written to" in result.output
    archives = list(out_dir.glob("*.zip"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as archive:
        assert "manifest.json" in archive.namelist()


def test_doctor_bundle_reports_skipped_sections(tmp_path: Path) -> None:
    out_dir = tmp_path / "bundle-out"
    result = runner.invoke(app, ["doctor", "--bundle", str(out_dir), "--no-probes", "--no-ports"])

    assert result.exit_code == 0, result.output
    assert "Skipped sections:" in result.output
    assert "dependencies" in result.output


def test_doctor_bundle_json_output_includes_path_and_manifest(tmp_path: Path) -> None:
    out_dir = tmp_path / "bundle-out"
    result = runner.invoke(
        app,
        ["doctor", "--bundle", str(out_dir), "--no-probes", "--no-ports", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert Path(payload["path"]).is_file()
    assert payload["manifest"]["schema"] == "hpx.support-bundle-manifest"


def test_doctor_bundle_raw_probe_ids_prints_warning(tmp_path: Path) -> None:
    out_dir = tmp_path / "bundle-out"
    result = runner.invoke(
        app,
        [
            "doctor",
            "--bundle",
            str(out_dir),
            "--no-probes",
            "--no-ports",
            "--raw-probe-ids",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Warning" in result.output
    assert "unredacted" in result.output


def test_doctor_bundle_explicit_zip_path_used_verbatim(tmp_path: Path) -> None:
    archive_path = tmp_path / "custom-support-bundle.zip"
    result = runner.invoke(
        app,
        ["doctor", "--bundle", str(archive_path), "--no-probes", "--no-ports"],
    )

    assert result.exit_code == 0, result.output
    assert archive_path.is_file()


def test_doctor_invalid_toolchain_exits_with_usage_error() -> None:
    with pytest.raises(SystemExit) as exc:
        cli._cmd_doctor(toolchain="not-a-real-toolchain")

    assert exc.value.code == 2


def test_cmd_doctor_defaults_print_table(capsys) -> None:
    cli._cmd_doctor()

    out = capsys.readouterr().out
    assert "Toolchain Check" in out
