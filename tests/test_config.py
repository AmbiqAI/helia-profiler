"""Basic tests for ProfileConfig construction."""

from pathlib import Path

from helia_profiler.config import ProfileConfig, load_config


def test_load_config_from_cli_overrides():
    """Config should be constructible from CLI overrides alone."""
    cli = {
        "model": {"path": "test.tflite", "arena_size": 32768},
        "engine": {"type": "tflm"},
    }
    config = load_config(None, cli)

    assert isinstance(config, ProfileConfig)
    assert config.model.path == Path("test.tflite")
    assert config.model.arena_size == 32768
    assert config.engine.type.value == "tflm"
    assert config.target.board == "apollo510_evb"
    assert config.target.jlink_serial is None
    assert config.profiling.iterations == 100


def test_jlink_serial_from_cli():
    """jlink_serial should be settable via CLI overrides."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "target": {"jlink_serial": "1160002255"},
    }
    config = load_config(None, cli)
    assert config.target.jlink_serial == "1160002255"


def test_config_is_frozen():
    """ProfileConfig should be immutable."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
    }
    config = load_config(None, cli)

    try:
        config.verbose = 5  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass


def test_timeouts_defaults():
    """TimeoutsConfig should be populated with defaults when unspecified."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
    }
    config = load_config(None, cli)
    t = config.timeouts
    assert t.configure_s == 120
    assert t.build_s == 300
    assert t.flash_s == 120
    assert t.toolchain_probe_s == 5
    assert t.binary_probe_s == 10
    assert t.download_api_s == 30
    assert t.download_asset_s == 300


def test_timeouts_overrides():
    """YAML/CLI overrides should flow into TimeoutsConfig."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "timeouts": {
            "build_s": 900,
            "flash_s": 60,
            "download_asset_s": 1200,
        },
    }
    config = load_config(None, cli)
    t = config.timeouts
    assert t.build_s == 900
    assert t.flash_s == 60
    assert t.download_asset_s == 1200
    # Unspecified values retain defaults
    assert t.configure_s == 120
    assert t.toolchain_probe_s == 5
