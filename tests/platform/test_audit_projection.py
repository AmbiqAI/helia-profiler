"""Consumer checks for catalog address and SDK capability projection."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from helia_platforms import builtin
from jinja2 import Environment, FileSystemLoader

import helia_profiler
from helia_profiler.config import load_config
from helia_profiler.config.platform import config_platform
from helia_profiler.errors import ConfigError, PlatformError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import Placement, placement_capacities
from helia_profiler.platform import get_soc_for_board
from helia_profiler.platform.catalog import application_regions
from helia_profiler.platform.placement import MemoryRegion
from helia_profiler.platform.provenance import PlatformSnapshot, TargetSelection
from helia_profiler.platform.soc import MemoryRange
from helia_profiler.runs.plan import FrozenRun, freeze_config
from helia_profiler.stages.plan_memory import _resolve_placement
from tests.runs.helpers import ATFE_OUTPUT


@pytest.fixture(autouse=True)
def host_inputs(monkeypatch):
    from helia_profiler.runs.plan import _ENVIRONMENT_VARIABLES, _SOURCE_VARIABLES

    monkeypatch.setattr("helia_profiler.hostenv._proc.tool_output", lambda *a, **k: ATFE_OUTPUT)
    for key in (*_ENVIRONMENT_VARIABLES, *_SOURCE_VARIABLES):
        monkeypatch.delenv(key, raising=False)


def config(tmp_path, board="apollo3p_evb_cygnus", **target):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(64))
    return load_config(
        None,
        {
            "model": {"path": str(model), "weights_location": "psram", "arena_size": 4096},
            "target": {"board": board, "transport": "rtt", "toolchain": "atfe", **target},
        },
    )


def placement(cfg, tmp_path):
    ctx = PipelineContext(config=cfg, work_dir=tmp_path / "work")
    ctx.soc = get_soc_for_board(cfg.target.board, registry=cfg.platform_registry)
    ctx.run_metadata.platform_provenance = config_platform(cfg)
    return _resolve_placement(ctx)


@pytest.mark.parametrize("toolchain", ["atfe", "armclang"])
def test_cygnus_catalog_aperture_reaches_frozen_plan_and_placement(tmp_path, toolchain):
    cfg = config(tmp_path, toolchain=toolchain)
    snapshot = config_platform(cfg)
    assert snapshot.expected_linker is not None
    region = next(r for r in snapshot.expected_linker.regions if r.region is MemoryRegion.PSRAM)
    assert region.window == MemoryRange(0x54000000, 8 * 1024 * 1024)
    assert region.allowed == (region.window,)
    assert not region.section_attributable
    assert placement_capacities(snapshot.soc, snapshot.expected_linker).psram == 8 * 1024 * 1024
    assert placement(cfg, tmp_path)[1] is Placement.PSRAM


def test_physical_absence_still_refuses_and_known_ap510_still_plans(tmp_path):
    with pytest.raises(PlatformError, match="has no PSRAM"):
        placement(config(tmp_path, board="apollo3p_evb"), tmp_path)
    cfg = config(tmp_path, board="apollo510_evb")
    snapshot = config_platform(cfg)
    assert placement_capacities(snapshot.soc, snapshot.expected_linker).psram == 64 * 1024 * 1024
    assert placement(cfg, tmp_path)[1] is Placement.PSRAM


@pytest.mark.parametrize("kind", ["unknown-aperture", "empty-application", "multiple-apertures"])
def test_unusable_declared_psram_is_not_reported_as_absent(tmp_path, monkeypatch, kind):
    cfg = config(tmp_path)
    snapshot = config_platform(cfg)
    facts = application_regions(snapshot.selection)
    assert facts is not None
    changed = []
    for fact in facts:
        if fact.region is MemoryRegion.PSRAM:
            if kind == "unknown-aperture":
                fact = replace(fact, aperture=None)
            elif kind == "empty-application":
                fact = replace(fact, intervals=())
            else:
                changed.append(replace(fact, name="second"))
        changed.append(fact)
    monkeypatch.setattr(
        "helia_profiler.platform.catalog.application_regions", lambda s: tuple(changed)
    )
    with pytest.raises(PlatformError) as error:
        placement(cfg, tmp_path)
    assert "has no PSRAM" not in str(error.value)
    assert ("unavailable" if kind != "empty-application" else "no usable") in str(error.value)


def test_aperture_and_application_ranges_share_core_alias_translation(monkeypatch):
    region = SimpleNamespace(name="psram0", technology="psram", base=0x50000000, size=8192)
    target = SimpleNamespace(
        application_ranges=lambda *a, **k: {
            "psram0": (SimpleNamespace(start=0x50001000, size=4096),)
        },
        memories=lambda *a, **k: (region,),
        reachable=lambda *a: SimpleNamespace(address=0x54000000, access="direct"),
    )
    monkeypatch.setattr(
        "helia_profiler.platform.catalog._builtin_catalog",
        lambda: SimpleNamespace(resolve=lambda *a, **k: target),
    )
    selection = TargetSelection(
        origin="catalog", catalog=None, target_id="test", boot="sbl", core="cpu"
    )
    facts = application_regions(selection)
    assert facts is not None
    assert facts[0].aperture == MemoryRange(0x54000000, 8192)
    assert facts[0].intervals == (MemoryRange(0x54001000, 4096),)


@pytest.mark.parametrize("board", ["apollo3p_evb_cygnus", "apollo510_evb"])
def test_selected_aperture_bounds_and_fragments_reach_admission(tmp_path, monkeypatch, board):
    cfg = config(tmp_path, board=board)
    facts = application_regions(config_platform(cfg).selection)
    assert facts is not None
    base = 0x64000000
    changed = tuple(
        replace(
            fact,
            aperture=MemoryRange(base, 8192),
            intervals=(MemoryRange(base, 2048), MemoryRange(base + 4096, 4096)),
        )
        if fact.region is MemoryRegion.PSRAM
        else fact
        for fact in facts
    )
    monkeypatch.setattr("helia_profiler.platform.catalog.application_regions", lambda s: changed)
    snapshot = config_platform(cfg)
    assert snapshot.expected_linker is not None
    region = next(r for r in snapshot.expected_linker.regions if r.region is MemoryRegion.PSRAM)
    assert region.window == MemoryRange(base, 8192)
    assert region.allowed == (MemoryRange(base, 2048), MemoryRange(base + 4096, 4096))
    assert region.capacity == 4096
    assert not region.section_attributable


def test_catalog_aperture_cannot_expand_declared_capacity(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    facts = application_regions(config_platform(cfg).selection)
    assert facts is not None
    base = 0x54000000
    changed = tuple(
        replace(
            fact,
            aperture=MemoryRange(base, 16 * 1024 * 1024),
            intervals=(MemoryRange(base, 16 * 1024 * 1024),),
        )
        if fact.region is MemoryRegion.PSRAM
        else fact
        for fact in facts
    )
    monkeypatch.setattr("helia_profiler.platform.catalog.application_regions", lambda s: changed)
    snapshot = config_platform(cfg)
    assert snapshot.expected_linker is not None
    assert placement_capacities(snapshot.soc, snapshot.expected_linker).psram == 8 * 1024 * 1024


@pytest.mark.parametrize(
    "define,header,expected",
    [
        ("AM_PART_APOLLO330P", "apollo330P.h", True),
        ("AM_PART_APOLLO510L", "apollo510L.h", True),
        ("AM_PART_APOLLO510", "apollo510.h", False),
        ("AM_PART_APOLLO330P", "apollo510.h", False),
        ("AM_PART_FUTURE", "apollo330P.h", False),
    ],
)
def test_rss_power_off_support_requires_known_sdk_route(define, header, expected):
    from helia_profiler.platform.sdk_facts import rss_power_off_available

    assert rss_power_off_available(define, header) is expected


def test_saved_cygnus_window_reads_offline_and_old_assumption_refuses_replay(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    plan = freeze_config(cfg)
    assert plan.platform is not None and plan.platform.expected_linker is not None
    current = plan.platform
    assert current.expected_linker is not None
    old_layout = replace(
        current.expected_linker,
        regions=tuple(
            r for r in current.expected_linker.regions if r.region is not MemoryRegion.PSRAM
        ),
    )
    old = replace(
        plan,
        platform=replace(
            current,
            expected_linker=old_layout,
            app_regions=tuple(r for r in current.app_regions if r.region is not MemoryRegion.PSRAM),
        ),
    )
    loaded = FrozenRun.from_dict(old.to_dict())
    with pytest.raises(ConfigError, match="different run"):
        loaded.config()
    assert (
        FrozenRun.from_dict(plan.to_dict()).intent_hash == freeze_config(plan.config()).intent_hash
    )

    def unavailable():
        raise AssertionError("offline snapshot reader reopened the catalog")

    monkeypatch.setattr("helia_profiler.platform.catalog._builtin_catalog", unavailable)
    restored = PlatformSnapshot.from_dict(current.to_dict())
    assert restored == current


def native_radio_config(tmp_path, soc="apollo510", radio=True, declarations=None):
    target = builtin(include_plugins=False).resolve(soc)
    core = target.cores()[0]
    options = core.options
    cores = [
        {
            "id": core.id,
            "role": core.role,
            "core": core.core,
            "clock_domain": core.clock_domain,
            "programmable_by": core.programmable_by,
            "options": {
                "fpu": options.fpu,
                "mve": options.mve,
                "fpu_dp": options.fpu_dp,
                "icache_kb": options.icache_kb,
                "dcache_kb": options.dcache_kb,
            },
        }
    ]
    for extra in target.cores()[1:]:
        extra_options = extra.options
        cores.append(
            {
                "id": extra.id,
                "role": extra.role if radio else "other",
                "core": extra.core,
                "clock_domain": extra.clock_domain,
                "programmable_by": extra.programmable_by,
                "options": {
                    "fpu": extra_options.fpu,
                    "mve": extra_options.mve,
                    "fpu_dp": extra_options.fpu_dp,
                    "icache_kb": extra_options.icache_kb or 0,
                    "dcache_kb": extra_options.dcache_kb or 0,
                },
            }
        )
    if radio and not any(c.role == "radio" for c in target.cores()):
        cores.append(
            {
                "id": "radio_0",
                "role": "radio",
                "core": "cortex-m4",
                "clock_domain": core.clock_domain,
                "options": {"fpu": "fpv4-sp-d16", "mve": "none"},
            }
        )
    path = tmp_path / "platform.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "socs": {"native": {"based_on": soc, "cores": cores}}})
    )
    profile = {
        "apollo510": "apollo510_evb",
        "apollo330P": "apollo330mP_evb",
        "apollo510L": "apollo510dL_evb",
    }[soc]
    return config(
        tmp_path,
        board="child",
        platform_overlay=str(path),
        custom_socs=declarations or {"child_chip": {"based_on": "native"}},
        custom_boards={
            "child": {
                "soc": "leaf" if declarations and "leaf" in declarations else "child_chip",
                "channel": "dev",
                "starter_profile_board": profile,
            }
        },
    )


@pytest.mark.parametrize(
    "soc,radio,expected",
    [
        ("apollo510", True, False),
        ("apollo330P", True, True),
        ("apollo510L", True, True),
        ("apollo330P", False, False),
    ],
)
def test_radio_emission_requires_sdk_api_and_declared_subsystem(tmp_path, soc, radio, expected):
    cfg = native_radio_config(tmp_path, soc, radio)
    snapshot = config_platform(cfg)
    assert snapshot.soc.has_radio_subsystem is expected
    assert not snapshot.soc.is_builtin and snapshot.expected_linker is None
    templates = Path(helia_profiler.__file__).parent / "firmware/templates"
    rendered = (
        Environment(loader=FileSystemLoader(templates))
        .get_template("_crypto_otp_shutdown.j2")
        .render(
            power_only=True,
            crypto_otp_shutdown=True,
            has_radio_subsystem=snapshot.soc.has_radio_subsystem,
        )
    )
    assert ("am_hal_pwrctrl_rss_pwroff();" in rendered) is expected


@pytest.mark.parametrize("at_leaf", [False, True])
@pytest.mark.parametrize(
    "route,expected",
    [
        ({"c_define": "AM_PART_APOLLO510", "cmsis_header": "apollo510.h"}, False),
        ({"c_define": "AM_PART_APOLLO510"}, False),
        ({"cmsis_header": "apollo510.h"}, False),
        ({"c_define": "AM_PART_APOLLO510L", "cmsis_header": "apollo510L.h"}, True),
    ],
)
def test_native_custom_route_override_rechecks_shutdown_api(tmp_path, route, expected, at_leaf):
    declarations = {"child_chip": {"based_on": "native"}, "leaf": {"based_on": "child_chip"}}
    declarations["leaf" if at_leaf else "child_chip"].update(route)
    cfg = native_radio_config(tmp_path, soc="apollo330P", declarations=declarations)
    soc = config_platform(cfg).soc
    assert soc.has_radio_subsystem is expected
    templates = Path(helia_profiler.__file__).parent / "firmware/templates"
    rendered = (
        Environment(loader=FileSystemLoader(templates))
        .get_template("_crypto_otp_shutdown.j2")
        .render(
            power_only=True, crypto_otp_shutdown=True, has_radio_subsystem=soc.has_radio_subsystem
        )
    )
    assert ("am_hal_pwrctrl_rss_pwroff();" in rendered) is expected


def test_native_custom_corrected_shutdown_assumption_refuses_saved_replay(tmp_path, monkeypatch):
    cfg = native_radio_config(
        tmp_path,
        soc="apollo330P",
        declarations={
            "child_chip": {
                "based_on": "native",
                "c_define": "AM_PART_APOLLO510",
                "cmsis_header": "apollo510.h",
            }
        },
    )
    plan = freeze_config(cfg)
    assert plan.platform is not None
    old = replace(
        plan,
        platform=replace(plan.platform, soc=replace(plan.platform.soc, has_radio_subsystem=True)),
    )
    with monkeypatch.context() as offline:
        offline.setattr(
            "helia_profiler.platform.catalog._builtin_catalog",
            lambda: pytest.fail("offline reader reopened catalog"),
        )
        restored = FrozenRun.from_dict(old.to_dict())
    with pytest.raises(ConfigError, match="different run"):
        restored.config()
    assert FrozenRun.from_dict(plan.to_dict()).config().platform_registry == cfg.platform_registry
