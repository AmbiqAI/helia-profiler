"""Legacy custom targets retain their prototype's non-declarable controls."""

from dataclasses import replace
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import helia_profiler
from helia_profiler.config import load_config
from helia_profiler.config.platform import config_platform
from helia_profiler.errors import ConfigError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.platform import build_custom_platform_registry
from helia_profiler.platform.soc import CoreArch, SocFamily, SocOrigin
from helia_profiler.results.models import PlatformInfo
from helia_profiler.runs.plan import FrozenRun, freeze_config
from helia_profiler.stages.preflight import _check_transport_support
from helia_profiler.transport.swo import swo_reference_clock_hz
from tests.runs.helpers import ATFE_OUTPUT


@pytest.fixture(autouse=True)
def host_inputs(monkeypatch):
    from helia_profiler.runs.plan import _ENVIRONMENT_VARIABLES, _SOURCE_VARIABLES

    monkeypatch.setattr("helia_profiler.hostenv._proc.tool_output", lambda *a, **k: ATFE_OUTPUT)
    for key in (*_ENVIRONMENT_VARIABLES, *_SOURCE_VARIABLES):
        monkeypatch.delenv(key, raising=False)


def config(tmp_path, base, *, declarations=None, **target):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(64))
    return load_config(
        None,
        {
            "model": {"path": str(model), "arena_size": 4096},
            "engine": {"type": "helia-rt"},
            "target": {
                "board": "child",
                "transport": "rtt",
                "toolchain": "atfe",
                "custom_socs": declarations
                or {"chip": {"based_on": base}, "leaf": {"based_on": "chip"}},
                "custom_boards": {"child": {"soc": "leaf", "channel": "dev"}},
                **target,
            },
        },
    )


def render(partial, **values):
    templates = Path(helia_profiler.__file__).parent / "firmware/templates"
    return Environment(loader=FileSystemLoader(templates)).get_template(partial).render(**values)


@pytest.mark.parametrize(
    "base,enum,usb,radio,trace,cache",
    [
        ("apollo330P", "AM_HAL_PWRCTRL_SRAM_1P75M", True, True, 48, True),
        ("apollo510L", "AM_HAL_PWRCTRL_SRAM_1P75M", True, True, 48, True),
        ("apollo3p", "AM_HAL_PWRCTRL_SRAM_3M", False, False, 48, False),
        ("atomiq110", "AM_HAL_PWRCTRL_SRAM_3M", False, False, 48, True),
        ("apollo4l", "AM_HAL_PWRCTRL_SRAM_3M", True, False, None, False),
        ("apollo4p", "AM_HAL_PWRCTRL_SRAM_3M", True, False, None, False),
        ("apollo510", "AM_HAL_PWRCTRL_SRAM_3M", True, False, None, True),
        ("apollo510b", "AM_HAL_PWRCTRL_SRAM_3M", True, False, None, True),
        ("apollo5b", "AM_HAL_PWRCTRL_SRAM_3M", True, False, None, None),
    ],
)
def test_custom_chain_inherits_resolved_controls(tmp_path, base, enum, usb, radio, trace, cache):
    cfg = config(tmp_path, base)
    for name in ("chip", "leaf"):
        soc = cfg.platform_registry.socs[name]
        assert soc.ssram_full_power_enum == enum
        assert soc.has_usb is usb
        assert soc.has_radio_subsystem is radio
        assert soc.swo_trace_clock_mhz == trace
        assert soc.catalog_has_dcache is cache
        assert soc.origin is SocOrigin.CUSTOM
        assert soc.registered_name is None and soc.catalog_target_id is None
    snapshot = config_platform(cfg)
    assert snapshot.expected_linker is None
    assert snapshot.selection.origin == "hpx-custom"
    if base in ("apollo330P", "apollo510L"):
        text = render(
            "_ssram_power.j2",
            manages_shared_ssram_power=soc.capabilities.memory.has_shared_ssram_power_domain,
            arena_region="sram",
            ssram_full_power_enum=soc.ssram_full_power_enum,
        )
        assert ".eSRAMCfg        = AM_HAL_PWRCTRL_SRAM_1P75M" in text
        assert "AM_HAL_PWRCTRL_SRAM_3M" not in text
    text = render(
        "_crypto_otp_shutdown.j2",
        power_only=True,
        crypto_otp_shutdown=True,
        has_radio_subsystem=soc.has_radio_subsystem,
    )
    assert ("am_hal_pwrctrl_rss_pwroff();" in text) is radio


@pytest.mark.parametrize("base", ["apollo3p", "atomiq110"])
def test_inherited_usb_restriction_reaches_preflight(tmp_path, base):
    cfg = config(tmp_path, base, transport="usb_cdc")
    with pytest.raises(ConfigError, match="has no USB device support"):
        _check_transport_support(cfg)


@pytest.mark.parametrize("base,cpu_mhz", [("apollo3p", 96), ("atomiq110", 25)])
def test_swo_uses_inherited_trace_policy_instead_of_cpu(tmp_path, base, cpu_mhz):
    cfg = config(tmp_path, base)
    ctx = PipelineContext(config=cfg, work_dir=tmp_path / "work")
    ctx.soc = cfg.platform_registry.socs["leaf"]
    ctx.run_metadata.platform = PlatformInfo(cpu_clock_mhz=cpu_mhz)
    assert swo_reference_clock_hz(ctx) == 48_000_000


@pytest.mark.parametrize(
    "base,family,core,mve,cache",
    [
        ("apollo510", "ap4", "cortex-m4", False, True),
        ("apollo3p", "ap5", "cortex-m55", True, False),
        ("apollo5b", "ap4", "cortex-m4", False, None),
    ],
)
def test_known_cache_fact_wins_over_explicit_family_fallback(
    tmp_path, base, family, core, mve, cache
):
    cfg = config(
        tmp_path,
        base,
        declarations={"leaf": {"based_on": base, "family": family, "core": core, "has_mve": mve}},
    )
    soc = config_platform(cfg).soc
    assert soc.family is SocFamily(family)
    assert soc.core is CoreArch(core)
    assert soc.has_mve is mve and soc.catalog_has_dcache is cache
    has_dcache = soc.capabilities.memory.has_dcache
    assert has_dcache is (cache if cache is not None else family == "ap5")
    text = render("_hpx_printf.j2", transport="rtt", has_dcache=has_dcache)
    assert ("SCB_InvalidateDCache_by_Addr(" in text) is has_dcache


@pytest.mark.parametrize(
    "route,expected",
    [
        ({"c_define": "AM_PART_APOLLO510"}, False),
        ({"cmsis_header": "apollo510.h"}, False),
        ({"c_define": "AM_PART_UNKNOWN", "cmsis_header": "unknown.h"}, False),
        ({"c_define": "AM_PART_APOLLO510L", "cmsis_header": "apollo510L.h"}, True),
    ],
)
def test_builtin_ancestor_rechecks_effective_sdk_route(tmp_path, route, expected):
    cfg = config(tmp_path, "apollo330P", declarations={"leaf": {"based_on": "apollo330P", **route}})
    soc = config_platform(cfg).soc
    text = render(
        "_crypto_otp_shutdown.j2",
        power_only=True,
        crypto_otp_shutdown=True,
        has_radio_subsystem=soc.has_radio_subsystem,
    )
    assert ("am_hal_pwrctrl_rss_pwroff();" in text) is expected


@pytest.mark.parametrize("base,expected", [("apollo330P", True), ("apollo510", False)])
def test_sdk_route_recovery_preserves_ancestor_eligibility(tmp_path, base, expected):
    cfg = config(
        tmp_path,
        base,
        declarations={
            "chip": {
                "based_on": base,
                "c_define": "AM_PART_UNKNOWN",
                "cmsis_header": "unknown.h",
            },
            "leaf": {
                "based_on": "chip",
                "c_define": "AM_PART_APOLLO510L",
                "cmsis_header": "apollo510L.h",
            },
        },
    )
    assert not cfg.platform_registry.socs["chip"].has_radio_subsystem
    assert cfg.platform_registry.socs["leaf"].has_radio_subsystem is expected


def test_explicit_custom_values_survive_inherited_controls(tmp_path):
    cfg = config(
        tmp_path,
        "apollo330P",
        declarations={
            "leaf": {
                "based_on": "apollo330P",
                "memory": {"sram_kb": 512},
                "clocks": [{"name": "cpu", "speeds": [{"name": "lp", "mhz": 12}], "default": "lp"}],
                "jlink_device": "custom-probe",
                "rtt_scan_ranges": [[0x20001000, 4096]],
                "app_flash_load_addr": 0x420000,
            }
        },
        custom_boards={"child": {"soc": "leaf", "channel": "dev", "default_go_gpio_pin": 17}},
    )
    snapshot = config_platform(cfg)
    soc = snapshot.soc
    assert soc.memory.sram_kb == 512
    assert soc.clocks[0].speeds[0].mhz == 12
    assert soc.jlink_device == "custom-probe" and soc.rtt_scan_ranges == ((0x20001000, 4096),)
    assert snapshot.load_address == 0x420000 and snapshot.board.default_go_gpio_pin == 17
    assert soc.ssram_full_power_enum == "AM_HAL_PWRCTRL_SRAM_1P75M"


def test_builtin_name_shadow_remains_custom_and_refuses_fixed_fixture(tmp_path):
    from helia_profiler.fixtures.build import _validate
    from tests.fixtures_api.test_fixed_fixture import fixture

    cfg, pinned = fixture(tmp_path)
    declarations = {"apollo510": {"based_on": "apollo510"}}
    registry = build_custom_platform_registry({"custom_socs": declarations})
    cfg = replace(
        cfg,
        target=replace(cfg.target, custom_socs=declarations),
        platform_registry=registry,
    )
    snapshot = config_platform(cfg)
    assert snapshot.soc.origin is SocOrigin.CUSTOM
    assert snapshot.soc.catalog_has_dcache is True
    assert snapshot.soc.registered_name is None and snapshot.soc.catalog_target_id is None
    assert snapshot.expected_linker is None
    with pytest.raises(ConfigError, match="custom target declarations"):
        _validate(cfg, pinned)


def test_unused_soc_declaration_and_board_only_customization_preserve_builtin_inputs(tmp_path):
    builtin = config(
        tmp_path, "apollo330P", board="apollo330mP_evb", custom_socs={}, custom_boards={}
    )
    unused = config(tmp_path, "apollo330P", board="apollo330mP_evb", custom_boards={})
    assert config_platform(unused) == config_platform(builtin)
    board_only = config(
        tmp_path,
        "apollo330P",
        custom_socs={},
        custom_boards={"child": {"based_on": "apollo330mP_evb"}},
    )
    assert config_platform(board_only).soc == config_platform(builtin).soc


def test_resolving_another_custom_config_does_not_change_existing_registry(tmp_path):
    first = config(tmp_path, "apollo330P")
    retained = config_platform(first)
    second = config(tmp_path, "apollo3p")
    assert not config_platform(second).soc.has_usb
    assert config_platform(first) == retained
    assert retained.soc.has_usb and retained.soc.has_radio_subsystem


@pytest.mark.parametrize("base", ["apollo330P", "apollo510"])
def test_old_custom_plan_reads_unchanged_offline_but_requires_refreeze(tmp_path, monkeypatch, base):
    cfg = config(tmp_path, base)
    plan = freeze_config(cfg)
    assert plan.platform is not None
    old_soc = replace(
        plan.platform.soc,
        swo_trace_clock_mhz=None,
        has_usb=True,
        ssram_full_power_enum="AM_HAL_PWRCTRL_SRAM_3M",
        has_radio_subsystem=False,
        catalog_has_dcache=None,
    )
    old = replace(plan, platform=replace(plan.platform, soc=old_soc))
    with monkeypatch.context() as offline:
        offline.setattr(
            "helia_profiler.platform.catalog._builtin_catalog",
            lambda: pytest.fail("offline plan reader reopened catalog"),
        )
        restored = FrozenRun.from_dict(old.to_dict())
        assert restored.platform == old.platform
        assert restored.intent_hash == old.intent_hash
    with pytest.raises(ConfigError, match="different run"):
        restored.config()
    assert plan.intent_hash != old.intent_hash
    assert (
        freeze_config(FrozenRun.from_dict(plan.to_dict()).config()).intent_hash == plan.intent_hash
    )
