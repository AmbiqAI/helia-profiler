"""Config-file failures at the shared reader and its API/CLI boundaries."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from helia_profiler import ConfigError, Session
from helia_profiler import config as config_module
from helia_profiler.cli.app import app
from helia_profiler.config import load_config, read_config_yaml


@pytest.mark.parametrize("load", [lambda path: load_config(path, {}), Session.from_yaml])
def test_directory_config_has_a_typed_error(tmp_path: Path, load) -> None:
    with pytest.raises(ConfigError, match="Cannot read config file") as caught:
        load(tmp_path)
    assert str(tmp_path) in str(caught.value)
    assert isinstance(caught.value.__cause__, OSError)
    assert caught.value.hint


@pytest.mark.parametrize("load", [lambda path: load_config(path, {}), Session.from_yaml])
def test_permission_denied_config_preserves_cause(tmp_path: Path, monkeypatch, load) -> None:
    path = tmp_path / "config.yaml"
    fault = PermissionError("access denied by test")
    monkeypatch.setattr(config_module, "open", Mock(side_effect=fault), raising=False)
    with pytest.raises(ConfigError, match="Cannot read config file") as caught:
        load(path)
    assert caught.value.__cause__ is fault
    assert str(path) in str(caught.value)
    assert "access denied by test" in str(caught.value)


def test_config_read_failure_is_translated_after_open(tmp_path: Path, monkeypatch) -> None:
    fault = OSError("read failed")
    stream = StringIO("model: {}")
    monkeypatch.setattr(stream, "read", Mock(side_effect=fault))
    monkeypatch.setattr(config_module, "open", Mock(return_value=stream), raising=False)
    with pytest.raises(ConfigError, match="Cannot read config file") as caught:
        read_config_yaml(tmp_path / "config.yaml")
    assert caught.value.__cause__ is fault
    assert stream.closed


@pytest.mark.parametrize("fault", [RuntimeError("reader bug"), KeyboardInterrupt()])
def test_unexpected_config_reader_errors_propagate(tmp_path: Path, monkeypatch, fault) -> None:
    monkeypatch.setattr(config_module, "open", Mock(side_effect=fault), raising=False)
    with pytest.raises(type(fault)) as caught:
        read_config_yaml(tmp_path / "config.yaml")
    assert caught.value is fault


@pytest.mark.parametrize("command", ["profile", "freeze"])
@pytest.mark.parametrize("failure", ["directory", "permission"])
def test_config_io_errors_are_reported_on_stderr(
    tmp_path: Path, monkeypatch, command: str, failure: str
) -> None:
    path = tmp_path
    if failure == "permission":
        path = tmp_path / "config.yaml"
        monkeypatch.setattr(
            config_module, "open", Mock(side_effect=PermissionError("access denied")), raising=False
        )
    plan = tmp_path / "plan.json"
    args = [command, "--config", str(path)]
    if command == "freeze":
        args += ["-o", str(plan)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    assert "Cannot read config file" in result.stderr
    assert "Traceback" not in result.stderr
    assert not plan.exists()


@pytest.mark.parametrize("document", ["", "model:\n  path: example.tflite\n"])
def test_readable_config_keeps_mapping_behavior(tmp_path: Path, document: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(document, encoding="utf-8")
    expected = {"model": {"path": "example.tflite"}} if document else {}
    assert read_config_yaml(path) == expected
