"""Actual plan consumers respect contiguous space and fixed reservation ownership."""

from dataclasses import replace

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.errors import ImageTooLargeError, PlatformError
from helia_profiler.placement import admit_memory_plan
from helia_profiler.platform.linker.budget import bind_application_ranges
from helia_profiler.platform.linker.data import characterized_input
from helia_profiler.platform.linker.model import ApplicationRegion
from helia_profiler.platform.memory_map import LinkFamily
from helia_profiler.platform.placement import MemoryRegion
from helia_profiler.platform.soc import MemoryRange
from helia_profiler.results import ConsumerKind, MemoryConsumer, MemoryPlan, MemoryRegionUsage


def _plan(size, *, stack=0, capacity=524288):
    consumers = [MemoryConsumer("tensor_arena", size)]
    if stack:
        consumers.append(MemoryConsumer("boot_stack", stack, ConsumerKind.STACK))
    return MemoryPlan(
        EngineType.HELIA_RT,
        (MemoryRegionUsage(MemoryRegion.DTCM, capacity, size + stack, tuple(consumers)),),
    )


def test_allocation_cannot_bridge_two_smaller_allowed_intervals():
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    start = 0x20000000
    layout = bind_application_ranges(
        layout,
        (
            ApplicationRegion(
                "dtcm",
                MemoryRegion.DTCM,
                (
                    MemoryRange(start, 32),
                    MemoryRange(start + 64, 32),
                ),
            ),
        ),
    )
    with pytest.raises(PlatformError, match="contiguous application budget of 32 B"):
        admit_memory_plan(_plan(48), layout)
    assert admit_memory_plan(_plan(32), layout).regions[0].capacity == 32


def test_known_empty_is_a_refusal_while_unknown_keeps_declared_constraints():
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    empty = bind_application_ranges(layout, ())
    with pytest.raises(PlatformError, match="budget of 0 B"):
        admit_memory_plan(_plan(1), empty)
    unknown = admit_memory_plan(_plan(16, capacity=32), layout)
    assert unknown.regions[0].capacity == 32
    with pytest.raises(PlatformError, match="declared capacity"):
        admit_memory_plan(_plan(33, capacity=32), layout)


def test_armlink_fixed_stack_is_not_charged_twice_against_app_extent():
    layout = characterized_input("apollo510", LinkFamily.ARMLINK)
    assert layout is not None
    region = next(r for r in layout.regions if r.region is MemoryRegion.DTCM)
    layout = replace(
        layout, regions=tuple(replace(r, allowed=(r.app_window,)) for r in layout.regions)
    )
    original = _plan(region.app_window.length, stack=16384)
    result = admit_memory_plan(original, layout)
    assert result.regions[0].used == region.app_window.length
    assert result.regions[0].capacity == region.app_window.length
    assert [c.name for c in result.regions[0].consumers] == ["tensor_arena"]
    assert len(original.regions[0].consumers) == 2
    assert admit_memory_plan(result, layout) == result


def test_gnu_floating_stack_still_consumes_app_space():
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    layout = replace(
        layout, regions=tuple(replace(r, allowed=(r.app_window,)) for r in layout.regions)
    )
    region = next(r for r in layout.regions if r.region is MemoryRegion.DTCM)
    with pytest.raises(PlatformError, match="application budget"):
        admit_memory_plan(_plan(region.app_window.length, stack=16384), layout)
    result = admit_memory_plan(_plan(region.app_window.length - 16384, stack=16384), layout)
    assert len(result.regions[0].consumers) == 2


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("ram_overflow", [False, True])
def test_mram_skip_requires_all_other_regions_to_fit(reverse, ram_overflow):
    layout = characterized_input("apollo510", LinkFamily.GNU)
    assert layout is not None
    layout = replace(
        layout, regions=tuple(replace(r, allowed=(r.app_window,)) for r in layout.regions)
    )
    regions = []
    for region in (MemoryRegion.MRAM, MemoryRegion.DTCM):
        entry = next(r for r in layout.regions if r.region is region)
        assert entry.capacity is not None
        used = entry.capacity + (1 if region is MemoryRegion.MRAM or ram_overflow else 0)
        regions.append(MemoryRegionUsage(region, 1 << 30, used))
    if reverse:
        regions.reverse()
    plan = MemoryPlan(EngineType.HELIA_RT, tuple(regions))
    error = PlatformError if ram_overflow else ImageTooLargeError
    with pytest.raises(error, match="DTCM" if ram_overflow else "MRAM"):
        admit_memory_plan(plan, layout)


@pytest.mark.parametrize("reverse", [False, True])
def test_mram_overflow_does_not_hide_fixed_stack_mismatch(reverse):
    layout = characterized_input("apollo510", LinkFamily.ARMLINK)
    assert layout is not None
    layout = replace(
        layout, regions=tuple(replace(r, allowed=(r.app_window,)) for r in layout.regions)
    )
    mram = next(r for r in layout.regions if r.region is MemoryRegion.MRAM)
    assert mram.capacity is not None
    regions = (
        MemoryRegionUsage(MemoryRegion.MRAM, 1 << 30, mram.capacity + 1),
        _plan(16, stack=1).regions[0],
    )
    plan = MemoryPlan(EngineType.HELIA_RT, regions[::-1] if reverse else regions)
    with pytest.raises(PlatformError, match="boot stack differs"):
        admit_memory_plan(plan, layout)
