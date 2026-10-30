from __future__ import annotations

from types import SimpleNamespace

import pytest

from helia_profiler.cli import profile_cmd as cli
from helia_profiler.console import HpxConsole
from helia_profiler.errors import EXIT_IMAGE_TOO_LARGE, BuildError, ImageTooLargeError
from helia_profiler.results import FirmwareMeta, PmuResult, ProfileResult


def _run_profile_cmd(**overrides):
    # verbose=False mirrors the old default so the overrides dicts asserted
    # below stay byte-identical.
    overrides.setdefault("verbose", False)
    cli._cmd_profile(**overrides)


def test_profile_cli_forwards_rtt_buffer_size(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["path"] = path
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr(
        "helia_profiler.api._execute_profile",
        lambda config, **kwargs: ProfileResult(PmuResult(meta=FirmwareMeta(), layers=[])),
    )

    _run_profile_cmd(rtt_buffer_size_up=16384)

    assert seen["overrides"] == {
        "target": {"rtt_buffer_size_up": 16384},
        "verbose": False,
    }


def test_profile_cli_forwards_explicit_dependency_mode(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr(
        "helia_profiler.api._execute_profile",
        lambda config, **kwargs: ProfileResult(PmuResult(meta=FirmwareMeta(), layers=[])),
    )

    _run_profile_cmd(update_dependencies=True)

    assert seen["overrides"] == {
        "build": {"update_dependencies": True},
        "verbose": False,
    }


def test_profile_cli_forwards_power_firmware(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["path"] = path
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr(
        "helia_profiler.api._execute_profile",
        lambda config, **kwargs: ProfileResult(PmuResult(meta=FirmwareMeta(), layers=[])),
    )

    _run_profile_cmd(power=True, power_firmware="shared")

    assert seen["overrides"] == {
        "power": {"enabled": True, "firmware": "shared"},
        "verbose": False,
    }


def test_profile_cli_forwards_split_placement_to_model(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["path"] = path
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr(
        "helia_profiler.api._execute_profile",
        lambda config, **kwargs: ProfileResult(PmuResult(meta=FirmwareMeta(), layers=[])),
    )

    _run_profile_cmd(runtime_arena_location="sram", runtime_weights_location="mram")

    assert seen["overrides"] == {
        "model": {"arena_location": "sram", "weights_location": "mram"},
        "verbose": False,
    }


def test_profile_cli_owns_console_presentation(monkeypatch) -> None:
    config = SimpleNamespace(verbose=1)
    seen: dict[str, object] = {}
    monkeypatch.setattr("helia_profiler.config.load_config", lambda *_args: config)

    def fake_run_profile(received_config, **kwargs):
        seen["config"] = received_config
        seen.update(kwargs)
        return ProfileResult(PmuResult(meta=FirmwareMeta(), layers=[]))

    monkeypatch.setattr("helia_profiler.api._execute_profile", fake_run_profile)

    _run_profile_cmd(verbose=1)

    assert seen["config"] is config
    console = seen["observer"]
    assert isinstance(console, HpxConsole)
    assert console.verbosity == 1


def test_cli_choice_lists_mirror_the_config_enums():
    """CLI choices expose every member of their configuration enums."""
    from typing import Any, cast

    from typer.main import get_command

    from helia_profiler.cli.app import app
    from helia_profiler.config import Aggregation, PowerFirmware

    profile = cast(Any, get_command(app)).commands["profile"]
    options = {param.name: param for param in profile.params}
    assert set(options["aggregation"].type.choices) == {a.value for a in Aggregation}
    assert set(options["power_firmware"].type.choices) == {f.value for f in PowerFirmware}


def test_profile_signature_matches_override_specs():
    """Every ``hpx profile`` Typer parameter must have exactly one override
    spec (and vice versa): the Typer signature forwards ``locals()`` by name,
    so a param without a spec would only fail at runtime via the unknown-name
    TypeError, and a spec without a param would be dead mapping."""
    import inspect

    from helia_profiler.cli.app import profile_command
    from helia_profiler.cli.options import _KNOWN_PARAMS

    sig_params = set(inspect.signature(profile_command).parameters) - {"config"}
    assert sig_params == set(_KNOWN_PARAMS)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ImageTooLargeError("Image exceeds flashable MRAM by 1 bytes"), EXIT_IMAGE_TOO_LARGE),
        (BuildError("Build failed"), 1),
    ],
)
def test_profile_cli_exit_code_names_oversized_image(monkeypatch, error, code) -> None:
    monkeypatch.setattr(
        "helia_profiler.config.load_config", lambda path, overrides: SimpleNamespace(verbose=0)
    )

    def fail(config, **kwargs):
        raise error

    monkeypatch.setattr("helia_profiler.api._execute_profile", fail)

    with pytest.raises(SystemExit) as excinfo:
        _run_profile_cmd()

    assert excinfo.value.code == code
    assert EXIT_IMAGE_TOO_LARGE == 4
