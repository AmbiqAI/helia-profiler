"""Admission follows disjoint hardware ranges, independently of physical-bank size."""

from dataclasses import replace

import pytest

from helia_profiler.platform import build_platform_registry
from helia_profiler.platform.catalog import application_regions
from helia_profiler.platform.linker.budget import bind_application_ranges, intersect_intervals
from helia_profiler.platform.linker.data import characterized_input
from helia_profiler.platform.linker.model import ApplicationRegion
from helia_profiler.platform.memory_map import LinkFamily
from helia_profiler.platform.placement import MemoryRegion
from helia_profiler.platform.soc import MemoryRange


def test_gaps_cannot_be_counted_as_a_contiguous_arena():
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    start = 0x20000000
    facts = (
        ApplicationRegion(
            "dtcm",
            MemoryRegion.DTCM,
            (
                MemoryRange(start, 32),
                MemoryRange(start + 64, 32),
            ),
        ),
    )
    bound = bind_application_ranges(layout, facts)
    region = next(r for r in bound.regions if r.region is MemoryRegion.DTCM)
    assert region.capacity == 32
    assert sum(r.length for r in (region.allowed or ())) == 64
    assert layout.admission_source == "unavailable"


@pytest.mark.parametrize("facts,expected", [(None, None), ((), ()), ((MemoryRange(10, 10),), ())])
def test_unknown_and_known_empty_are_distinct(facts, expected):
    assert intersect_intervals(facts, MemoryRange(0, 10)) == expected


def test_touching_spans_are_contiguous_but_reserved_gaps_are_not():
    assert intersect_intervals((MemoryRange(0, 5), MemoryRange(5, 5)), MemoryRange(0, 20)) == (
        MemoryRange(0, 10),
    )
    assert intersect_intervals((MemoryRange(0, 4), MemoryRange(5, 5)), MemoryRange(2, 6)) == (
        MemoryRange(2, 2),
        MemoryRange(5, 3),
    )


@pytest.mark.parametrize("family", list(LinkFamily))
def test_current_catalog_and_sdk_agree_on_small_m55_reserved_nvm(family):
    registry = build_platform_registry()
    facts = application_regions(registry.selections["apollo330mP_evb"])
    layout = characterized_input("apollo330P", family)
    assert layout is not None and facts is not None
    bound = bind_application_ranges(layout, facts)
    region = next(r for r in bound.regions if r.region is MemoryRegion.MRAM)
    assert region.allowed == (MemoryRange(0x00410000, 1_589_248),)
    assert region.capacity == 1_589_248
    assert region.capacity < region.window.length


def test_unknown_boot_does_not_select_the_catalog_default_as_an_admission_fact():
    registry = build_platform_registry()
    selection = replace(registry.selections["apollo510_evb"], boot=None)
    facts = application_regions(selection)
    assert facts is not None and all(r.intervals is None for r in facts)


def test_sdk_descriptor_is_explicitly_distinct_from_catalog_admission():
    layout = characterized_input("apollo5b", LinkFamily.GNU)
    assert layout is not None
    result = bind_application_ranges(layout, None, sdk_descriptor=True)
    assert result.admission_source == "sdk-descriptor"
    assert all(r.allowed == (r.app_window,) for r in result.regions)
