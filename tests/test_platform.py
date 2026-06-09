"""Tests for the platform model."""

import pytest

from helia_profiler.platform import (
    CoreArch,
    NpuArch,
    PmuTier,
    SocFamily,
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


def test_apollo330_is_ap5_family():
    """AP330 is Cortex-M55 and belongs to AP5 family."""
    soc = get_soc_for_board("apollo330mP_evb")
    assert soc.family is SocFamily.AP5
    assert soc.core is CoreArch.CORTEX_M55
    assert soc.has_full_pmu
    assert soc.has_mve


def test_atomiq110_exposes_cpu_and_npu_profiling_surfaces():
    soc = get_soc_for_board("atomiq110_fpga_turbo")
    assert soc.npu is NpuArch.ETHOS_U85
    assert soc.has_npu
    assert soc.profiling_backends == ("dwt", "armv8m-pmu", "ethos-u85-pmu")
    assert soc.profiling_domains == ("cpu", "memory", "mve", "npu")
    assert soc.feature_flags == ("dwt", "armv8m-pmu", "ethos-u85-pmu", "mve")


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
