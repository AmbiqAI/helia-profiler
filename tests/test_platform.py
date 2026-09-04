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
    # apollo330P's gcc-linked TCM region is 240 KB (hardware aperture is
    # 256 KB — see memory_map.py), so its scan window is tighter (see
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
    # apollo510b_evb carries a 64 MB APS512XXN part (verified on hardware);
    # other AP5 boards assume 32 MB until validated.
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
    # Linked memories: 240 KB gcc MCU_TCM (the capacity-check ceiling;
    # the hardware DTCM aperture is 256 KB, see memory_map.py), 1792 KB
    # SSRAM, 1984 KB usable MRAM, no separate ITCM region.
    assert soc.memory.dtcm_kb == 240
    assert soc.memory.itcm_kb == 0
    assert soc.memory.sram_kb == 1792
    assert soc.memory.mram_kb == 1984
    # RTT scan window bounded to the 240 KB gcc-linked TCM region.
    assert soc.rtt_scan_ranges == ((0x20000000, 0x3C000),)
    # HAL defines SRAM_1P75M only (no SRAM_3M on this part).
    assert soc.ssram_full_power_enum == "AM_HAL_PWRCTRL_SRAM_1P75M"
    assert soc.pmu_max_ops == 512
    assert soc.jlink_device == "Apollo330P_510L"


def test_apollo510_lite_hardware_facts_match_apollo330P_not_apollo510():
    """apollo510L shares apollo330P's memory map and quirks, not apollo510's.

    The linker map, SSRAM power enum, trace clock and J-Link device name are
    pinned against the SDK sources so a copy from apollo510 cannot creep in.
    """
    soc = get_soc("apollo510L")
    assert get_soc_for_board("apollo510dL_evb").name == "apollo510L"
    # The EVB hides its PSRAM until nsx-psram lists the part (see board.py).
    assert get_soc_for_board("apollo510dL_evb").memory.psram_kb == 0
    assert soc.family is SocFamily.AP5
    assert soc.core is CoreArch.CORTEX_M55
    assert soc.has_full_pmu
    assert soc.has_mve
    assert soc.swo_trace_clock_mhz == 48
    assert soc.memory.dtcm_kb == 240
    assert soc.memory.itcm_kb == 0
    assert soc.memory.sram_kb == 1792
    assert soc.memory.mram_kb == 1984
    assert soc.memory.psram_kb == 32768
    assert soc.rtt_scan_ranges == ((0x20000000, 0x3C000),)
    assert soc.ssram_full_power_enum == "AM_HAL_PWRCTRL_SRAM_1P75M"
    assert soc.pmu_max_ops == 512
    assert soc.jlink_device == "AP510L"
    assert soc.cmsis_header == "apollo510L.h"
    assert soc.c_define == "AM_PART_APOLLO510L"


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
    assert "apollo510dL_evb" in names


def test_list_socs_returns_all():
    socs = list_socs()
    names = {s.name for s in socs}
    assert "apollo510" in names
    assert "apollo3p" in names
    assert "apollo330P" in names
    assert "apollo510L" in names


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
    # Stated rather than left implicit because it CHANGED with issue #149, and
    # nothing here noticed: this SocDef used to resolve 0x00410000 off its AP5
    # family tag and now resolves None.  The programmatic path has no
    # ``based_on`` to inherit through, so a caller building a SocDef by hand
    # must pass ``app_flash_load_addr=`` to get an address at all.
    assert soc.origin is SocOrigin.CUSTOM
    assert soc.capabilities.memory.app_flash_load_addr is None


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
    """The declared tier is consulted BEFORE the two tables, not after.

    Observing that order needs a ``SocDef`` for which both sides could answer,
    and no such part exists in normal use: built-ins declare no address of
    their own (pinned by
    ``test_every_builtin_soc_is_stamped_builtin_and_states_no_address_itself``)
    and custom ones never reach the tables.  So the fixture is built by hand --
    a ``replace`` copy of a built-in, which keeps ``SocOrigin.BUILTIN`` and
    therefore keeps both tables in play, carrying a declared address as well.
    Both tables are then stacked against it: a per-SoC override patched in
    under its name, and its own family's baseline behind that.

    Without this, the tier order is unobservable and moving the declared tier
    below the tables leaves the whole suite green.

    The two table reads below are *guard* asserts, not the subject: they show
    what tiers 2 and 3 would have answered, so that a future reader can see the
    declared tier beating something rather than beating nothing.  Tier 2's is
    monkeypatched and therefore inert.  Tier 3's reads the production table, so
    if ``_FAMILY_APP_FLASH_LOAD_ADDR[AP5]`` is ever revised this line fails
    first -- deliberately.  When that happens, update the literal to whatever
    the table now says; the assertion that matters is the last one.
    """
    from dataclasses import replace

    from helia_profiler.platform import capabilities

    monkeypatch.setitem(capabilities._SOC_APP_FLASH_LOAD_ADDR, "apollo510", 0x22000000)
    soc = replace(get_soc("apollo510"), app_flash_load_addr=0x00040000)

    assert soc.is_builtin  # both tables are reachable for it
    assert capabilities._SOC_APP_FLASH_LOAD_ADDR["apollo510"] == 0x22000000  # tier 2 would say
    assert capabilities._FAMILY_APP_FLASH_LOAD_ADDR[SocFamily.AP5] == 0x00410000  # tier 3 would
    assert soc.capabilities.memory.app_flash_load_addr == 0x00040000


def test_a_custom_socs_declared_address_survives_a_name_matched_override(monkeypatch):
    """What the user wrote about their own silicon outranks every table here.

    Sibling of the test above, from the other origin: this is the shape a real
    ``target.custom_socs`` entry has, so the tables are gated off it entirely
    and only the declared tier can answer.  What it pins is that naming the
    part after a built-in with an override does not disturb that -- the user is
    describing a part hpx has never seen, and is the only source of truth for
    its bootloader reservation.
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
        (0x220000000, "does not fit in 32 bits"),
        ("0x220000000", "does not fit in 32 bits"),
    ],
)
def test_an_unusable_declared_address_is_rejected_with_a_typed_error(value, expected):
    """Bad addresses fail as ConfigError, never as a raw ValueError/TypeError.

    ``True`` is called out separately because ``bool`` is an ``int`` subclass:
    without an explicit check, ``app_flash_load_addr: true`` would quietly
    program the image at address 0x1.

    ``0x220000000`` is the typo this field invites -- one hex digit too many on
    an otherwise plausible value.  Magnitude was unchecked while sign and type
    were, so it resolved verbatim and reached the J-Link recipe's
    ``LoadFile ..., 0x{addr:08X}`` as a 36-bit literal.  No 32-bit Cortex-M
    part has such an address, which makes it cheap to reject and expensive to
    accept.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr=value))

    assert expected in str(exc_info.value)
    assert "app_flash_load_addr" in str(exc_info.value)


def test_the_32_bit_bound_is_printed_in_the_repo_wide_address_format():
    """Addresses read as ``0x{value:08X}`` everywhere in hpx; this one drifted.

    ``transport/rtt.py``, ``stages/verify_placement.py`` and the J-Link
    ``LoadFile`` recipe itself all use the padded-uppercase form, and the
    recipe's is the one the user will be comparing this message against.
    Written as ``{value:#x}`` the bound printed ``0xffffffff`` -- same number,
    different shape, in the one message whose whole job is to be held up
    against an address the user wrote.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr=0x220000000))

    assert "0x220000000 does not fit in 32 bits (max 0xFFFFFFFF)" in str(exc_info.value)


def test_the_widest_32_bit_address_is_still_accepted():
    """The bound is inclusive -- it rejects impossible values, not extreme ones."""
    soc = _custom_soc("oem4", _scratch_soc_spec(app_flash_load_addr=0xFFFFFFFF))

    assert soc.capabilities.memory.app_flash_load_addr == 0xFFFFFFFF


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


def test_a_custom_soc_named_after_an_override_part_still_refuses_to_guess():
    """The same refusal, with the per-SoC override table stacked against it.

    Sibling of the test above, and the shape that actually observes the origin
    gate: ``atomiq110`` is a real ``_SOC_APP_FLASH_LOAD_ADDR`` entry, so a
    custom SoC that borrows the name has a live table value waiting under it.
    With no address and no ``based_on`` there is nothing else that could
    answer, so a non-``None`` result here means the name reached the table --
    the forgery df34b6e closed.

    Deliberately not monkeypatched: this uses the production table, so it keeps
    holding when PR #98 registers ``atomiq110`` as a built-in and the entry
    stops being reachable only through a user-chosen name.

    The cost of that choice is that the guard assert below pins a production
    value, and ``platform/capabilities.py`` says this one is expected to move:
    0x22000000 is the *nbl* origin correct for the atomiq110 FPGA realization,
    and real AT110 silicon "very likely" shifts it to the *sbl* 0x22010000.
    When that lands, this line fails first.  Update the literal -- do not
    delete the assert, and do not "correct" the table to the sbl address for
    the FPGA part.  The subject of the test is the two asserts after it.
    """
    from helia_profiler.platform import capabilities

    assert capabilities._SOC_APP_FLASH_LOAD_ADDR["atomiq110"] == 0x22000000
    soc = _custom_soc("atomiq110", _scratch_soc_spec())

    assert soc.origin is SocOrigin.CUSTOM
    assert soc.capabilities.memory.app_flash_load_addr is None


def test_an_explicit_null_address_is_a_refusal_not_an_omission():
    """``app_flash_load_addr: null`` must not be read as "key absent".

    The two are opposite statements and only a sentinel keeps them apart --
    ``spec.get(key) is None`` collapses them.  Writing ``null`` is the only way
    the config surface offers to say "do not guess for this part", and the
    user most likely to write it is one who wants a ``based_on``'s memory and
    clock facts but not its flash window.  Collapsed, that user gets the
    inherited address: the precise opposite of what they wrote.
    """
    soc = _custom_soc("apollo510_custom", {"based_on": "apollo510", "app_flash_load_addr": None})

    assert soc.memory == get_soc("apollo510").memory  # based_on still supplies everything else
    assert soc.capabilities.memory.app_flash_load_addr is None


def test_omitting_the_address_key_still_inherits_from_based_on():
    """The other half of the sentinel: absence must keep inheriting.

    Pinned alongside the explicit-``null`` case because a sentinel applied to
    both branches would silently turn every ``based_on`` entry into ``None``.
    """
    soc = _custom_soc("apollo510_custom", {"based_on": "apollo510"})

    assert soc.capabilities.memory.app_flash_load_addr == 0x00410000


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
    assert soc.is_builtin  # the copy is still describing the part it was registered as
    assert soc.capabilities.memory.app_flash_load_addr == 0x0000C000


def test_a_renamed_builtin_copy_does_not_inherit_the_name_it_was_given():
    """The other half of the ``is_builtin`` rule, and the tighter half.

    ``dataclasses.replace`` of a built-in is the obvious way to build a custom
    ``SocDef`` programmatically -- no guide points at it (``docs/guide/boards.md``
    shows a fresh ``SocDef(...)`` constructor, which defaults to ``CUSTOM``), but
    it is the shape a caller invents -- and ``origin`` survives it by design,
    which is exactly what the sibling test above requires.  The cost is that a
    renamed copy survives it too: an ``origin``-only gate lets
    ``replace(get_soc("apollo510"), name="atomiq110")`` read atomiq110's
    per-SoC override and flash an Apollo510 at 0x22000000, an address
    belonging to a different part.  That is the same address forgery, reopened
    on the programmatic path one dimension over from the config path.

    Not reachable from YAML -- ``_build_custom_socs`` constructs fresh
    ``SocDef``s, which default to ``CUSTOM`` (pinned by
    ``test_a_custom_soc_is_stamped_custom_even_when_named_after_a_builtin``) --
    so the copy has to be made by hand here, as a caller of the public API
    would make it.
    """
    from dataclasses import replace

    from helia_profiler.platform import capabilities

    forged = replace(get_soc("apollo510"), name="atomiq110")

    assert capabilities._SOC_APP_FLASH_LOAD_ADDR["atomiq110"] == 0x22000000  # waiting under it
    assert forged.origin is SocOrigin.BUILTIN  # carried through by `replace`, as intended
    assert forged.registered_name == "apollo510"  # ...but the stamp says what it really is
    assert not forged.is_builtin
    assert forged.capabilities.memory.app_flash_load_addr is None


def test_a_self_stamped_registered_name_does_not_confer_builtin_provenance():
    """The ``origin`` half of the ``is_builtin`` rule, which nothing else observes.

    The sibling above pins the *name* half.  Nothing pinned this one: deleting
    ``self.origin is SocOrigin.BUILTIN`` and returning only
    ``self.registered_name == self.name`` passes the entire suite, even though
    :attr:`SocDef.is_builtin` states that both halves are load-bearing.

    It passes because no path in the codebase builds the shape that tells the
    two halves apart.  ``registered_name`` is stamped by ``_register_soc`` and
    by nothing else, so every object carrying one also carries ``BUILTIN``;
    everything that skips the registry -- ``_build_custom_socs``' fresh
    ``SocDef``s, fixtures, callers of the public API -- leaves it ``None``,
    which cannot equal a non-empty ``name``.  So the name half already answers
    every case that exists, and ``origin`` sits unobservable behind it.

    That argues for pinning it, not for dropping it.  ``registered_name`` is an
    ordinary field: ``replace`` sets it to anything, and the day something
    stamps it outside ``_register_soc`` -- a serialisation round-trip, a
    registry that re-keys on merge, a fixture builder that copies a built-in
    "properly" -- the name half stops being sufficient and ``origin`` is the
    only thing still refusing.  Untested, that refusal could be deleted in a
    tidy-up with zero signal, reopening the df34b6e forgery against a live
    per-SoC override: ``atomiq110``'s address read for an object that is
    holding Apollo510's platform facts.

    Hence the shape below, which is deliberately not reachable today.  It is a
    mutation guard, and the mutation it guards is a one-line simplification
    that looks obviously safe.
    """
    from dataclasses import replace

    from helia_profiler.platform import capabilities

    claimed = replace(
        get_soc("apollo510"),
        name="atomiq110",
        registered_name="atomiq110",
        origin=SocOrigin.CUSTOM,
    )

    # Membership, not the value: the sibling above already pins 0x22000000, and
    # `capabilities` says that literal is expected to move.  All this assert
    # needs is that *some* table tier would answer if the gate opened.
    assert "atomiq110" in capabilities._SOC_APP_FLASH_LOAD_ADDR
    assert claimed.registered_name == claimed.name  # the name half is satisfied...
    assert not claimed.is_builtin  # ...so only `origin` is left to refuse
    assert claimed.capabilities.memory.app_flash_load_addr is None


def test_a_builtin_copy_that_keeps_its_name_still_resolves_through_the_tables():
    """The looser half: renaming is the disqualifier, copying is not.

    Stated on its own rather than only through ``get_soc_for_board`` so that
    tightening ``is_builtin`` into an identity check -- the bug ``origin``
    replaced -- fails here even if the seven ``psram_kb`` boards ever stop
    being the way a built-in copy is made.
    """
    from dataclasses import replace

    copy = replace(get_soc("apollo510"), has_usb=not get_soc("apollo510").has_usb)

    assert copy is not get_soc("apollo510")
    assert copy.is_builtin
    assert copy.capabilities.memory.app_flash_load_addr == 0x00410000


def test_every_builtin_soc_is_stamped_builtin_and_states_no_address_itself():
    """Built-ins keep their addresses in the capability tables, not the field.

    The field is the escape hatch for parts those tables cannot speak for.  A
    built-in quietly acquiring one would bypass the pinned table values, so
    adding an address to a registered ``SocDef`` must be a deliberate act that
    updates this test too.
    """
    for soc in list_socs():
        assert soc.origin is SocOrigin.BUILTIN, soc.name
        assert soc.registered_name == soc.name, soc.name
        assert soc.is_builtin, soc.name
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
    assert "Did you mean 'flash_load_address' -> 'app_flash_load_addr'?" in (
        exc_info.value.hint or ""
    )


def test_an_unknown_key_error_names_every_offender_and_lists_what_is_supported():
    """One pass, not one error per round-trip, and never a bare "invalid key".

    Neither offender here is close enough to earn a suggestion -- ``jlink``
    scores 0.588 against ``jlink_device``, just under difflib's 0.6 cutoff --
    which is the point: the supported-key listing is unconditional, so the user
    is never left with only the news that they were wrong.  (This test once
    read ``assert "jlink_device" in hint  # close-match suggestion``, which
    matched the listing.  The suggestion it named does not fire for this input
    at all; the dedicated test below uses keys that do.)
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_soc("oem4", _scratch_soc_spec(jlink="OEM4", nonsense=1))

    message = str(exc_info.value)
    assert "'jlink'" in message and "'nonsense'" in message
    hint = exc_info.value.hint or ""
    assert "Did you mean" not in hint
    assert "Supported keys:" in hint and "rtt_scan_ranges" in hint  # full listing


@pytest.mark.parametrize(
    ("target", "typo", "expected"),
    [
        (
            {"custom_socs": {"c": {"based_on": "apollo510", "flash_load_address": 1}}},
            "flash_load_address",
            "app_flash_load_addr",
        ),
        (
            {"custom_socs": {"c": {"based_on": "apollo510", "memory": {"tcm_kb": 240}}}},
            "tcm_kb",
            "itcm_kb",
        ),
        (
            {"custom_boards": {"lab": {"based_on": "apollo510_evb", "default_sync_pin": 27}}},
            "default_sync_pin",
            "default_sync_gpio_pin",
        ),
    ],
)
def test_a_near_miss_key_is_named_alongside_the_key_it_almost_is(target, typo, expected):
    """The close-match suggestion must be asserted on its own literal text.

    Every other assertion about it reads a key name out of the hint, and the
    ``Supported keys: ...`` listing in the same string contains every key name
    already -- so those assertions pass with the suggestion deleted outright.
    They were checking the listing, not the suggestion.  Matching "Did you
    mean" is the only phrasing that distinguishes the two.
    """
    with pytest.raises(ConfigError) as exc_info:
        build_custom_platform_registry(target)

    assert f"Did you mean {typo!r} -> {expected!r}?" in (exc_info.value.hint or "")


def test_unknown_keys_of_mixed_types_are_reported_not_crashed_on():
    """YAML keys are not all strings, and the offender list has to sort them.

    PyYAML resolves a bare ``on``/``off``/``yes``/``no`` to a ``bool`` and bare
    digits to an ``int``, so one unknown string key beside one unknown
    non-string key compares ``str`` against ``bool``.  Unkeyed, that sort
    raises ``TypeError`` from ``_prepare_merged_config`` -- which sits outside
    ``load_config``'s ``try`` -- so it escapes as a traceback and breaks that
    function's documented "never a raw exception" contract.
    """
    import yaml

    target = yaml.safe_load(
        "custom_socs:\n  apollo510_custom: {based_on: apollo510, on: 1, notes: hello}\n"
    )
    assert True in target["custom_socs"]["apollo510_custom"]  # PyYAML made `on:` a bool

    with pytest.raises(ConfigError) as exc_info:
        build_custom_platform_registry(target)

    assert "'notes'" in str(exc_info.value)


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
    comes from the mapping key, ``origin``/``registered_name`` are not the
    user's to claim, and the rest below are HAL/silicon facts no config has
    needed to override yet.  Anything else new lands in the unknown-key
    rejection instead, so this fails until someone chooses which side it
    belongs on.
    """
    from dataclasses import fields

    from helia_profiler.platform.custom import CustomSocField

    not_exposed = {
        "name",  # the mapping key
        "origin",  # stamped by the registry, never declared
        "registered_name",  # stamped by the registry alongside `origin`
        "swo_trace_clock_mhz",
        "has_usb",
        "ssram_full_power_enum",
        "has_radio_subsystem",
    }
    # Keys that are config surface only and back no SocDef field.
    not_a_soc_field = {
        "based_on",  # names the part to inherit from; never stored
        "description",  # free-form annotation, accepted for parity with boards
    }
    declared = {member.value for member in CustomSocField} - not_a_soc_field

    assert declared == {field.name for field in fields(SocDef)} - not_exposed


def test_the_custom_board_keys_cover_every_board_definition_field():
    """Every ``BoardDef`` field except the mapping key must be writable.

    ``custom_boards`` has no "deliberately not exposed" set the way
    ``custom_socs`` does -- every field on it is board wiring a lab rig can
    legitimately differ in.  A field missing here is not merely unwritable: it
    is silently dropped when inherited through ``based_on`` AND a hard
    ``ConfigError`` if written, which is how ``ble_reset_gpio_pin`` went
    unnoticed.
    """
    from dataclasses import fields

    from helia_profiler.platform.custom import CustomBoardField

    declared = {member.value for member in CustomBoardField} - {"based_on"}

    assert declared == {field.name for field in fields(BoardDef)} - {"name"}


def test_both_custom_blocks_accept_the_same_free_form_description():
    """Annotating a custom SoC must not be a hard error when boards allow it.

    Unknown keys are now rejected outright, so a key one block accepts and the
    other does not is no longer a difference in what gets stored -- it is a
    config that loads or does not.  ``description:`` is the obvious thing to
    write on either, and a user who commented their custom board the same way
    has every reason to expect it.
    """
    from helia_profiler.platform.custom import CustomBoardField, CustomSocField

    assert "description" in {member.value for member in CustomBoardField}
    assert "description" in {member.value for member in CustomSocField}

    soc = _custom_soc("oem4", _scratch_soc_spec(description="OEM part, rev B"))

    assert soc.name == "oem4"  # accepted, and backs no SocDef field


def test_a_custom_board_inherits_the_ble_reset_pin_of_the_board_it_is_based_on():
    """A Blue board's Cooper radio reset line must survive ``based_on``.

    ``_build_custom_boards`` never passed this through, so a custom board
    derived from a Blue EVB silently lost it (55 -> None).  The consequence is
    not cosmetic: ``_ble_reset.j2`` only emits the gating when the pin is set,
    so the power binary leaves the radio ungated and the board reads a higher
    idle current than the EVB it was copied from -- with nothing in the config
    to point at.  Rejecting unknown keys turned the obvious workaround (write
    the key) into a hard error, which is what makes passing it through the fix
    rather than a nicety.
    """
    base = get_board("apollo4p_blue_kxr_evb")
    registry = build_custom_platform_registry(
        {"custom_boards": {"blue_lab": {"based_on": "apollo4p_blue_kxr_evb"}}}
    )

    assert base.ble_reset_gpio_pin == 55
    assert registry.boards["blue_lab"].ble_reset_gpio_pin == 55


def test_a_custom_board_may_state_its_own_ble_reset_pin():
    """The point of the key: a rewired or differently-routed Blue board."""
    registry = build_custom_platform_registry(
        {
            "custom_boards": {
                "blue_lab": {"based_on": "apollo4p_blue_kxr_evb", "ble_reset_gpio_pin": 42}
            }
        }
    )

    assert registry.boards["blue_lab"].ble_reset_gpio_pin == 42


def test_a_custom_board_based_on_a_non_blue_board_has_no_ble_reset_pin():
    """Inheritance must not invent a pin for a board with no radio.

    ``_ble_reset.j2`` keys the whole block on this being set, so a default
    borrowed from somewhere would make a plain board drive an unrelated GPIO
    during the measured window.
    """
    registry = build_custom_platform_registry(
        {"custom_boards": {"plain_lab": {"based_on": "apollo510_evb"}}}
    )

    assert get_board("apollo510_evb").ble_reset_gpio_pin is None
    assert registry.boards["plain_lab"].ble_reset_gpio_pin is None


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


# ---------------------------------------------------------------------------
# target.custom_boards: GPIO pin validation (issue #149)
# ---------------------------------------------------------------------------

#: The four ``custom_boards`` keys that name a GPIO pad.  Every one of them
#: ends up configuring that pad as an output inside the measured window, so
#: every one of them carries the same hazards; the tests below are parametrized
#: over all four rather than over the one that prompted the audit.
_BOARD_PIN_FIELDS = (
    "default_sync_gpio_pin",
    "default_state_gpio_pin",
    "default_go_gpio_pin",
    "ble_reset_gpio_pin",
)


def _custom_board(spec, *, based_on="apollo510_evb"):
    target = {"custom_boards": {"lab": {"based_on": based_on, **spec}}}
    return build_custom_platform_registry(target).boards["lab"]


@pytest.mark.parametrize("field", _BOARD_PIN_FIELDS)
@pytest.mark.parametrize("value", [True, False])
def test_a_boolean_is_not_a_gpio_pad_number(field, value):
    """``bool`` is an ``int`` subclass, and on these fields that is a power bug.

    ``ble_reset_gpio_pin: true`` resolved to pad 1 and had the power binary
    configure GPIO 1 as an output and hold it low for the entire measured
    window -- an arbitrary, unrelated pin driven underneath a power capture,
    which is the exact silent corruption this field exists to prevent.
    ``false`` resolved to pad 0.  Nothing echoes the resolved pin back, so
    neither is recoverable by the user.

    The three sibling pins had the same hazard, so all four go through one
    shared parser and one test shape.  A new pin field that skips the parser
    fails as soon as it joins the list above.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_board({field: value})

    assert f"target.custom_boards.lab.{field}" in str(exc_info.value)
    assert "boolean" in str(exc_info.value)


@pytest.mark.parametrize("field", _BOARD_PIN_FIELDS)
@pytest.mark.parametrize("value", ["abc", -1, 29.5, [29], None])
def test_a_gpio_pin_that_is_not_a_pad_number_raises_config_error_not_a_traceback(field, value):
    """``load_config`` documents "never a raw exception"; a bare ``int()`` broke it.

    ``_prepare_merged_config`` is called *outside* ``load_config``'s ``try``,
    so ``int("abc")`` there escaped as ``ValueError: invalid literal for int()``
    with a traceback -- breaking the same contract this change's ``key=str``
    sort fix cites.  ``None`` covers an explicit ``null`` (``TypeError`` from
    ``int()``); ``29.5`` covers a float, which used to be truncated to pad 29
    without a word.
    """
    if field == "ble_reset_gpio_pin" and value is None:
        pytest.skip("null is a meaningful statement on this field -- see the test below")

    with pytest.raises(ConfigError) as exc_info:
        _custom_board({field: value})

    assert f"target.custom_boards.lab.{field}" in str(exc_info.value)
    assert exc_info.value.hint  # every rejection shows the shape to write


@pytest.mark.parametrize("field", ["default_state_gpio_pin", "default_go_gpio_pin"])
def test_zero_still_disables_the_sibling_wires(field):
    """The sibling pins keep their documented ``0`` sentinel.

    ``platform/board.py`` establishes 0 as "wire not present" for the 3-wire
    lock-step handshake, which degrades to the 1-wire gate-only form.  Those
    fields are plain ``int``s with no ``None`` spelling, so the sentinel is
    all they have -- rejecting 0 across all four pins would have taken a
    documented capability away to fix a problem only one of them has.
    """
    assert getattr(_custom_board({field: 0}), field) == 0


def test_zero_is_refused_for_the_ble_reset_pin():
    """The one pin where 0 has two readings, and both corrupt a capture silently.

    ``ble_reset_gpio_pin`` is ``int | None`` and says "no onboard radio" by
    being absent, so unlike its siblings it needs no sentinel -- which leaves 0
    meaning either "disabled" (to anyone going by the siblings) or pad 0.  Read
    as pad 0, the power binary drives an unrelated pad low for the whole
    window.  Read as disabled, a board genuinely wired to pad 0 leaves its
    radio free-running and reads high -- this change's own documented failure.
    Refusing it turns both into an error naming the field and both meanings.
    """
    with pytest.raises(ConfigError) as exc_info:
        _custom_board({"ble_reset_gpio_pin": 0}, based_on="apollo4p_blue_kxr_evb")

    assert "target.custom_boards.lab.ble_reset_gpio_pin" in str(exc_info.value)
    hint = exc_info.value.hint or ""
    assert "no onboard radio" in hint and "pad 0" in hint


def test_an_explicit_null_ble_reset_pin_declares_a_board_with_no_radio():
    """``null`` is how a board derived from a Blue EVB says its radio is gone.

    The counterpart to refusing 0: there has to be *some* way to drop an
    inherited pin, and absence cannot be it once ``based_on`` supplies one.
    """
    assert get_board("apollo4p_blue_kxr_evb").ble_reset_gpio_pin == 55

    board = _custom_board({"ble_reset_gpio_pin": None}, based_on="apollo4p_blue_kxr_evb")

    assert board.ble_reset_gpio_pin is None


def test_every_gate_on_the_ble_reset_pin_agrees_about_pad_zero():
    """Three gates read this field, and they must not disagree about 0.

    ``firmware/context.py`` (``power_binary_needs_gpio``) and
    ``firmware/__init__.py`` (nsx-gpio module selection) test ``is not None``;
    ``_ble_reset.j2`` used to test Jinja *truthiness*.  At pad 0 those split:
    the module got linked and ``nsx_gpio.h`` emitted for a block that never
    rendered.  The YAML surface now refuses 0, but ``BoardDef`` is public, so
    this pins the model rather than only the parser -- past the parser, 0 is
    an ordinary pad number everywhere.
    """
    from helia_profiler.firmware.render import _jinja_env

    board = BoardDef("lab", soc="apollo4p", channel="preview", ble_reset_gpio_pin=0)
    rendered = _jinja_env.get_template("_ble_reset.j2").render(
        power_only=True, ble_reset_gpio_pin=board.ble_reset_gpio_pin
    )

    assert board.ble_reset_gpio_pin is not None  # what both firmware gates read
    assert "nsx_gpio_init" in rendered  # ...and now what the template reads


# ---------------------------------------------------------------------------
# The key enums vs. what the builders actually read
# ---------------------------------------------------------------------------


def _keys_the_builders_read() -> set[str]:
    """Every spec key ``platform/custom.py`` looks up, read out of its own AST.

    String literals count only in *lookup* position -- ``spec.get("k", ...)``,
    ``"k" in spec``, ``spec["k"]`` -- so a key that appears in a docstring or
    an error-message hint cannot launder itself into looking read.  Enum member
    references (``CustomSocField.APP_FLASH_LOAD_ADDR.value``) count wherever
    they appear, because unlike a string they are already code that resolves to
    a real member, and the builders bind them to a local before looking up.
    Iterating an enum (``for field in CustomMemoryField``) counts for every
    member, because that shape cannot drift by construction.
    """
    import ast
    import inspect

    from helia_profiler.platform import custom as custom_module
    from helia_profiler.platform.custom import (
        CustomBoardField,
        CustomMemoryField,
        CustomSocField,
    )

    enums = {
        "CustomSocField": CustomSocField,
        "CustomMemoryField": CustomMemoryField,
        "CustomBoardField": CustomBoardField,
    }
    tree = ast.parse(inspect.getsource(custom_module))

    def key_of(node) -> set[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}
        # ``CustomSocField.APP_FLASH_LOAD_ADDR.value``
        member = node.value if isinstance(node, ast.Attribute) and node.attr == "value" else None
        if (
            isinstance(member, ast.Attribute)
            and isinstance(member.value, ast.Name)
            and member.value.id in enums
        ):
            return {enums[member.value.id][member.attr].value}
        return set()

    read: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and node.args:
                read |= key_of(node.args[0])
        elif isinstance(node, ast.Compare) and any(
            isinstance(op, (ast.In, ast.NotIn)) for op in node.ops
        ):
            read |= key_of(node.left)
        elif isinstance(node, ast.Subscript):
            read |= key_of(node.slice)
        elif isinstance(node, (ast.For, ast.comprehension)):
            if isinstance(node.iter, ast.Name) and node.iter.id in enums:
                read |= {member.value for member in enums[node.iter.id]}
        elif isinstance(node, ast.Attribute):
            read |= key_of(node)
    return read


def test_every_accepted_key_is_a_key_the_builders_actually_read():
    """The enums are rejection allow-lists; the builders read bare strings.

    ``_reject_unknown_keys`` exists to stop a key being accepted and then
    silently discarded.  But the allow-list and the reads are separate copies
    of the same strings, so adding an enum member without a matching read
    reopens that exact failure through the back door: the validator waves the
    key through and the builder never looks at it -- indistinguishable, from
    the user's side, from the typo case the rejection was built for.

    This does not demand that the builders *use* the enums (21 of the reads are
    bare literals, and converting them is a separate and noisier change); it
    demands only that the two agree.  ``description`` is the documented
    exception -- a free-form annotation accepted on both blocks and
    deliberately backing no field, pinned by
    ``test_both_custom_blocks_accept_the_same_free_form_description``.
    """
    from helia_profiler.platform.custom import (
        CustomBoardField,
        CustomMemoryField,
        CustomSocField,
    )

    # Config surface only: accepted, read by nothing, and that is the point.
    config_surface_only = {"description"}
    accepted = {
        member.value
        for enum_cls in (CustomSocField, CustomMemoryField, CustomBoardField)
        for member in enum_cls
    }

    assert (accepted - config_surface_only) <= _keys_the_builders_read()


def test_the_key_scan_reads_lookups_and_not_prose():
    """Guard on the guard above: a key named only in a docstring is not "read".

    The three positives below each cover a different lookup shape the scan has
    to recognise.  The negatives are the load-bearing half: ``custom.py`` is
    full of string constants that are not lookups -- error messages, hints, and
    above all the ``NAME = "value"`` lines of the enum declarations themselves.
    A scan that swept every string literal would pick those declarations up and
    make the test above vacuously true, passing with every builder read
    deleted, so it must pick up none of the constants below either.
    """
    read = _keys_the_builders_read()

    assert "starter_profile_board" in read  # bare literal in a `.get()`
    assert "app_flash_load_addr" in read  # only ever reached via the enum member
    assert "mram_kb" in read  # only ever reached by iterating CustomMemoryField
    assert "not_a_key_anywhere" not in read
    assert not any(s.startswith("target.custom_socs must be") for s in read)  # a message
    assert not any("Supported keys" in s for s in read)  # a hint
