from __future__ import annotations

from types import SimpleNamespace

from helia_profiler.cli import profile_cmd as cli


def _profile_args(**overrides):
    args = SimpleNamespace(
        model=None,
        arena_size=None,
        model_location=None,
        runtime_arena_location=None,
        runtime_weights_location=None,
        core_override=None,
        engine=None,
        engine_config=None,
        board=None,
        toolchain=None,
        jlink_serial=None,
        transport=None,
        rtt_buffer_size_up=None,
        cpu_clock=None,
        frozen=False,
        pmu_presets=None,
        pmu_counters=None,
        per_layer=None,
        iterations=None,
        warmup=None,
        power=False,
        power_driver=None,
        power_firmware=None,
        power_mode=None,
        power_duration=None,
        sync_gpio=None,
        ensure_power=False,
        no_ensure_power=False,
        power_serial=None,
        output_dir=None,
        output_format=None,
        no_model_explorer=False,
        detailed=False,
        work_dir=None,
        keep_work_dir=False,
        clean=False,
        verbose=False,
        nsx_channel=None,
        nsx_module_overrides=None,
        config=None,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_profile_cli_forwards_rtt_buffer_size(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["path"] = path
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr("helia_profiler.api.profile", lambda config: None)

    cli._cmd_profile(_profile_args(rtt_buffer_size_up=16384))

    assert seen["overrides"] == {
        "target": {"rtt_buffer_size_up": 16384},
        "verbose": False,
    }


def test_profile_cli_forwards_power_firmware(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def fake_load_config(path, overrides):
        seen["path"] = path
        seen["overrides"] = overrides
        return SimpleNamespace(verbose=False)

    monkeypatch.setattr("helia_profiler.config.load_config", fake_load_config)
    monkeypatch.setattr("helia_profiler.api.profile", lambda config: None)

    cli._cmd_profile(_profile_args(power=True, power_firmware="shared"))

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
    monkeypatch.setattr("helia_profiler.api.profile", lambda config: None)

    cli._cmd_profile(
        _profile_args(runtime_arena_location="sram", runtime_weights_location="mram")
    )

    assert seen["overrides"] == {
        "model": {"arena_location": "sram", "weights_location": "mram"},
        "verbose": False,
    }