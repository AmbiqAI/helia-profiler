"""Tests for the platform model."""

import pytest

from helia_profiler.platform import (
    BoardDef,
    CoreArch,
    PmuTier,
    SocDef,
    SocFamily,
    build_platform_registry,
    get_board,
    get_soc,
    get_soc_for_board,
    list_boards,
    list_socs,
)


def test_apollo510_evb_resolves_to_cortex_m55():
    soc = get_soc_for_board("apollo510_evb")
    assert soc.core is CoreArch.CORTEX_M55
    assert soc.family is SocFamily.AP5
    assert soc.has_full_pmu
    assert soc.has_mve
    assert soc.profiling_backends == ("dwt", "armv8m-pmu")
    assert soc.profiling_domains == ("cpu", "memory", "mve")


def test_apollo510_evb_default_sync_gpio_pin_is_29():
    board = get_board("apollo510_evb")
    assert board.default_sync_gpio_pin == 29


def test_apollo510b_evb_default_sync_gpio_pin_is_29():
    board = get_board("apollo510b_evb")
    assert board.default_sync_gpio_pin == 29


def test_apollo510b_uses_expected_jlink_device():
    soc = get_soc("apollo510b")
    assert soc.jlink_device == "AP510BFA-CBR"


def test_apollo3p_evb_resolves_to_cortex_m4():
    soc = get_soc_for_board("apollo3p_evb")
    assert soc.core is CoreArch.CORTEX_M4
    assert soc.family is SocFamily.AP3
    assert soc.pmu_tier is PmuTier.DWT_ONLY
    assert not soc.has_mve
    assert soc.profiling_backends == ("dwt",)
    assert soc.profiling_domains == ("cpu",)
    assert soc.memory.psram_kb == 8192


def test_apollo4p_evb_exposes_board_psram_capacity():
    soc = get_soc_for_board("apollo4p_evb")
    assert soc.family is SocFamily.AP4
    assert soc.memory.psram_kb == 32768


def test_apollo510_family_uses_shared_cmsis_header():
    assert get_soc("apollo510").cmsis_header == "apollo510.h"
    assert get_soc("apollo510b").cmsis_header == "apollo510.h"
    assert get_soc("apollo5b").cmsis_header == "apollo510.h"


def test_apollo510_family_uses_ap5_rtt_scan_window():
    # On the cache-coherent M55 parts RTT is pinned to non-cached TCM (.bss),
    # not .sram_bss/SHARED_SRAM. DTCM is based at 0x20000000 (512 KB), so the
    # fallback scan window covers that region (the known-address nm/map path is
    # the primary route and skips scanning entirely).
    assert get_soc("apollo510").rtt_scan_ranges == ((0x20000000, 0x80000),)
    assert get_soc("apollo510b").rtt_scan_ranges == ((0x20000000, 0x80000),)
    assert get_soc("apollo5b").rtt_scan_ranges == ((0x20000000, 0x80000),)
    # apollo330P's TCM is only 240 KB, so its window is tighter (see
    # test_apollo330_hardware_facts_not_copied_from_apollo510).
    assert get_soc("apollo330P").rtt_scan_ranges == ((0x20000000, 0x3C000),)


def test_cortex_m4_socs_use_ap3_ap4_rtt_scan_window():
    for soc in list_socs():
        if soc.family in (SocFamily.AP3, SocFamily.AP4):
            assert soc.rtt_scan_ranges == ((0x10000000, 0x100000),)


def test_every_soc_declares_cmsis_header_and_rtt_scan_ranges():
    for soc in list_socs():
        assert soc.cmsis_header.endswith(".h"), f"{soc.name} missing cmsis_header"
        assert soc.rtt_scan_ranges, f"{soc.name} missing rtt_scan_ranges"
        for base, length in soc.rtt_scan_ranges:
            assert base > 0 and length > 0, f"{soc.name} has invalid rtt scan window"



def test_ap5_socs_expose_expected_psram_capacity():
    # apollo510b_evb populates a 64 MB APS512XXN part (hardware-proven via
    # XIP address-aliasing, 2026-07-05); other AP5 boards assume 32 MB until
    # validated on hardware.
    expected_kb = {"apollo510": 65536, "apollo510b": 65536}
    for soc in list_socs():
        if soc.family is SocFamily.AP5:
            assert soc.memory.psram_kb == expected_kb.get(soc.name, 32768)


def test_apollo330_is_ap5_family():
    """AP330 is Cortex-M55 and belongs to AP5 family."""
    soc = get_soc_for_board("apollo330mP_evb")
    assert soc.family is SocFamily.AP5
    assert soc.core is CoreArch.CORTEX_M55
    assert soc.has_full_pmu
    assert soc.has_mve


def test_apollo330_hardware_facts_not_copied_from_apollo510():
    """apollo330P metadata must match the real part, not apollo510.

    Every value here was verified against the synced NSX/AmbiqSuite
    sources for apollo330P (linker script, HAL headers, NSX SoC facts)
    on real Apollo330mP Rev1 EVB hardware -- guarding against the
    copy-paste-from-AP510 bug class found during bring-up.
    """
    soc = get_soc_for_board("apollo330mP_evb")
    # Fixed 48 MHz XTAL_HS trace clock (like AP3), NOT core-clocked.
    assert soc.swo_trace_clock_mhz == 48
    # Real memories: 240 KB unified TCM, 1792 KB SSRAM, 1984 KB usable
    # MRAM, no separate ITCM region.
    assert soc.memory.dtcm_kb == 240
    assert soc.memory.itcm_kb == 0
    assert soc.memory.sram_kb == 1792
    assert soc.memory.mram_kb == 1984
    # RTT scan window bounded to the real 240 KB TCM.
    assert soc.rtt_scan_ranges == ((0x20000000, 0x3C000),)
    # HAL defines SRAM_1P75M only (no SRAM_3M on this part).
    assert soc.ssram_full_power_enum == "AM_HAL_PWRCTRL_SRAM_1P75M"
    assert soc.pmu_max_ops == 512
    assert soc.jlink_device == "Apollo330P_510L"


def test_unknown_board_raises():
    with pytest.raises(ValueError, match="Unknown board"):
        get_board("nonexistent_evb")


def test_unknown_soc_raises():
    with pytest.raises(ValueError, match="Unknown SoC"):
        get_soc("nonexistent_soc")


def test_list_boards_returns_all():
    boards = list_boards()
    names = {b.name for b in boards}
    assert "apollo510_evb" in names
    assert "apollo3p_evb" in names
    assert "apollo4p_evb" in names
    assert "apollo330mP_evb" in names


def test_list_socs_returns_all():
    socs = list_socs()
    names = {s.name for s in socs}
    assert "apollo510" in names
    assert "apollo3p" in names
    assert "apollo330P" in names


def test_all_ap5_socs_have_full_pmu():
    for soc in list_socs():
        if soc.family is SocFamily.AP5:
            assert soc.has_full_pmu, f"{soc.name} is AP5 but missing full PMU"
            assert soc.has_mve, f"{soc.name} is AP5 but missing MVE"


def test_custom_board_registry_can_extend_builtin_board_metadata():
    registry = build_platform_registry(
        boards={
            "apollo510_lab": BoardDef(
                name="apollo510_lab",
                soc="apollo510",
                channel="dev",
                default_sync_gpio_pin=41,
                starter_profile_board="apollo510_evb",
            )
        }
    )

    board = get_board("apollo510_lab", registry=registry)
    soc = get_soc_for_board("apollo510_lab", registry=registry)

    assert board.default_sync_gpio_pin == 41
    assert board.profile_source_board == "apollo510_evb"
    assert soc.name == "apollo510"


def test_custom_soc_registry_can_override_jlink_and_rtt():
    base_soc = get_soc("apollo510")
    registry = build_platform_registry(
        socs={
            "apollo510_custom": SocDef(
                name="apollo510_custom",
                family=base_soc.family,
                core=base_soc.core,
                pmu_tier=base_soc.pmu_tier,
                has_mve=base_soc.has_mve,
                memory=base_soc.memory,
                clocks=base_soc.clocks,
                c_define=base_soc.c_define,
                cmsis_header=base_soc.cmsis_header,
                rtt_scan_ranges=((0x21000000, 0x100000),),
                jlink_device="AP510-CUSTOM",
                pmu_max_ops=base_soc.pmu_max_ops,
            )
        },
        boards={
            "apollo510_custom_board": BoardDef(
                name="apollo510_custom_board",
                soc="apollo510_custom",
                channel="dev",
                starter_profile_board="apollo510_evb",
            )
        },
    )

    soc = get_soc_for_board("apollo510_custom_board", registry=registry)

    assert soc.jlink_device == "AP510-CUSTOM"
    assert soc.rtt_scan_ranges == ((0x21000000, 0x100000),)
