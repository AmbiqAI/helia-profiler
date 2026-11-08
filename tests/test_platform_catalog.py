"""Real catalog projections and the provenance boundary used by saved artifacts."""

from dataclasses import replace

import pytest
from helia_platforms import builtin

from helia_profiler.platform import build_platform_registry, get_soc_for_board
from helia_profiler.platform.provenance import PlatformSnapshot, platform_snapshot


def test_supported_names_have_explicit_source_identity():
    registry = build_platform_registry()
    assert len(registry.boards) == 13
    assert registry.catalog is not None
    assert (
        registry.catalog.catalog_digest == builtin(include_plugins=False).identity().catalog_digest
    )
    for board in registry.boards:
        selection = registry.selections[board]
        if board == "apollo5b_evb":
            assert selection.origin == "sdk-descriptor"
            assert selection.catalog is None and selection.target_id is None
        else:
            target = builtin(include_plugins=False).resolve(board)
            assert selection.origin == "catalog"
            assert selection.target_id == target.id
            assert selection.catalog == registry.catalog


def test_board_facts_respect_fitted_memory_and_concrete_part():
    registry = build_platform_registry()
    assert get_soc_for_board("apollo3p_evb", registry=registry).memory.psram_kb == 0
    assert get_soc_for_board("apollo3p_evb_cygnus", registry=registry).memory.psram_kb == 8192
    composed = registry.selections["apollo3p_evb_cygnus"]
    assert composed.target_id == "apollo3p_evb" and composed.addons == ("cygnus",)
    board = "apollo4p_blue_kxr_evb"
    assert get_soc_for_board(board, registry=registry).jlink_device == (
        builtin(include_plugins=False).resolve(board).jlink_device
    )


def test_discovery_is_not_enabled_by_an_environment_variable(tmp_path, monkeypatch):
    broken = tmp_path / "broken.yaml"
    broken.write_text("not a catalog")
    monkeypatch.setenv("HELIA_PLATFORMS_PATH", str(broken))
    from helia_profiler.platform.catalog import catalog_views

    catalog_views.cache_clear()
    try:
        catalog = platform_snapshot("apollo510_evb").selection.catalog
        assert catalog is not None and catalog.sources == ()
    finally:
        catalog_views.cache_clear()


def test_per_run_custom_override_is_not_relabelled_catalog():
    base = build_platform_registry()
    custom = replace(base.socs["apollo510"], jlink_device="ExplicitProbe")
    registry = build_platform_registry(base=base, socs={custom.name: custom})
    assert get_soc_for_board("apollo510_evb", registry=registry).jlink_device == "ExplicitProbe"
    assert get_soc_for_board("apollo510_evb", registry=base).jlink_device != "ExplicitProbe"
    assert registry.selections["apollo510_evb"].origin == "hpx-custom"


def test_snapshot_roundtrip_and_catalog_drift_refusal(monkeypatch):
    original = platform_snapshot("apollo510_evb")
    assert PlatformSnapshot.from_dict(original.to_dict()) == original
    assert original.selection.catalog is not None
    catalog = replace(original.selection.catalog, catalog_digest="sha256:" + "a" * 64)
    drifted = replace(original, selection=replace(original.selection, catalog=catalog))
    monkeypatch.setattr(
        "helia_profiler.platform.provenance.platform_snapshot", lambda *a, **k: drifted
    )
    with pytest.raises(ValueError, match="matching HPX/catalog environment"):
        original.verify_current()


@pytest.mark.parametrize("mutate", ["missing", "extra", "wrong-type"])
def test_snapshot_refuses_noncanonical_recorded_facts(mutate):
    document = platform_snapshot("apollo510_evb").to_dict()
    if mutate == "missing":
        del document["selection"]
    elif mutate == "extra":
        document["soc"]["unrecorded"] = "anything"
    else:
        document["soc"]["memory"]["dtcm_kb"] = True
    with pytest.raises(ValueError):
        PlatformSnapshot.from_dict(document)


def test_ordinary_artifacts_keep_the_same_resolved_platform(tmp_path):
    import json

    from helia_profiler.report import write_report
    from helia_profiler.results import FirmwareMeta, LayerResult, PmuResult, PresetResult
    from tests.contracts.conftest import make_pmu_ctx
    from tests.pipeline_context_helpers import set_profile_result

    ctx = make_pmu_ctx(
        tmp_path, board="apollo510_evb", extra={"output": {"dir": str(tmp_path / "results")}}
    )
    lock = tmp_path / "nsx.lock"
    lock.write_text("schema_version: 4\ntargets: {}\n")
    ctx.dependency_lock_path = lock
    ctx.run_metadata.run_id = "catalog-test"
    ctx.run_metadata.timestamp = "2026-11-07T00:00:00Z"
    ctx.run_metadata.hpx_version = "0.0.0"
    layer = LayerResult(id=0, op="ADD", counters={}, cycles=100.0)
    result = PmuResult(
        meta=FirmwareMeta(),
        presets={"cpu": PresetResult(name="cpu", layers=[layer])},
        layers=[layer],
    )
    set_profile_result(ctx, result)
    write_report(ctx)
    assert ctx.run_metadata.platform_provenance is not None
    expected = ctx.run_metadata.platform_provenance.to_dict()
    metadata = json.loads((tmp_path / "results/run_metadata.json").read_text())
    summary = json.loads((tmp_path / "results/summary.json").read_text())
    manifest = json.loads((tmp_path / "results/result_manifest.json").read_text())
    assert metadata["platform_provenance"] == expected
    assert summary["platform_provenance"] == expected
    assert manifest["provenance"]["platform"] == expected

    from helia_profiler.api import load_result, observations
    from helia_profiler.readers.native import ProfileResultRecord

    record = load_result(tmp_path / "results")
    assert isinstance(record, ProfileResultRecord) and record.metadata is not None
    assert record.metadata["platform_provenance"] == expected
    assert observations(record)["producer"]["name"] == "helia-profiler"


@pytest.mark.parametrize("profile", ["default", "itcm"])
def test_retained_windows_use_the_selected_aot_linker_profile(tmp_path, profile):
    from helia_profiler.config import load_config
    from helia_profiler.config.platform import config_platform

    config = load_config(
        None,
        {
            "model": {"path": str(tmp_path / "model.tflite")},
            "engine": {"type": "helia-aot", "config": {"linker_profile": profile}},
        },
    )
    snapshot = config_platform(config)
    assert snapshot.linker_profile == profile
    if profile == "itcm":
        assert snapshot.app_regions == ()
    else:
        assert snapshot.app_regions


@pytest.mark.parametrize(
    "board,device",
    [
        ("apollo4l_blue_evb", "AMA4B2KL-KXR"),
        ("apollo4p_blue_kbr_evb", "AMA4B2KP-KBR"),
        ("apollo4p_blue_kxr_evb", "AMA4B2KP-KXR"),
    ],
)
def test_custom_board_inherits_concrete_probe_facts_through_a_chain(board, device):
    from helia_profiler.platform.custom import build_custom_platform_registry

    registry = build_custom_platform_registry(
        {
            "custom_boards": {
                "derived": {"based_on": board, "default_sync_gpio_pin": 12},
                "child": {"based_on": "derived"},
            }
        }
    )
    for name in ("derived", "child"):
        snapshot = platform_snapshot(name, registry=registry)
        assert snapshot.soc.jlink_device == device
        assert snapshot.board.default_sync_gpio_pin == 12
        assert snapshot.selection.origin == "hpx-custom"


def test_custom_board_inherits_an_explicit_soc_override():
    from helia_profiler.platform.custom import build_custom_platform_registry

    registry = build_custom_platform_registry(
        {
            "custom_socs": {"apollo4l": {"based_on": "apollo4l", "jlink_device": "ExplicitProbe"}},
            "custom_boards": {"derived": {"based_on": "apollo4l_blue_evb"}},
        }
    )
    assert get_soc_for_board("derived", registry=registry).jlink_device == "ExplicitProbe"
    assert get_soc_for_board("apollo4l_blue_evb").jlink_device == "AMA4B2KL-KXR"


def test_custom_board_with_another_soc_does_not_inherit_the_base_probe():
    from helia_profiler.platform.custom import build_custom_platform_registry

    registry = build_custom_platform_registry(
        {
            "custom_boards": {
                "different": {"based_on": "apollo4l_blue_evb", "soc": "apollo510"},
            }
        }
    )
    assert get_soc_for_board("different", registry=registry).jlink_device == "AP510NFA-CBR"
