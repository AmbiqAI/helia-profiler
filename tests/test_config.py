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
    assert config.profiling.iterations == 100


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
