from __future__ import annotations

import pytest

from helia_profiler.platform.counters import (
    DEFAULT_COUNTERS,
    EXTENDED_COUNTERS,
    GROUPS,
    MAX_COUNTERS_PER_PASS,
    get_counter,
    list_counters,
    plan_passes,
    resolve_counters,
    supported_groups_for_domains,
    validate_group_selection,
)


def test_groups_exist():
    assert "cpu" in GROUPS
    assert "mve" in GROUPS
    assert "memory" in GROUPS


def test_catalog_matches_upstream_export_size():
    # 70 ARM PMU counters from the upstream export + 9 Ethos-U NPU events
    # + 22 name-only Ethos-U85 diagnostic events.
    counters = list_counters()
    arm = [c for c in counters if c.name.startswith("ARM_PMU_")]
    npu = [c for c in counters if c.name.startswith("ETHOSU_PMU_")]
    assert len(arm) == 70
    assert len(npu) == 9 + 22
    assert len(counters) == 101


def test_catalog_includes_noncontiguous_unaligned_mve_counter():
    counter = get_counter("ARM_PMU_MVE_LDST_UNALIGNED_NONCONTIG_RETIRED")
    assert counter.event_id == 0x0298
    assert counter.group == "mve"


def test_catalog_uses_upstream_descriptions():
    counter = get_counter("ARM_PMU_INST_RETIRED")
    assert counter.description == "Instruction architecturally executed"


def test_default_counters_fit_one_pass():
    for group, names in DEFAULT_COUNTERS.items():
        assert len(names) <= MAX_COUNTERS_PER_PASS, (
            f"Default counters for '{group}' exceed {MAX_COUNTERS_PER_PASS}"
        )


def test_resolve_default():
    counters = resolve_counters({"cpu": "default"})
    assert len(counters) <= MAX_COUNTERS_PER_PASS
    for c in counters:
        assert c.group == "cpu"


def test_resolve_all():
    counters = resolve_counters({"mve": "all"})
    assert len(counters) == len(GROUPS["mve"])


def test_resolve_explicit_names():
    counters = resolve_counters(
        {
            "cpu": ["ARM_PMU_CPU_CYCLES", "ARM_PMU_INST_RETIRED"],
        }
    )
    assert len(counters) == 2
    names = {c.name for c in counters}
    assert "ARM_PMU_CPU_CYCLES" in names
    assert "ARM_PMU_INST_RETIRED" in names


def test_resolve_unknown_counter_raises():
    with pytest.raises(ValueError):
        resolve_counters({"cpu": ["NONEXISTENT_COUNTER"]})


def test_plan_passes_single_pass():
    counters = resolve_counters({"cpu": "default"})
    passes = plan_passes(counters)
    assert len(passes) == 1
    assert passes[0].group == "cpu"
    assert len(passes[0].counters) <= MAX_COUNTERS_PER_PASS


def test_plan_passes_multi_pass():
    counters = resolve_counters({"mve": "all"})
    passes = plan_passes(counters)
    # With 34 MVE counters and 4 per pass, should need 9 passes
    expected = -(-len(GROUPS["mve"]) // MAX_COUNTERS_PER_PASS)
    assert len(passes) == expected
    for p in passes:
        assert len(p.counters) <= MAX_COUNTERS_PER_PASS
        assert p.group == "mve"


def test_plan_passes_mixed_groups():
    counters = resolve_counters({"cpu": "default", "mve": "default"})
    passes = plan_passes(counters)
    groups = {p.group for p in passes}
    assert "cpu" in groups
    assert "mve" in groups


def test_supported_groups_for_domains_filters_unknown_domains():
    groups = supported_groups_for_domains(("cpu", "memory", "mve", "custom"))
    assert groups == ("cpu", "memory", "mve")


def test_validate_group_selection_rejects_unsupported_groups():
    with pytest.raises(ValueError, match="not supported"):
        validate_group_selection({"mve": "default"}, supported_groups=("cpu",))


def test_ethos_npu_group_registered():
    counters = resolve_counters({"ethos_npu": "all"})
    assert len(counters) == 9  # U85 catalogue
    for c in counters:
        assert c.group == "ethos_npu"
        assert c.name.startswith("ETHOSU_PMU_")


def test_ethos_npu_default_fits_one_pass():
    counters = resolve_counters({"ethos_npu": "default"})
    passes = plan_passes(counters)
    assert len(passes) == 1
    assert passes[0].group == "ethos_npu"
    names = [c.name for c in passes[0].counters]
    assert "ETHOSU_PMU_CYCLE" in names
    assert "ETHOSU_PMU_NPU_ACTIVE" in names


def test_ethos_npu_gated_by_domain():
    assert "ethos_npu" in supported_groups_for_domains(("cpu", "ethos_npu"))
    assert "ethos_npu" not in supported_groups_for_domains(("cpu", "memory", "mve"))
    with pytest.raises(ValueError, match="not supported"):
        validate_group_selection(
            {"ethos_npu": "default"}, supported_groups=("cpu", "memory", "mve")
        )


def test_ethos_npu_all_excludes_extended_events():
    names = [c.name for c in resolve_counters({"ethos_npu": "all"})]
    extended = EXTENDED_COUNTERS["ethos_npu"]
    assert len(extended) == 22
    assert len(set(extended)) == 22
    assert not set(names) & set(extended)
    assert not set(DEFAULT_COUNTERS["ethos_npu"]) & set(extended)
    assert not set(GROUPS["ethos_npu"]) & set(extended)


def test_ethos_npu_extended_events_resolve_by_name_in_order():
    wanted = [
        "ETHOSU_PMU_SRAM1_RD_DATA_BEAT_RECEIVED",
        "ETHOSU_PMU_SRAM0_RD_DATA_BEAT_RECEIVED",
        "ETHOSU_PMU_SRAM_WR_DATA_BEAT_STALLED",
        "ETHOSU_PMU_NPU_ACTIVE",
        "ETHOSU_PMU_SRAM_RD_STALL_LIMIT",
    ]
    counters = resolve_counters({"ethos_npu": wanted})
    assert [c.name for c in counters] == wanted
    assert all(c.group == "ethos_npu" for c in counters)
    passes = plan_passes(counters)
    assert [len(p.counters) for p in passes] == [4, 1]
    assert [c.name for p in passes for c in p.counters] == wanted


def test_ethos_npu_extended_events_are_listed_for_the_group():
    listed = [c.name for c in list_counters("ethos_npu")]
    assert listed[:9] == GROUPS["ethos_npu"]
    assert listed[9:] == EXTENDED_COUNTERS["ethos_npu"]


def test_ethos_npu_extended_events_duplicate_selection_is_deduplicated():
    name = "ETHOSU_PMU_SRAM0_WR_TRANS_ACCEPTED"
    assert [c.name for c in resolve_counters({"ethos_npu": [name, name]})] == [name]


def test_ethos_npu_extended_events_gated_by_domain():
    with pytest.raises(ValueError):
        validate_group_selection(
            {"ethos_npu": ["ETHOSU_PMU_SRAM1_RD_DATA_BEAT_RECEIVED"]},
            supported_groups=("cpu", "memory", "mve"),
        )


@pytest.mark.parametrize(
    "bad",
    [
        "ETHOSU_PMU_SRAM2_RD_DATA_BEAT_RECEIVED",
        "ETHOSU_PMU_SRAM0_RD_DATA_BEAT",
        "",
        "ethosu_pmu_wd_stalled",
    ],
)
def test_unknown_ethos_npu_event_name_is_rejected(bad):
    with pytest.raises(ValueError, match="Unknown PMU counter"):
        resolve_counters({"ethos_npu": [bad]})
