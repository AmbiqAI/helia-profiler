"""Unit tests for the measured-memory console rendering (#133 Phase 2).

The renderer previously shipped with zero coverage (#177 review M2),
which hid a rich-markup injection through ELF section names (M3).
"""

from __future__ import annotations

from rich.console import Console

from helia_profiler.console import HpxConsole
from helia_profiler.console.results import (
    measured_memory_is_renderable,
    render_memory_regions,
)
from helia_profiler.placement import MemoryRegion
from helia_profiler.results import (
    MeasuredMemoryRegions,
    MeasuredRegion,
    UnattributedSection,
)


def _render(measured: MeasuredMemoryRegions) -> str:
    hpx_console = HpxConsole(verbosity=0)
    recorder = Console(record=True, highlight=False, width=200)
    hpx_console._console = recorder
    render_memory_regions(hpx_console, measured)
    return recorder.export_text()


def _region(**overrides) -> MeasuredRegion:
    base = dict(
        region=MemoryRegion.DTCM,
        window_start=0x20000000,
        window_length=524_288,
        app_start=0x20000000,
        app_length=507_904,
        used=16_664,
        reserved=491_240,
        load_image=0,
    )
    base.update(overrides)
    return MeasuredRegion(**base)


def test_measured_table_renders_used_free_and_reserved():
    text = _render(
        MeasuredMemoryRegions(
            link_family="gnu",
            linker_profile="default",
            regions=(_region(),),
        )
    )
    assert "Memory (measured)" in text
    assert "gnu link" in text
    assert "DTCM" in text
    assert "491,240" in text or "491.2" in text or "479.7 KB" in text.replace("\n", "")


def test_hostile_section_names_render_escaped_and_do_not_crash():
    """#177 review M3: ELF section names are attacker-ish input. A name
    carrying rich markup must neither restyle the line nor raise
    MarkupError out of a successful run."""
    text = _render(
        MeasuredMemoryRegions(
            link_family="gnu",
            linker_profile="default",
            regions=(_region(),),
            unattributed=(
                UnattributedSection(
                    name="[red]evil[/red] .oops", address=0x30000000, size=64
                ),
                UnattributedSection(name=".weird[/bold]", address=0x0, size=1),
            ),
        )
    )
    # The literal names survive, tags un-swallowed:
    assert "[red]evil[/red] .oops" in text
    assert ".weird[/bold]" in text
    assert "unattributed" in text


def test_unattributed_load_bytes_line_renders():
    text = _render(
        MeasuredMemoryRegions(
            link_family="armlink",
            linker_profile="default",
            regions=(_region(),),
            unattributed_load_bytes=8192,
        )
    )
    assert "unattributed load image" in text


def test_all_zero_measured_block_falls_back_to_the_plan_table():
    """#177 review n7: a measured block whose every region is idle must
    not suppress the plan table with a header-only shell. The call site
    uses measured_memory_is_renderable — the REAL predicate, not a
    mirror (follow-up NIT-3)."""
    idle = MeasuredMemoryRegions(
        link_family="gnu",
        linker_profile="default",
        regions=(_region(used=0, reserved=0, load_image=0),),
    )
    assert not measured_memory_is_renderable(idle)
    text = _render(idle)
    assert "DTCM" not in text


def test_police_lines_render_even_when_every_region_is_zero():
    """Follow-up MINOR-1: everything landing OUTSIDE the characterized
    windows is the anomaly the police lines exist for — an all-zero
    region set with unattributed content must still render, not fall back
    to the plan table."""
    anomalous = MeasuredMemoryRegions(
        link_family="gnu",
        linker_profile="default",
        regions=(_region(used=0, reserved=0, load_image=0),),
        unattributed=(
            UnattributedSection(name=".rogue", address=0x70000000, size=4096),
        ),
    )
    assert measured_memory_is_renderable(anomalous)
    text = _render(anomalous)
    assert ".rogue" in text and "unattributed" in text

    load_only = MeasuredMemoryRegions(
        link_family="gnu",
        linker_profile="default",
        regions=(_region(used=0, reserved=0, load_image=0),),
        unattributed_load_bytes=512,
    )
    assert measured_memory_is_renderable(load_only)


def test_reserved_only_region_renders_a_row():
    """Follow-up NIT-2: an armlink DTCM holding only the fixed heap+stack
    reservation is real information — the row must not vanish."""
    reserved_only = MeasuredMemoryRegions(
        link_family="armlink",
        linker_profile="default",
        regions=(_region(used=0, reserved=20_480, load_image=0),),
    )
    assert measured_memory_is_renderable(reserved_only)
    text = _render(reserved_only)
    assert "DTCM" in text
