"""Tests for the platform model."""

import pytest

from helia_profiler.errors import ConfigError
from helia_profiler.platform import (
    BoardDef,
    CoreArch,
    MemoryLayout,
    PmuTier,
    SocDef,
    SocFamily,
    SocOrigin,
    build_platform_registry,
    get_board,
    get_soc,
    get_soc_for_board,
    list_boards,
    list_socs,
)
from helia_profiler.platform.custom import build_custom_platform_registry


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


def test_clean_window_needs_probe_attach_tracks_both_conjuncts():
    """``SocCapabilities.clean_window_needs_probe_attach`` is the single source
    for "this window's clock stops when the probe goes away" (#121).

    Written as the implication rather than an enumeration of today's families,
    so it keeps meaning if a new SoC lands in either half. Both conjuncts must
    stay load-bearing: a STIMER-timed window is indifferent to the probe even
    on a Cortex-M4F part, and a DWT family that held the debug domain up
    unaided would need no wait either.
    """
    checked = 0
    for soc in list_socs():
        caps = soc.capabilities
        expected = (
            caps.clock.clean_window_timer == "dwt"
            and caps.transport.requires_attached_probe_for_cycles
        )
        assert caps.clean_window_needs_probe_attach is expected, soc.name
        if expected:
            checked += 1
        if caps.clock.clean_window_timer == "stimer":
            assert not caps.clean_window_needs_probe_attach, soc.name
    assert checked, (
        "no registered SoC times its clean window with a probe-dependent "
        "counter — this test lost its subject"
    )


def test_cortex_m4f_profile_windows_need_the_probe_attached():
    """The Cortex-M4F families are the concrete subject of #121.

    Apollo3/3P and Apollo4/4P/4L keep DWT for the profile binary precisely
    because a debugger holds the debug domain up for them; Apollo5 does not
    use DWT there at all. Pinned per family so a capability refactor that
    quietly flips a family shows up here and not on a bench.
    """
    for soc in list_socs():
        needs = soc.capabilities.clean_window_needs_probe_attach
        if soc.family in (SocFamily.AP3, SocFamily.AP4):
            assert needs, soc.name
        else:
            assert not needs, soc.name


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


# ---------------------------------------------------------------------------
# target.custom_socs: app-image flash load address (issue #149)
# ---------------------------------------------------------------------------


def _scratch_soc_spec(**overrides):
    """A custom SoC declared from scratch -- no ``based_on`` to inherit from.

    This is the shape the issue's failure scenario has: an OEM part the user
    describes entirely themselves, whose only link to anything hpx knows is
    the ``family`` tag.  Every key here is one ``_build_custom_socs`` requires.
    """
    spec = {
        "family": "ap4",
        "core": "cortex-m4",
        "pmu_tier": "dwt",
        "has_mve": False,
        "c_define": "AM_PART_OEM4",
        "cmsis_header": "oem4.h",
        "memory": {"mram_kb": 2000, "sram_kb": 1024, "dtcm_kb": 384},
        "clocks": [
            {
                "name": "cpu",
                "speeds": [{"name": "lp", "mhz": 96, "perf_tier": "NSX_PERF_LOW"}],
                "default": "lp",
            }
        ],
        "rtt_scan_ranges": [[0x10000000, 0x100000]],
    }
    spec.update(overrides)
    return spec


def _custom_soc(name, spec):
    return build_custom_platform_registry({"custom_socs": {name: spec}}).socs[name]


def test_a_declared_app_flash_load_address_beats_both_lookup_tiers(monkeypatch):
    """What the user wrote about their own silicon outranks every table here.

    Both tiers are stacked against it: the SoC is named after a built-in with
    a per-SoC override patched in, and declares the family whose baseline
    would otherwise answer.  Neither may win -- the user is describing a part
    hpx has never seen, and is the only source of truth for its bootloader
    reservation.
    """
    from helia_profiler.platform import capabilities

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo4p", 0x22000000)
    soc = _custom_soc("apollo4p", _scratch_soc_spec(app_flash_load_addr=0x00040000))

    assert soc.family is SocFamily.AP4  # the family baseline would say 0x00018000
    assert soc.capabilities.memory.app_flash_load_addr == 0x00040000


def test_a_declared_address_accepts_a_quoted_hex_literal():
    """``"0x40000"`` is how a hex address survives a quoting habit, not a bug.

    Unquoted YAML resolves ``0x00040000`` to an int already; the quoted form
    must not become a ValueError traceback out of ``int()``.
    """
    soc = _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr="0x00040000"))

    assert soc.capabilities.memory.app_flash_load_addr == 0x00040000


def test_a_declared_address_of_zero_is_honoured():
    """0 is an address, not "unstated".

    ``None`` is the only value meaning "nobody knows".  Written as a
    truthiness test, a declared 0 would fall through to the family baseline --
    the silent inheritance this field exists to end.
    """
    soc = _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr=0))

    assert soc.capabilities.memory.app_flash_load_addr == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "boolean"),
        ("0x1G000", "not an integer address"),
        (-4096, "negative"),
        ([0x40000], "not an integer address"),
    ],
)
def test_an_unusable_declared_address_is_rejected_with_a_typed_error(value, expected):
    """Bad addresses fail as ConfigError, never as a raw ValueError/TypeError.

    ``True`` is called out separately because ``bool`` is an ``int`` subclass:
    without an explicit check, ``app_flash_load_addr: true`` would quietly
    program the image at address 0x1.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr=value))

    assert expected in str(exc_info.value)
    assert "app_flash_load_addr" in str(exc_info.value)


def test_a_custom_soc_inherits_the_address_of_the_part_it_is_based_on():
    """``based_on`` is a statement about a specific, characterised part.

    Unlike a family tag, it names one SoC whose address was checked against
    NSX's own facts file, so carrying that address across is honouring what
    the user said rather than guessing from a core tier.
    """
    soc = _custom_soc("apollo510_custom", {"based_on": "apollo510"})

    assert soc.capabilities.memory.app_flash_load_addr == 0x00410000


def test_based_on_inheritance_carries_a_per_soc_override_not_just_the_family(monkeypatch):
    """Inheritance must take the base's RESOLVED address, not its raw field.

    Built-in parts keep their addresses in the capability tables, so a
    ``SocDef``-field copy would inherit ``None`` and silently fall back.  The
    case that matters is a part with a per-SoC override -- atomiq110 in
    production -- where the difference is 0x22000000 versus its family's
    0x00410000.
    """
    from helia_profiler.platform import capabilities

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo330P", 0x22000000)
    soc = _custom_soc("atomiq_like", {"based_on": "apollo330P"})

    assert soc.family is SocFamily.AP5  # its family would say 0x00410000
    assert soc.capabilities.memory.app_flash_load_addr == 0x22000000


def test_a_declared_address_beats_the_based_on_part():
    """The whole point of the field: a novel part derived from a known one.

    ``based_on: apollo510`` plus a different bootloader reservation is exactly
    the bring-up path ``docs/guide/boards.md`` documents.
    """
    soc = _custom_soc(
        "apollo510_custom",
        {"based_on": "apollo510", "app_flash_load_addr": 0x22000000},
    )

    assert soc.capabilities.memory.app_flash_load_addr == 0x22000000


def test_based_on_inheritance_chains_through_another_custom_soc():
    """A custom SoC may be based on an earlier custom SoC; the address follows.

    Resolving the chain one link at a time (rather than only against built-ins)
    is what keeps a two-step bring-up -- family part, then board-specific
    variant -- from losing the address at the second step.
    """
    registry = build_custom_platform_registry(
        {
            "custom_socs": {
                "oem4": _scratch_soc_spec(app_flash_load_addr=0x00040000),
                "oem4_rev_b": {"based_on": "oem4", "jlink_device": "OEM4-REVB"},
            }
        }
    )

    assert registry.socs["oem4_rev_b"].capabilities.memory.app_flash_load_addr == 0x00040000


def test_a_custom_soc_with_no_address_and_no_based_on_refuses_to_guess():
    """THE decision this change makes: unstated and underived means unknown.

    This is the issue's failure scenario -- an OEM part declared ``family:
    ap4`` whose secure bootloader reserves more than stock Apollo4's 0x18000.
    Handing it 0x18000 would be a *plausible* wrong answer: mapped on that
    family, likely accepted by the silicon, and landing the image at the wrong
    offset.  ``None`` instead reaches the J-Link fallback's "refuse to guess"
    guard, which until now could not fire for a custom SoC at all because
    ``family`` is enum-validated and every member is mapped.
    """
    soc = _custom_soc("oem4", _scratch_soc_spec())

    assert soc.family is SocFamily.AP4  # the family baseline would say 0x00018000
    assert soc.capabilities.memory.app_flash_load_addr is None


def test_overriding_memory_does_not_drop_an_inherited_address():
    """The rejected alternative, pinned so it cannot creep back in.

    Keying "refuse to guess" on the presence of a ``memory:`` override was
    considered and rejected: ``MemoryLayout`` carries sizes in KB and no
    addresses, so resizing a region says nothing about the bootloader
    reservation.  Here a board simply populates a larger PSRAM part -- a
    working inherited address must survive that.
    """
    soc = _custom_soc(
        "apollo510_custom",
        {"based_on": "apollo510", "memory": {"psram_kb": 131072}},
    )

    assert soc.memory.psram_kb == 131072
    assert soc.memory.mram_kb == get_soc("apollo510").memory.mram_kb  # merged, not replaced
    assert soc.capabilities.memory.app_flash_load_addr == 0x00410000


def test_a_board_psram_override_does_not_cost_a_builtin_soc_its_address():
    """Provenance must survive ``get_soc_for_board``'s ``replace`` copy.

    Seven built-in boards override ``psram_kb``, and for those the SoC handed
    to the pipeline is a copy, not the registered object.  Gating the address
    tables on object identity silently reclassified those copies as unknown
    parts -- so the per-SoC override tier would vanish on exactly the boards
    that populate PSRAM, which is why this is a typed field.
    """
    soc = get_soc_for_board("apollo3p_evb")

    assert soc is not get_soc("apollo3p")  # a replace() copy
    assert soc.memory.psram_kb == 8192
    assert soc.origin is SocOrigin.BUILTIN
    assert soc.capabilities.memory.app_flash_load_addr == 0x0000C000


def test_every_builtin_soc_is_stamped_builtin_and_states_no_address_itself():
    """Built-ins keep their addresses in the capability tables, not the field.

    The field is the escape hatch for parts those tables cannot speak for.  A
    built-in quietly acquiring one would bypass the pinned table values, so
    adding an address to a registered ``SocDef`` must be a deliberate act that
    updates this test too.
    """
    for soc in list_socs():
        assert soc.origin is SocOrigin.BUILTIN, soc.name
        assert soc.app_flash_load_addr is None, soc.name


def test_a_custom_soc_is_stamped_custom_even_when_named_after_a_builtin():
    """The name is user-chosen; it must not confer built-in provenance."""
    soc = _custom_soc("apollo510", {"based_on": "apollo4p", "family": "ap4"})

    assert soc.origin is SocOrigin.CUSTOM


# ---------------------------------------------------------------------------
# target.custom_socs / custom_boards: unknown keys (issue #149)
# ---------------------------------------------------------------------------


def test_an_unknown_key_in_a_custom_soc_is_rejected():
    """Silence is the worst answer to a key the user reached for deliberately.

    Before this, every unrecognised key was discarded without a word -- so a
    user who correctly diagnosed a wrong flash address and wrote
    ``app_flash_load_addr:`` on a version that did not support it saw their
    config accepted and their part flashed at the old address anyway.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(flash_load_address=0x00040000))

    assert "target.custom_socs.oem4" in str(exc_info.value)
    assert "flash_load_address" in str(exc_info.value)
    assert "app_flash_load_addr" in (exc_info.value.hint or "")


def test_an_unknown_key_error_names_every_offender_and_lists_what_is_supported():
    """One pass, not one error per round-trip, and never a bare "invalid key"."""
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(jlink="OEM4", nonsense=1))

    message = str(exc_info.value)
    assert "'jlink'" in message and "'nonsense'" in message
    hint = exc_info.value.hint or ""
    assert "jlink_device" in hint  # close-match suggestion
    assert "rtt_scan_ranges" in hint  # full supported-key listing


def test_an_unknown_key_inside_a_custom_soc_memory_block_is_rejected():
    """A misspelt size silently left the base part's value in place.

    These sizes are the arena/weights capacity checks, so the consequence of
    dropping one is a placement accepted here and overflowing at link time --
    the apollo330P TCM lesson recorded in ``platform/soc.py``.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("apollo510_custom", {"based_on": "apollo510", "memory": {"tcm_kb": 240}})

    assert "target.custom_socs.apollo510_custom.memory" in str(exc_info.value)
    assert "dtcm_kb" in (exc_info.value.hint or "")


def test_the_custom_memory_keys_track_the_memory_layout_fields_exactly():
    """The accepted keys ARE the layout's fields -- drift either way is a bug.

    A missing member makes a legitimate size unwritable *and* now rejects it
    outright; a surplus member is accepted here and dropped by ``MemoryLayout``.
    """
    from dataclasses import fields

    from helia_profiler.platform.custom import CustomMemoryField

    assert {member.value for member in CustomMemoryField} == {
        field.name for field in fields(MemoryLayout)
    }


def test_the_custom_soc_keys_are_a_pinned_subset_of_the_soc_definition():
    """Adding a ``SocDef`` field must be a decision, not an omission.

    ``custom_socs`` deliberately exposes only part of ``SocDef``: ``name``
    comes from the mapping key, ``origin`` is not the user's to claim, and the
    rest below are HAL/silicon facts no config has needed to override yet.
    Anything else new lands in the unknown-key rejection instead, so this
    fails until someone chooses which side it belongs on.
    """
    from dataclasses import fields

    from helia_profiler.platform.custom import CustomSocField

    not_exposed = {
        "name",  # the mapping key
        "origin",  # stamped by the registry, never declared
        "swo_trace_clock_mhz",
        "has_usb",
        "ssram_full_power_enum",
        "has_radio_subsystem",
    }
    declared = {member.value for member in CustomSocField} - {"based_on"}

    assert declared == {field.name for field in fields(SocDef)} - not_exposed


def test_an_unknown_key_in_a_custom_board_is_rejected():
    """Same hole, same fix: custom boards discarded unknown keys too.

    A misspelt ``default_sync_gpio_pin`` leaves the capture wired to the wrong
    pin with nothing said about it.
    """
    with pytest.raises(ConfigError) as exc_info:
        build_custom_platform_registry(
            {
                "custom_boards": {
                    "apollo510_lab": {
                        "based_on": "apollo510_evb",
                        "default_sync_pin": 27,
                    }
                }
            }
        )

    assert "target.custom_boards.apollo510_lab" in str(exc_info.value)
    assert "default_sync_gpio_pin" in (exc_info.value.hint or "")
