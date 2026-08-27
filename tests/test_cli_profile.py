from __future__ import annotations

from types import SimpleNamespace

from helia_profiler.cli import profile_cmd as cli
from helia_profiler.console import HpxConsole


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
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: None)

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
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: None)

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
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: None)

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
    monkeypatch.setattr("helia_profiler.profiler.run_profile", lambda config, **kwargs: None)

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

    monkeypatch.setattr("helia_profiler.profiler.run_profile", fake_run_profile)

    _run_profile_cmd(verbose=1)

    assert seen["config"] is config
    console = seen["console"]
    assert isinstance(console, HpxConsole)
    assert console.verbosity == 1


def test_cli_choice_lists_mirror_the_config_enums():
    """The --aggregation / --power-firmware Choice lists must derive from the
    enums, not restate them: a hand-maintained list would let a new member
    land invisible to the CLI (the drift class the wire-name binding test
    closes for HPX_ENGINE)."""
    from helia_profiler.cli.app import _AGGREGATION_CHOICE, _POWER_FIRMWARE_CHOICE
    from helia_profiler.config import Aggregation, PowerFirmware

    assert set(_AGGREGATION_CHOICE.choices) == {a.value for a in Aggregation}
    assert set(_POWER_FIRMWARE_CHOICE.choices) == {f.value for f in PowerFirmware}


def test_profile_signature_matches_override_specs():
    """Every ``hpx profile`` Typer parameter must have exactly one override
    spec (and vice versa): the Typer signature forwards ``locals()`` by name,
    so a param without a spec would only fail at runtime via the unknown-name
    TypeError, and a spec without a param would be dead mapping."""
    import inspect

    from helia_profiler.cli.app import profile_command
    from helia_profiler.cli.profile_cmd import _KNOWN_PARAMS

    sig_params = set(inspect.signature(profile_command).parameters) - {"config"}
    assert sig_params == set(_KNOWN_PARAMS)
