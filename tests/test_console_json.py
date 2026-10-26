from __future__ import annotations

import pytest

from helia_profiler.console.json import print_json


def test_json_is_exact_raw_stdout(capsys):
    value = "[bold]" + "long text " * 30 + "[/bold]"
    print_json({"z": value, "a": 1})
    output = capsys.readouterr()
    assert output.out == '{\n  "a": 1,\n  "z": "' + value + '"\n}\n'
    assert output.err == ""


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_json_rejects_nonfinite_numbers_without_output(capsys, value):
    with pytest.raises(ValueError):
        print_json({"value": value})
    assert capsys.readouterr() == ("", "")


def test_json_write_errors_propagate(monkeypatch):
    error = BrokenPipeError("closed")

    def refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr("builtins.print", refuse)
    with pytest.raises(BrokenPipeError) as caught:
        print_json({"value": 1})
    assert caught.value is error


@pytest.mark.parametrize("error", [ValueError("nonfinite"), BrokenPipeError("closed")])
def test_command_emission_errors_stay_outside_reported_boundary(tmp_path, monkeypatch, error):
    from helia_profiler.cli.run_cmds import _cmd_validate_build

    monkeypatch.setattr("helia_profiler.run_directory.load_from", lambda path: object())
    monkeypatch.setattr("helia_profiler.run_directory.build_summary", lambda *args: {})

    def refuse(*args):
        raise error

    monkeypatch.setattr("helia_profiler.cli.run_cmds.print_json", refuse)
    with pytest.raises(type(error)) as caught:
        _cmd_validate_build(directory=tmp_path, verbose=0)
    assert caught.value is error
