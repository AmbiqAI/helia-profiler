"""Basic tests for ProfileConfig construction."""

import importlib
import json
from pathlib import Path

import pytest

from helia_profiler.config import (
    Aggregation,
    CleanWindowProbe,
    ClockSelection,
    EngineConfig,
    EngineType,
    ModelConfig,
    NsxModuleOverride,
    PowerConfig,
    PowerFirmware,
    ProfileConfig,
    ProfilingConfig,
    TargetConfig,
    Toolchain,
    WindowMode,
    load_config,
)
from helia_profiler.errors import ConfigError
from helia_profiler.pipeline import serialize_config
from helia_profiler.power.base import PowerMode


def test_load_config_from_cli_overrides():
    """Config should be constructible from CLI overrides alone."""
    cli = {
        "model": {"path": "test.tflite", "arena_size": 32768},
        "engine": {"type": "helia-rt"},
    }
    config = load_config(None, cli)

    assert isinstance(config, ProfileConfig)
    assert config.model.path == Path("test.tflite")
    assert config.model.arena_size == 32768
    assert config.engine.type.value == "helia-rt"
    assert config.target.board == "apollo510_evb"
    assert config.target.jlink_serial is None
    assert config.profiling.iterations == 100


def test_aggregation_defaults_to_median():
    config = load_config(None, {"model": {"path": "m.tflite"}, "engine": {"type": "helia-rt"}})
    assert config.profiling.aggregation == "median"


def test_power_stats_rate_hz_default_and_override():
    base = {"model": {"path": "m.tflite"}, "engine": {"type": "helia-rt"}}
    config = load_config(None, base)
    assert config.power.stats_rate_hz == 1000

    cli = {**base, "power": {"stats_rate_hz": 2000}}
    config = load_config(None, cli)
    assert config.power.stats_rate_hz == 2000


def test_power_stats_rate_hz_must_be_positive():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "power": {"stats_rate_hz": 0},
    }
    with pytest.raises(ConfigError, match="stats_rate_hz must be >= 1"):
        load_config(None, cli)


def test_aggregation_cli_override():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profiling": {"aggregation": "trimmed"},
    }
    config = load_config(None, cli)
    assert config.profiling.aggregation == "trimmed"


# ---------------------------------------------------------------------------
# Closed config vocabularies (#162 Phase 3)
# ---------------------------------------------------------------------------
# Each member VALUE is user- and wire-visible: it is what a config YAML says,
# what the CLI --choices accept, what the Jinja render compares against, and
# what lands in config_snapshot / config_sha256 / the run manifest.  Renaming
# one silently changes every one of those, so the literals are pinned here
# rather than derived from the enum under test.

_ENUM_VOCABULARIES = [
    (WindowMode, {"FIXED": "fixed", "AUTO": "auto"}),
    (CleanWindowProbe, {"INFER": "infer", "BUSY_LOOP": "busy_loop"}),
    (Aggregation, {"MEAN": "mean", "MEDIAN": "median", "TRIMMED": "trimmed"}),
    (PowerFirmware, {"DEDICATED": "dedicated", "SHARED": "shared"}),
]
_ENUM_VOCABULARY_IDS = [enum_cls.__name__ for enum_cls, _ in _ENUM_VOCABULARIES]

#: section, field, enum, default member, the legal-values text an unknown
#: value must produce.
_ENUM_FIELDS = [
    ("profiling", "window_mode", WindowMode, WindowMode.AUTO, "Input should be 'fixed' or 'auto'"),
    (
        "profiling",
        "clean_window_probe",
        CleanWindowProbe,
        CleanWindowProbe.INFER,
        "Input should be 'infer' or 'busy_loop'",
    ),
    (
        "profiling",
        "aggregation",
        Aggregation,
        Aggregation.MEDIAN,
        "Input should be 'mean', 'median' or 'trimmed'",
    ),
    (
        "power",
        "firmware",
        PowerFirmware,
        PowerFirmware.DEDICATED,
        "Input should be 'dedicated' or 'shared'",
    ),
]
_ENUM_FIELD_IDS = [f"{section}.{field}" for section, field, *_ in _ENUM_FIELDS]


def _load_with(section: str | None = None, field: str | None = None, value=None):
    cli = {"model": {"path": "m.tflite"}, "engine": {"type": "helia-rt"}}
    if section is not None:
        cli[section] = {field: value}
    return load_config(None, cli)


@pytest.mark.parametrize(
    ("enum_cls", "expected"), _ENUM_VOCABULARIES, ids=_ENUM_VOCABULARY_IDS
)
def test_config_enum_wire_values_are_stable(enum_cls, expected):
    assert {member.name: member.value for member in enum_cls} == expected


@pytest.mark.parametrize(
    ("enum_cls", "expected"), _ENUM_VOCABULARIES, ids=_ENUM_VOCABULARY_IDS
)
def test_config_enum_members_stay_interchangeable_with_their_strings(enum_cls, expected):
    """StrEnum interop is load-bearing, not cosmetic.

    Templates compare these against string literals, ``power/diagnostics.py``
    tests set membership against a literal frozenset, and json.dumps writes
    them into the manifest — all of which rely on a member comparing, hashing
    and serializing exactly as its value.
    """
    for name, value in expected.items():
        member = enum_cls[name]
        assert member == value
        assert f"{member}" == value
        assert {value: name}[member] == name  # dict-key interop
        assert member in frozenset({value})  # set-membership interop
        assert json.dumps(member) == json.dumps(value)


@pytest.mark.parametrize(
    ("section", "field", "enum_cls", "default", "legal_values"),
    _ENUM_FIELDS,
    ids=_ENUM_FIELD_IDS,
)
def test_config_enum_field_default_when_absent(section, field, enum_cls, default, legal_values):
    config = _load_with()
    assert getattr(getattr(config, section), field) is default


@pytest.mark.parametrize(
    ("section", "field", "enum_cls", "default", "legal_values"),
    _ENUM_FIELDS,
    ids=_ENUM_FIELD_IDS,
)
def test_config_enum_field_accepts_every_legal_string(
    section, field, enum_cls, default, legal_values
):
    for member in enum_cls:
        config = _load_with(section, field, member.value)
        assert getattr(getattr(config, section), field) is member


@pytest.mark.parametrize(
    ("section", "field", "enum_cls", "default", "legal_values"),
    _ENUM_FIELDS,
    ids=_ENUM_FIELD_IDS,
)
def test_config_enum_field_rejects_unknown_value(
    section, field, enum_cls, default, legal_values
):
    with pytest.raises(ConfigError) as excinfo:
        _load_with(section, field, "bogus")
    message = str(excinfo.value)
    assert f"{section}.{field}" in message
    assert legal_values in message


def test_jlink_serial_from_cli():
    """jlink_serial should be settable via CLI overrides."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"jlink_serial": "1160002255"},
    }
    config = load_config(None, cli)
    assert config.target.jlink_serial == "1160002255"


def test_rtt_buffer_size_up_from_cli():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"rtt_buffer_size_up": 16384},
    }
    config = load_config(None, cli)
    assert config.target.rtt_buffer_size_up == 16384


def test_clock_defaults_to_none_selection():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
    }
    config = load_config(None, cli)
    assert config.target.clock.cpu is None


def test_clock_from_cli():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"clock": {"cpu": "hp"}},
    }
    config = load_config(None, cli)
    assert config.target.clock.cpu == "hp"


def test_psram_clock_default_and_override():
    base = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
    }
    assert load_config(None, base).target.psram.clock_hz == 48_000_000

    configured = load_config(
        None,
        {**base, "target": {"psram": {"clock_hz": 125_000_000}}},
    )
    assert configured.target.psram.clock_hz == 125_000_000


def test_invalid_psram_clock_rejected():
    with pytest.raises(ConfigError, match="Unsupported PSRAM clock"):
        load_config(
            None,
            {
                "model": {"path": "test.tflite"},
                "engine": {"type": "helia-rt"},
                "target": {"psram": {"clock_hz": 100_000_000}},
            },
        )


def test_config_is_frozen():
    """ProfileConfig should be immutable."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
    }
    config = load_config(None, cli)

    try:
        # Deliberate invalid assignment: proves the dataclass is frozen.
        config.verbose = 5  # ty: ignore[invalid-assignment]
        assert False, "Should have raised FrozenInstanceError"
    except AttributeError:
        pass


def test_timeouts_defaults():
    """TimeoutsConfig should be populated with defaults when unspecified."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
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
        "engine": {"type": "helia-rt"},
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
        "engine": {"type": "helia-rt"},
    }
    config = load_config(None, cli)
    assert config.build.channel is None
    assert config.build.nsx_modules == {}
    assert config.build.update_dependencies is False
    assert config.build.offline is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"build": {"offline": True, "update_dependencies": True}},
        {"frozen": True, "build": {"update_dependencies": True}},
    ],
)
def test_dependency_update_rejects_offline_modes(overrides: dict):
    with pytest.raises(ConfigError, match="cannot both|cannot both be enabled"):
        load_config(
            None,
            {
                "model": {"path": "test.tflite"},
                "engine": {"type": "helia-rt"},
                **overrides,
            },
        )


def test_engine_defaults_to_helia_rt():
    cli = {
        "model": {"path": "test.tflite"},
    }

    config = load_config(None, cli)

    assert config.engine.type.value == "helia-rt"


def test_tflm_engine_is_accepted():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "tflm"},
    }

    config = load_config(None, cli)

    assert config.engine.type.value == "tflm"


def test_build_config_channel_override():
    """Channel override should flow through from YAML/CLI."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "build": {"channel": "dev"},
    }
    config = load_config(None, cli)
    assert config.build.channel == "dev"


def test_build_config_nsx_module_path_override():
    """Local path override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "build": {
            "nsx_modules": {
                "nsx-core": {"path": "/home/dev/my-nsx-core"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-core"]
    assert override.path == Path("/home/dev/my-nsx-core")
    assert override.ref is None
    assert override.version is None


def test_build_config_nsx_module_ref_override():
    """Git ref override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "build": {
            "nsx_modules": {
                "nsx-cmsis-core": {"ref": "feat/apollo6-support"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-cmsis-core"]
    assert override.ref == "feat/apollo6-support"
    assert override.path is None
    assert override.version is None


def test_build_config_nsx_module_version_override():
    """Version pin override for an NSX module."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "build": {
            "nsx_modules": {
                "nsx-gpio": {"version": "2.0.0"},
            },
        },
    }
    config = load_config(None, cli)
    override = config.build.nsx_modules["nsx-gpio"]
    assert override.version == "2.0.0"
    assert override.path is None
    assert override.ref is None


def test_build_config_multiple_overrides():
    """Multiple NSX module overrides in one config."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "build": {
            "channel": "dev",
            "nsx_modules": {
                "nsx-core": {"path": "/dev/nsx-core"},
                "nsx-cmsis-core": {"ref": "main"},
                "nsx-gpio": {"version": "3.0.0"},
            },
        },
    }
    config = load_config(None, cli)
    assert config.build.channel == "dev"
    assert len(config.build.nsx_modules) == 3
    assert config.build.nsx_modules["nsx-core"].path == Path("/dev/nsx-core")
    assert config.build.nsx_modules["nsx-cmsis-core"].ref == "main"
    assert config.build.nsx_modules["nsx-gpio"].version == "3.0.0"


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
        "engine": {"type": "helia-rt"},
        "build": {"channel": "../escape"},
    }
    with pytest.raises(ConfigError, match="Invalid build.channel"):
        load_config(None, cli)


def test_build_config_channel_rejects_empty():
    """Empty string channel should be rejected."""
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
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
        "engine": {"type": "helia-rt"},
        "build": {
            "nsx_modules": {
                "nsx-core": "just-a-string",
            },
        },
    }
    with pytest.raises(ConfigError, match="must be a mapping"):
        load_config(None, cli)


def test_custom_board_inherits_builtin_board_profile_and_sync_pin():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {
            "board": "apollo510_lab",
            "custom_boards": {
                "apollo510_lab": {
                    "based_on": "apollo510_evb",
                    "default_sync_gpio_pin": 33,
                }
            },
        },
    }

    config = load_config(None, cli)

    board = config.platform_registry.boards["apollo510_lab"]
    assert config.power.sync_gpio_pin == 33
    assert board.profile_source_board == "apollo510_evb"
    assert board.channel == "stable"


def test_custom_soc_and_board_are_available_via_platform_registry():
    cli = {
        "model": {"path": "test.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {
            "board": "apollo510_custom_board",
            "custom_socs": {
                "apollo510_custom": {
                    "based_on": "apollo510",
                    "jlink_device": "AP510-CUSTOM",
                    "rtt_scan_ranges": [[553648128, 1048576]],
                }
            },
            "custom_boards": {
                "apollo510_custom_board": {
                    "soc": "apollo510_custom",
                    "channel": "dev",
                    "starter_profile_board": "apollo510_evb",
                }
            },
        },
    }

    config = load_config(None, cli)
    soc = config.platform_registry.socs["apollo510_custom"]
    board = config.platform_registry.boards["apollo510_custom_board"]

    assert soc.jlink_device == "AP510-CUSTOM"
    assert soc.rtt_scan_ranges == ((553648128, 1048576),)
    assert board.profile_source_board == "apollo510_evb"


# ---------------------------------------------------------------------------
# load_config error wrapping (FIX 3)
# ---------------------------------------------------------------------------


def test_load_config_missing_file_raises_config_error(tmp_path: Path):
    """A --config path that doesn't exist should raise ConfigError, not FileNotFoundError."""
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(missing, {"model": {"path": "m.tflite"}, "engine": {"type": "helia-rt"}})


def test_load_config_malformed_yaml_raises_config_error(tmp_path: Path):
    """Malformed YAML should raise ConfigError, not yaml.YAMLError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("model: [unterminated\n")
    with pytest.raises(ConfigError, match="Malformed YAML"):
        load_config(bad, {})


def test_load_config_non_dict_yaml_raises_config_error(tmp_path: Path):
    """A YAML file whose top-level value is not a mapping should raise ConfigError."""
    not_a_dict = tmp_path / "list.yaml"
    not_a_dict.write_text("- one\n- two\n")
    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        load_config(not_a_dict, {})

    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("just a string\n")
    with pytest.raises(ConfigError, match="must contain a YAML mapping"):
        load_config(scalar, {})


def test_load_config_missing_model_path_raises_config_error():
    """Missing model.path should raise a clear ConfigError, not KeyError."""
    cli = {"engine": {"type": "helia-rt"}}
    with pytest.raises(ConfigError, match="model.path is required"):
        load_config(None, cli)


def test_load_config_bad_toolchain_raises_config_error():
    """An unknown toolchain should raise ConfigError, not ValueError."""
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"toolchain": "bogus-toolchain"},
    }
    with pytest.raises(ConfigError, match="Unknown toolchain"):
        load_config(None, cli)


def test_load_config_duration_s_explicit_null_is_none():
    """power.duration_s: null should behave like an absent key (None), not crash."""
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "power": {"duration_s": None},
    }
    config = load_config(None, cli)
    assert config.power.duration_s is None


def test_load_config_profiling_value_error_wrapped_as_config_error():
    """A bad ProfilingConfig value (e.g. window_min) surfaces as ConfigError via load_config."""
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profiling": {"window_min": 0},
    }
    with pytest.raises(ConfigError, match="window_min must be >= 1"):
        load_config(None, cli)


def test_extreme_mode_and_force_shared_sram_are_mutually_exclusive():
    """The two SSRAM levers pull opposite directions (#161): extreme mode
    powers the shared array down, force_shared_sram forces it on, and the
    firmware partials each assume the other is absent — template include
    order used to decide the winner silently. Reject the pair at config."""
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profiling": {"extreme_mode": True, "force_shared_sram": True},
    }
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config(None, cli)
    # Each lever alone stays valid.
    for lever in ("extreme_mode", "force_shared_sram"):
        cli_one = {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-rt"},
            "profiling": {lever: True},
        }
        load_config(None, cli_one)


# ---------------------------------------------------------------------------
# Output format restrictions (FIX 5)
# ---------------------------------------------------------------------------


def test_model_explorer_rejected_as_primary_output_format():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "output": {"format": "model-explorer"},
    }
    with pytest.raises(ConfigError, match="model-explorer"):
        load_config(None, cli)


# ---------------------------------------------------------------------------
# Toolchain gcc alias normalization (FIX 6)
# ---------------------------------------------------------------------------


def test_gcc_toolchain_alias_normalized_to_arm_none_eabi_gcc():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"toolchain": "gcc"},
    }
    config = load_config(None, cli)
    assert config.target.toolchain is Toolchain.ARM_NONE_EABI_GCC


# ---------------------------------------------------------------------------
# Public exports (FIX 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", importlib.import_module("helia_profiler").__all__)
def test_public_export_resolvable(name: str):
    """Every name in helia_profiler.__all__ must be importable from the package root."""
    pkg = importlib.import_module("helia_profiler")
    assert hasattr(pkg, name), f"helia_profiler.{name} is not resolvable"


# ---------------------------------------------------------------------------
# ProfileResult.power typing (FIX 2)
# ---------------------------------------------------------------------------


def test_profile_result_power_accepts_power_result_and_none():
    from helia_profiler.power.base import PowerResult, PowerSummary
    from helia_profiler.results import FirmwareMeta, PmuResult, ProfileResult

    pmu = PmuResult(meta=FirmwareMeta())
    result_none = ProfileResult(pmu=pmu, power=None)
    assert result_none.power is None

    summary = PowerSummary(
        avg_current_a=0.01,
        avg_power_w=0.033,
        peak_current_a=0.02,
        energy_j=0.1,
        duration_s=1.0,
        sample_count=100,
    )
    power_result = PowerResult(summary=summary)
    result_power = ProfileResult(pmu=pmu, power=power_result)
    assert isinstance(result_power.power, PowerResult)



def test_unknown_top_level_key_includes_path_and_suggestion():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "verbsoe": 2,
    }

    with pytest.raises(ConfigError) as exc_info:
        load_config(None, cli)

    message = str(exc_info.value)
    assert "verbsoe" in message
    assert "verbose" in message



def test_unknown_nested_key_includes_dotted_path_and_suggestion():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profiling": {"iterrations": 5},
    }

    with pytest.raises(ConfigError) as exc_info:
        load_config(None, cli)

    message = str(exc_info.value)
    assert "profiling.iterrations" in message
    assert "iterations" in message



def test_unknown_section_name_includes_suggestion():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profilng": {"iterations": 5},
    }

    with pytest.raises(ConfigError) as exc_info:
        load_config(None, cli)

    message = str(exc_info.value)
    assert "profilng" in message
    assert "profiling" in message



def test_multiple_validation_errors_are_reported_together():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "profilng": {"iterations": 5},
        "profiling": {"iterrations": 5},
        "power": {"stats_rate_hz": 0},
    }

    with pytest.raises(ConfigError) as exc_info:
        load_config(None, cli)

    message = str(exc_info.value)
    assert "profilng" in message
    assert "profiling.iterrations" in message
    assert "power.stats_rate_hz must be >= 1" in message




def test_non_dict_heartbeat_is_rejected():
    cli = {
        "model": {"path": "m.tflite"},
        "engine": {"type": "helia-rt"},
        "target": {"heartbeat": "always"},
    }

    with pytest.raises(ConfigError, match="target.heartbeat"):
        load_config(None, cli)



def test_direct_construction_still_coerces_strings():
    # Deliberate plain strings: this test proves direct construction coerces
    # them to the enum members asserted below.
    profiling = ProfilingConfig(
        aggregation="median",  # ty: ignore[invalid-argument-type]
        window_mode="fixed",  # ty: ignore[invalid-argument-type]
        clean_window_probe="busy_loop",  # ty: ignore[invalid-argument-type]
    )
    target = TargetConfig(toolchain="gcc")  # ty: ignore[invalid-argument-type]
    power = PowerConfig(
        mode="external",  # ty: ignore[invalid-argument-type]
        firmware="shared",  # ty: ignore[invalid-argument-type]
    )

    assert profiling.aggregation is Aggregation.MEDIAN
    assert profiling.window_mode is WindowMode.FIXED
    assert profiling.clean_window_probe is CleanWindowProbe.BUSY_LOOP
    assert target.toolchain is Toolchain.ARM_NONE_EABI_GCC
    assert power.mode is PowerMode.EXTERNAL
    assert power.firmware is PowerFirmware.SHARED



def test_config_snapshot_serialization_is_json_safe():
    config = ProfileConfig(
        model=ModelConfig(path=Path("m.tflite")),
        engine=EngineConfig(type=EngineType.HELIA_RT, config={"backend_mode": "fast"}),
        target=TargetConfig(clock=ClockSelection(cpu="hp")),
    )

    snapshot = serialize_config(config)

    assert snapshot["model"]["path"] == "m.tflite"
    assert snapshot["engine"]["type"] == "helia-rt"
    assert snapshot["target"]["clock"]["cpu"] == "hp"
    json.dumps(snapshot)


@pytest.mark.parametrize(
    ("power_enabled", "window_mode", "window_target_ms", "expected_ms"),
    [
        # The power floor applies only where the window is auto-sized.
        (True, "auto", 1000, 5000),
        (True, "auto", 9000, 9000),
        # "fixed" means use my number -- raising it here would silently run a
        # 5x longer window than the user asked for.
        (True, "fixed", 1000, 1000),
        (True, "fixed", 9000, 9000),
        # No power capture, no reason to lengthen anything.
        (False, "auto", 1000, 1000),
        (False, "fixed", 1000, 1000),
    ],
)
def test_effective_window_target_applies_the_power_floor_only_when_auto_sized(
    power_enabled: bool, window_mode: str, window_target_ms: int, expected_ms: int
):
    """The single source both the firmware render and the power plan read.

    These two used to derive the rule separately and disagreed: the render
    gated the floor on ``window_mode == "auto"`` while the plan applied it
    unconditionally, so a ``fixed`` sub-floor window produced a plan
    describing a 5 s window against firmware built to spin for 1 s.
    """
    config = load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": {"type": "helia-rt"},
            "profiling": {
                "window_mode": window_mode,
                "window_target_ms": window_target_ms,
            },
            "power": {"enabled": power_enabled},
        },
    )

    assert config.effective_window_target_ms == expected_ms
