"""Tests for the PMU counter registry module."""

from __future__ import annotations

import pytest

from helia_profiler.counters import (
    GROUPS,
    DEFAULT_COUNTERS,
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
    assert len(list_counters()) == 70


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
