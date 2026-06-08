"""Basic tests for ProfileConfig construction."""

from pathlib import Path

import pytest

from helia_profiler.config import ProfileConfig, load_config, NsxModuleOverride
from helia_profiler.errors import ConfigError


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


def test_clock_mode_defaults_to_low():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
    }
    config = load_config(None, cli)
    assert config.target.clock_mode.value == "low"


def test_clock_mode_from_cli():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "target": {"clock_mode": "high"},
    }
    config = load_config(None, cli)
    assert config.target.clock_mode.value == "high"


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


# ---------------------------------------------------------------------------
# BuildConfig / NSX module overrides
# ---------------------------------------------------------------------------


def test_build_config_defaults():
    """BuildConfig should be present with defaults when unspecified."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
    }
    config = load_config(None, cli)
    assert config.build.channel is None
    assert config.build.nsx_modules == {}


def test_build_config_channel_override():
    """Channel override should flow through from YAML/CLI."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {"channel": "dev"},
    }
    config = load_config(None, cli)
    assert config.build.channel == "dev"


def test_build_config_nsx_module_path_override():
    """Local path override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {
            "nsx_modules": {
                "nsx-ambiq-bsp-r5": {"path": "/home/dev/my-bsp"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-ambiq-bsp-r5"]
    assert override.path == Path("/home/dev/my-bsp")
    assert override.ref is None
    assert override.version is None


def test_build_config_nsx_module_ref_override():
    """Git ref override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {
            "nsx_modules": {
                "nsx-ambiq-hal-r5": {"ref": "feat/apollo6-support"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-ambiq-hal-r5"]
    assert override.ref == "feat/apollo6-support"
    assert override.path is None
    assert override.version is None


def test_build_config_nsx_module_version_override():
    """Version pin override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {
            "nsx_modules": {
                "nsx-ambiqsuite-r5": {"version": "2.0.0"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-ambiqsuite-r5"]
    assert override.version == "2.0.0"
    assert override.path is None
    assert override.ref is None


def test_build_config_multiple_overrides():
    """Multiple NSX module overrides in one config."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {
            "channel": "dev",
            "nsx_modules": {
                "nsx-ambiq-bsp-r5": {"path": "/dev/bsp"},
                "nsx-ambiq-hal-r5": {"ref": "main"},
                "nsx-ambiqsuite-r5": {"version": "3.0.0"},
            },
        },
    }
    config = load_config(None, cli)
    assert config.build.channel == "dev"
    assert len(config.build.nsx_modules) == 3
    assert config.build.nsx_modules["nsx-ambiq-bsp-r5"].path == Path("/dev/bsp")
    assert config.build.nsx_modules["nsx-ambiq-hal-r5"].ref == "main"
    assert config.build.nsx_modules["nsx-ambiqsuite-r5"].version == "3.0.0"


# ---------------------------------------------------------------------------
# NsxModuleOverride validation
# ---------------------------------------------------------------------------


def test_nsx_module_override_rejects_no_mode():
    """NsxModuleOverride must have at least one mode set."""
    with pytest.raises(ConfigError, match="exactly one"):
        NsxModuleOverride()


def test_nsx_module_override_rejects_multiple_modes():
    """NsxModuleOverride rejects more than one mode."""
    with pytest.raises(ConfigError, match="only one"):
        NsxModuleOverride(path=Path("/x"), ref="main")


def test_nsx_module_override_rejects_all_three():
    """NsxModuleOverride rejects all three modes set."""
    with pytest.raises(ConfigError, match="only one"):
        NsxModuleOverride(path=Path("/x"), ref="main", version="1.0.0")


# ---------------------------------------------------------------------------
# Channel validation
# ---------------------------------------------------------------------------


def test_build_config_invalid_channel():
    """Invalid channel names should raise ConfigError."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {"channel": "../escape"},
    }
    with pytest.raises(ConfigError, match="Invalid build.channel"):
        load_config(None, cli)


def test_build_config_channel_rejects_empty():
    """Empty string channel should be rejected."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {"channel": ""},
    }
    with pytest.raises(ConfigError, match="Invalid build.channel"):
        load_config(None, cli)


# ---------------------------------------------------------------------------
# Malformed nsx_modules spec
# ---------------------------------------------------------------------------


def test_build_config_malformed_module_spec():
    """Non-dict module spec should raise ConfigError."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
        "build": {
            "nsx_modules": {
                "nsx-ambiq-bsp-r5": "just-a-string",
            },
        },
    }
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(None, cli)
