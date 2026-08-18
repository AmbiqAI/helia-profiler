"""Contract: firmware render snapshots across SoC x transport x engine.

Renders the real firmware templates (``main.cc.j2`` for TFLM/heliaRT and
``main_aot.cc.j2`` for heliaAOT) through the profiler's real Jinja environment
for the supported (SoC x transport x engine) matrix, with template variables
sourced from platform metadata exactly as ``firmware.generate_app`` sources
them.

For each combination we snapshot a STABLE digest:

* ``markers`` — which feature blocks are active (GPIO sync, DWT init, USB
  timer, cache shims, extreme mode, ITM/SWO, RTT, Armv8-M PMU, ...).  This is
  the semantic contract: it says *what the firmware does*.
* ``sha256`` — a hash of the full render, catching any byte-level drift the
  marker set might miss.

Snapshots live in ``snapshots/firmware_render.json`` (committed).  When an
intentional template change lands, regenerate with::

    HPX_UPDATE_SNAPSHOTS=1 pytest tests/contracts/test_firmware_render_snapshots.py

and review the JSON diff.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from helia_profiler.engines import TFLM_ENGINE_HEADER
from helia_profiler.firmware import _jinja_env
from helia_profiler.platform import get_soc, list_socs

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "firmware_render.json"
_UPDATE = os.environ.get("HPX_UPDATE_SNAPSHOTS") == "1"

# Representative SoC per family.
_SOCS = ["apollo3p", "apollo4p", "apollo510"]
_TRANSPORTS = ["rtt", "usb_cdc", "swo", "uart"]
# tflm and helia-rt both render main.cc.j2 with the same engine header, so they
# produce identical output; helia-aot renders the distinct AOT template.
_ENGINES = ["tflm", "helia-rt", "helia-aot"]

# Feature markers: substring -> human name.  Presence is the semantic snapshot.
_MARKERS: dict[str, str] = {
    "gpio_sync": "kPowerSyncEnabled",
    "dwt_init": "dwt_init(",
    "usb_timer": "usb_timer_pause(",
    "cache_shims": "hpx_cache_",
    "extreme_mode": "HPX_EXTREME_MODE",
    "itm_swo": "nsx_itm_printf_enable(",
    "debug_itm": "NSX_DEBUG_ITM",
    "debug_uart": "NSX_DEBUG_UART",
    "rtt_config": "SEGGER_RTT_ConfigUpBuffer",
    "armv8m_pmu": "ARM_PMU_",
    "busy_loop_probe": "busy_loop",
    "auto_window": "window_min",
    "heartbeat": "HPX_HEARTBEAT",
    "ssram_power_ap5": "ns_power",
    "newlib_syscalls": "_sbrk",
    "peripheral_power_down": "AM_HAL_PWRCTRL_PERIPH_IOM0",
    # Which clock times the measured window. Without this marker the switch
    # from DWT to STIMER shows up only as a sha256 change, so the semantic
    # layer of the snapshot -- the part a reviewer actually reads -- stayed
    # silent about the fix.
    "stimer_window": "hpx_stimer_init(",
}


def _sample_pmu_passes() -> list[dict[str, object]]:
    return [
        {
            "name": "Cache",
            "custom": False,
            "event_ids": [],
            "counter_names": [
                "ARM_PMU_CPU_CYCLES",
                "ARM_PMU_INST_RETIRED",
            ],
            "num_counters": 2,
            "c_enum": "NSX_PMU_PRESET_BASIC_CPU",
            "group": "cpu",
        }
    ]


def _common_kwargs(soc_name: str, transport: str) -> dict:
    soc = get_soc(soc_name)
    backends = list(soc.profiling_backends)
    return {
        "iterations": 3,
        "warmup": 1,
        "clean_warmup": 1,
        "clean_iters": 3,
        "window_mode": "fixed",
        "window_target_ms": 1000,
        "window_min": 10,
        "window_max": 2000,
        "clean_window_probe": "infer",
        "clean_window_trace": False,
        "force_shared_sram": False,
        "pmu_passes": _sample_pmu_passes(),
        "pmu_pass_names": ["Cache"],
        "power_sync_enabled": False,
        "sync_gpio_pin": 22,
        "lockstep": False,
        "state_gpio_pin": 23,
        "go_gpio_pin": 24,
        "cmsis_device_header": soc.cmsis_header,
        "has_dcache": soc.capabilities.memory.has_dcache,
        "manages_shared_ssram_power": soc.capabilities.memory.has_shared_ssram_power_domain,
        "ssram_full_power_enum": soc.ssram_full_power_enum,
        "clean_window_timer": soc.capabilities.clock.clean_window_timer,
        "power_window_timer": soc.capabilities.power_window_timer,
        "gate_debug_domain_in_window": soc.capabilities.clock.gate_debug_domain_in_window,
        "broad_peripheral_shutdown": soc.capabilities.clock.broad_peripheral_shutdown,
        "crypto_otp_shutdown": soc.capabilities.clock.crypto_otp_shutdown,
        "has_radio_subsystem": soc.has_radio_subsystem,
        "pmu_max_ops": soc.pmu_max_ops,
        "transport": transport,
        "usb_serial_marker": None,
        "usb_serial_product": "NSX HPX Profiler",
        "extreme_mode": False,
        "arena_region": "tcm",
        "weights_region": "mram",
        "profiling_backends": backends,
        "has_armv8m_pmu": "armv8m-pmu" in backends,
        "perf_mode_symbol": "NSX_PERF_LOW",
        "perf_mode_mhz": 48 if soc.family.value == "ap3" else 96,
        "apollo3_burst": False,
        "heartbeat_enabled": True,
        "heartbeat_every_n_ops": 4,
        "heartbeat_every_ms": 0,
        "psram_clock_hz": 48_000_000,
    }


def _render(soc_name: str, transport: str, engine: str, power_only: bool = False) -> str:
    kwargs = _common_kwargs(soc_name, transport)
    if power_only:
        kwargs["power_only"] = True
    if engine == "helia-aot":
        kwargs.update(
            aot_prefix="fake",
            aot_op_manifest=[{"id": 0, "op_type": "CONV_2D"}],
            printf_linkage="static ",
            allocate_arenas=False,
            arena_regions=[],
        )
        return _jinja_env.get_template("main_aot.cc.j2").render(**kwargs)
    if engine not in ("tflm", "helia-rt"):
        # Production picks the template three ways (firmware/__init__.py:
        # HELIA_AOT -> main_aot.cc.j2, EXECUTORCH -> main_executorch.cc.j2,
        # else main.cc.j2), but this helper only ever branched on helia-aot.
        # Falling through for anything else would render main.cc.j2 while the
        # snapshot key says otherwise -- entries byte-identical to tflm,
        # asserting coverage of a template no contract test has ever rendered.
        # Fail loudly instead: a silent wrong answer here is worse than none,
        # because the snapshot it writes reads as real coverage forever after.
        raise AssertionError(
            f"_render() has no branch for engine {engine!r}; add one that renders "
            "the template production would select for it before adding the engine "
            "to _ENGINES."
        )
    kwargs.update(
        engine_header=TFLM_ENGINE_HEADER,
        arena_size=65_536,
        model_size=1024,
        resolver_mode="all",
        resolver_max_ops=2,
        resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
        resource_variable_count=0,
        printf_linkage="",
    )
    return _jinja_env.get_template("main.cc.j2").render(**kwargs)


def _digest(rendered: str) -> dict:
    markers = {name: (token in rendered) for name, token in _MARKERS.items()}
    return {
        "markers": markers,
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }


def _all_combos() -> list[tuple[str, str, str]]:
    return [
        (soc, transport, engine)
        for soc in _SOCS
        for transport in _TRANSPORTS
        for engine in _ENGINES
    ]


# power_only variant matrix (WP1): dedicated power binary, no transport ever
# initialized.  Only rendered for "rtt" — power_only forces NSX_DEBUG_NONE
# regardless of the requested transport, so varying transport here would not
# exercise any additional code path (see main.cc.j2/main_aot.cc.j2 power_only
# guards).  Covers every SoC family x engine per the WP1 verification matrix.
_POWER_TRANSPORT = "rtt"


def _power_combos() -> list[tuple[str, str, str]]:
    return [(soc, _POWER_TRANSPORT, engine) for soc in _SOCS for engine in _ENGINES]


def _key(soc: str, transport: str, engine: str, power_only: bool = False) -> str:
    suffix = "|power" if power_only else ""
    return f"{soc}|{transport}|{engine}{suffix}"


def _build_all() -> dict:
    result = {
        _key(soc, transport, engine): _digest(_render(soc, transport, engine))
        for soc, transport, engine in _all_combos()
    }
    result.update(
        {
            _key(soc, transport, engine, power_only=True): _digest(
                _render(soc, transport, engine, power_only=True)
            )
            for soc, transport, engine in _power_combos()
        }
    )
    return result


def _maybe_regenerate() -> None:
    if _UPDATE:
        _SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SNAPSHOT_PATH.write_text(json.dumps(_build_all(), indent=2, sort_keys=True) + "\n")


_maybe_regenerate()

_SNAPSHOTS: dict = (
    json.loads(_SNAPSHOT_PATH.read_text()) if _SNAPSHOT_PATH.exists() else {}
)

_REGEN_HINT = (
    "Firmware render output changed. If this change is intentional, review the "
    "diff then regenerate the snapshot with:\n"
    "    HPX_UPDATE_SNAPSHOTS=1 pytest tests/contracts/test_firmware_render_snapshots.py"
)


@pytest.mark.parametrize(
    "soc,transport,engine",
    _all_combos(),
    ids=[_key(*c) for c in _all_combos()],
)
def test_render_matches_snapshot(soc, transport, engine):
    assert _SNAPSHOTS, (
        "no firmware render snapshot committed — generate it with "
        "HPX_UPDATE_SNAPSHOTS=1"
    )
    key = _key(soc, transport, engine)
    assert key in _SNAPSHOTS, f"{key} missing from snapshot. {_REGEN_HINT}"

    current = _digest(_render(soc, transport, engine))
    expected = _SNAPSHOTS[key]

    # Semantic contract first: which feature blocks are active.
    assert current["markers"] == expected["markers"], (
        f"[{key}] active feature blocks changed:\n"
        f"  expected: {expected['markers']}\n"
        f"  actual:   {current['markers']}\n{_REGEN_HINT}"
    )
    # Byte-level contract: catch any render drift the markers miss.
    assert current["sha256"] == expected["sha256"], f"[{key}] render hash changed. {_REGEN_HINT}"


@pytest.mark.parametrize(
    "soc,transport,engine",
    _power_combos(),
    ids=[_key(*c, power_only=True) for c in _power_combos()],
)
def test_power_only_render_matches_snapshot(soc, transport, engine):
    """WP1: dedicated power binary (power_only=true) render snapshots.

    Rendered from the SAME main.cc.j2 / main_aot.cc.j2 templates as the
    regular (non-power) matrix above — power_only never introduces a new
    template, only a new Jinja variable — so this exercises the identical
    template files, just with the power_only branches taken.
    """
    assert _SNAPSHOTS, (
        "no firmware render snapshot committed — generate it with "
        "HPX_UPDATE_SNAPSHOTS=1"
    )
    key = _key(soc, transport, engine, power_only=True)
    assert key in _SNAPSHOTS, f"{key} missing from snapshot. {_REGEN_HINT}"

    current = _digest(_render(soc, transport, engine, power_only=True))
    expected = _SNAPSHOTS[key]

    assert current["markers"] == expected["markers"], (
        f"[{key}] active feature blocks changed:\n"
        f"  expected: {expected['markers']}\n"
        f"  actual:   {current['markers']}\n{_REGEN_HINT}"
    )
    assert current["sha256"] == expected["sha256"], f"[{key}] render hash changed. {_REGEN_HINT}"


def test_power_only_never_initializes_transport():
    """WP1 content contract: power_only firmware never brings up UART/SWO/USB,
    never emits the per-layer PMU pass loop / CSV dump / HPX_START/HPX_END
    sentinels, but still runs the shared model-init + gated clean window.
    """
    import re

    def _strip_line_comments(src: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

    for soc, transport, engine in _power_combos():
        rendered = _render(soc, transport, engine, power_only=True)
        code_only = _strip_line_comments(rendered)

        assert "NSX_DEBUG_NONE" in rendered, (soc, transport, engine)
        assert "hpx_sync_window_begin" in rendered, (soc, transport, engine)
        assert "hpx_sync_window_end" in rendered, (soc, transport, engine)
        if engine == "helia-aot":
            assert "_model_init(" in rendered, (soc, transport, engine)
        else:
            assert "InitializeTarget" in rendered, (soc, transport, engine)
            assert "GetModel" in rendered, (soc, transport, engine)

        for forbidden in (
            "nsx_uart_printf_enable(",
            "nsx_itm_printf_enable(",
            "usb_timer_",
            "HPX_PRESET",
            "HPX_START",
            "HPX_END",
        ):
            assert forbidden not in code_only, (soc, transport, engine, forbidden)


def test_window_is_never_timed_by_a_domain_the_binary_powers_down():
    """No power render may both disable the debug power domain and time its
    measured window with DWT->CYCCNT.

    DWT lives in the CoreSight debug domain. Where the power binary calls
    am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG) (AP4 families,
    via broad_peripheral_shutdown), an in-window DWT read returns garbage, the
    accumulated cycle count is meaningless, and the terminal report's
    elapsed_us -- derived from it -- is inflated. Energy survives (the monitor
    integrates in hardware) but average power and current are silently divided
    by the inflation factor. Measured ~7x on Apollo4 Blue Plus, in-session:
    the unfixed build reported 6027 us/inference against the fixed build's
    866 us with identical energy per inference.

    Scope: this pins the invariant across the *render matrix* -- any future
    SoC that gains the broad shutdown must not also inherit a DWT-timed
    window. It is a textual check, keyed on the literal
    am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG) and
    DWT->CYCCNT spellings between the hpx_sync_window_begin();/end(); call
    sites, so it does NOT defend against refactors that rename or wrap any
    of those (a DWT read behind a helper, a differently-spelled shutdown).
    The OTHER way the domain disappears on Cortex-M4F parts -- a free-running
    binary with no debugger asserting CDBGPWRUPREQ -- is pinned separately, by
    capability rather than by spelling, in
    ``test_free_running_power_binary_never_times_the_window_with_dwt``.
    """
    import re

    def _code_only(src: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

    checked = 0
    for soc, transport, engine in _power_combos():
        code = _code_only(_render(soc, transport, engine, power_only=True))
        if "am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG)" not in code:
            continue
        checked += 1
        # Anchor on the CALL sites, not the names: the no-op `static inline`
        # definitions appear earlier in the file (and an end() call precedes
        # the begin() call), so slicing on bare names yields an empty window
        # and silently asserts nothing.
        begin = code.index("hpx_sync_window_begin();")
        window = code[begin : code.index("hpx_sync_window_end();", begin)]
        assert "DWT->CYCCNT" not in window, (
            f"{soc}|{transport}|{engine}: window timed by DWT while the same "
            "binary disables the debug power domain DWT lives in"
        )
    assert checked, (
        "no power render disables the debug domain — this test lost its subject, "
        "so it is no longer pinning anything"
    )


def test_every_soc_that_cannot_read_dwt_unwatched_resolves_to_stimer():
    """``SocCapabilities.power_window_timer`` is the single source of the
    predicate; check it directly for every registered SoC, not just the three
    representatives the render matrix covers.

    A power binary may keep DWT only when it neither powers the debug domain
    down itself nor depends on an attached probe to keep that domain alive.
    No registered SoC is in that set today (AP3/AP4 are Cortex-M4F, AP5
    prefers STIMER outright) -- the assertion is written as an implication so
    it stays meaningful if one ever is.
    """
    for soc in list_socs():
        caps = soc.capabilities
        unwatched_dwt_is_unreadable = (
            caps.clock.broad_peripheral_shutdown
            or caps.transport.requires_attached_probe_for_cycles
        )
        if unwatched_dwt_is_unreadable or caps.clock.clean_window_timer == "stimer":
            assert caps.power_window_timer == "stimer", soc.name
        else:
            assert caps.power_window_timer == caps.clock.clean_window_timer, soc.name
        # The profile binary runs with a transport (and so a probe) attached
        # and keeps the family preference -- the power override must not leak
        # into it.
        assert caps.clock.clean_window_timer in {"dwt", "stimer"}, soc.name


def test_free_running_power_binary_never_times_the_window_with_dwt():
    """No power render for a family that needs an attached probe to read DWT
    may time its measured window with DWT->CYCCNT.

    This is the capability-driven half of the invariant. The sibling test above
    keys on the *literal shutdown call*, so it only catches families that power
    the debug domain down themselves (AP4). It cannot see the other, equally
    fatal mechanism: on the Cortex-M4F parts DWT lives in the core debug power
    domain and stays powered only while a debugger asserts CDBGPWRUPREQ, which
    firmware cannot set. The dedicated power binary free-runs unwatched once
    flashed (WP4 -- the probe is released after flash+reset and the Joulescope
    watches GPIO, not SWD), so on Apollo3 the counter never advances: elapsed_us
    lands at 0, HPX_CLEAN_INFER_AVG_US at 0, and every per-inference power
    metric derived from them is suppressed or wrong.

    ``transport.requires_attached_probe_for_cycles`` is the capability that
    already records exactly this fact (confirmed empirically on AP3 in
    2026-06: AOT-over-UART read 0 cycles until a probe was held attached), so
    this keys on it rather than on any single shutdown spelling.
    """
    import re

    def _code_only(src: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

    checked = 0
    for soc, transport, engine in _power_combos():
        if not get_soc(soc).capabilities.transport.requires_attached_probe_for_cycles:
            continue
        checked += 1
        code = _code_only(_render(soc, transport, engine, power_only=True))
        # Anchor on the CALL sites, not the names: the no-op `static inline`
        # definitions appear earlier in the file (and an end() call precedes
        # the begin() call), so slicing on bare names yields an empty window
        # and silently asserts nothing.
        begin = code.index("hpx_sync_window_begin();")
        window = code[begin : code.index("hpx_sync_window_end();", begin)]
        assert "DWT->CYCCNT" not in window, (
            f"{soc}|{transport}|{engine}: window timed by DWT in a free-running "
            "power binary, where nothing holds the core debug power domain up"
        )
    assert checked, (
        "no power render targets a probe-dependent-cycles family — this test "
        "lost its subject, so it is no longer pinning anything"
    )


def test_hal_umbrella_header_is_included_at_most_once():
    """``am_mcu_apollo.h`` has several independent consumers in the main
    templates (Apollo3 burst, the Armv8-M PMU, STIMER window timing, the broad
    peripheral / crypto-otp shutdowns) guarded by separate blocks. They must
    stay mutually exclusive: main.cc.j2 keeps them as separate blocks, while
    main_aot.cc.j2 merges the STIMER and shutdown cases into one guard. This
    pins the shared invariant so the two structures cannot silently diverge
    into a double include -- or, as the AOT template did before this change,
    into no include at all for a render that calls am_hal_stimer_*.
    """
    for soc, transport, engine in _all_combos():
        rendered = _render(soc, transport, engine)
        assert rendered.count('#include "am_mcu_apollo.h"') <= 1, (
            soc,
            transport,
            engine,
        )
    for soc, transport, engine in _power_combos():
        rendered = _render(soc, transport, engine, power_only=True)
        assert rendered.count('#include "am_mcu_apollo.h"') <= 1, (
            soc,
            transport,
            engine,
        )
        if "am_hal_stimer_" in rendered:
            assert '#include "am_mcu_apollo.h"' in rendered, (
                f"{soc}|{transport}|{engine}: calls am_hal_stimer_* with no "
                "AmbiqSuite HAL umbrella header in scope"
            )


def test_snapshot_covers_exactly_the_current_matrix():
    """The committed snapshot must match the code's supported matrix exactly."""
    expected_keys = {_key(*c) for c in _all_combos()} | {
        _key(*c, power_only=True) for c in _power_combos()
    }
    assert set(_SNAPSHOTS) == expected_keys, _REGEN_HINT


def test_transport_specific_blocks_are_pinned():
    """Sanity anchors so a broken harness can't silently pin empty output."""
    usb = _digest(_render("apollo510", "usb_cdc", "tflm"))
    assert usb["markers"]["usb_timer"] is True
    swo = _digest(_render("apollo510", "swo", "tflm"))
    assert swo["markers"]["debug_itm"] is True
    rtt = _digest(_render("apollo510", "rtt", "tflm"))
    assert rtt["markers"]["rtt_config"] is True
    # AP5 has the Armv8-M PMU; AP3/AP4 are DWT-only.
    assert _digest(_render("apollo510", "rtt", "tflm"))["markers"]["armv8m_pmu"] is True
    assert _digest(_render("apollo3p", "rtt", "tflm"))["markers"]["armv8m_pmu"] is False


def test_ble_reset_only_in_power_only_binary_for_blue_boards():
    """Blue-variant boards (Cooper BLE SiP) hold the radio in hardware reset
    -- but ONLY in the dedicated power binary (power_only=True). The
    transport-attached PMU-phase binary is untouched, and non-Blue boards
    (ble_reset_gpio_pin unset) never emit this code at all.
    """
    kwargs = _common_kwargs("apollo4p", "rtt")
    kwargs.update(
        engine_header=TFLM_ENGINE_HEADER,
        arena_size=65_536,
        model_size=1024,
        resolver_mode="all",
        resolver_max_ops=2,
        resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
        resource_variable_count=0,
        printf_linkage="",
        ble_reset_gpio_pin=55,
        # Mirrors FirmwareRenderContext.power_binary_needs_gpio: true here
        # because this board carries a BLE reset pin, even though power_sync
        # is off. The CMake link line reads the same flag, so the include and
        # the linked module cannot disagree.
        power_binary_needs_gpio=True,
    )

    power_rendered = _jinja_env.get_template("main.cc.j2").render(**{**kwargs, "power_only": True})
    assert "bleResetCfg" in power_rendered
    assert "NSX_GPIO_LEVEL_LOW" in power_rendered
    assert "nsx_gpio.h" in power_rendered

    transport_rendered = _jinja_env.get_template("main.cc.j2").render(
        **{**kwargs, "power_only": False}
    )
    assert "bleResetCfg" not in transport_rendered

    # A board with no Cooper radio (ble_reset_gpio_pin unset) never emits it,
    # even in the power_only binary.
    no_ble_kwargs = dict(kwargs)
    no_ble_kwargs.pop("ble_reset_gpio_pin")
    no_ble_rendered = _jinja_env.get_template("main.cc.j2").render(
        **{**no_ble_kwargs, "power_only": True}
    )
    assert "bleResetCfg" not in no_ble_rendered


def test_peripheral_power_down_ap4_power_only_only():
    """AP4's broad peripheral power-down (mirrors AutoDeploy's
    ns_power_down_peripherals()) only fires in the dedicated power binary,
    and only for the AP4 family -- AP3's AutoDeploy implementation is an
    empty no-op and AP5's only clears XTAL/VCOMP, so neither needs (or gets)
    this block.
    """
    ap4_power = _render("apollo4p", "rtt", "tflm", power_only=True)
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" in ap4_power
    assert "AM_HAL_PWRCTRL_PERIPH_DEBUG" in ap4_power
    assert "AM_HAL_PWRCTRL_PERIPH_MSPI0" in ap4_power  # no PSRAM in _common_kwargs

    ap4_transport = _render("apollo4p", "rtt", "tflm", power_only=False)
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" not in ap4_transport


def test_crypto_otp_shutdown_ap5_power_only_only():
    """AP5's narrow crypto/OTP/VCOMP power-down (mirrors the unconditional
    part of AutoDeploy's ns_power_platform_config()) only fires in the
    dedicated power binary, and only for AP5-family SoCs. This is
    deliberately separate from/narrower than AP4's broad_peripheral_shutdown
    (no IOM/UART/memory changes -- see _crypto_otp_shutdown.j2 docstring).
    apollo330P additionally emits am_hal_pwrctrl_rss_pwroff() (its HAL
    exposes the internal radio-subsystem power-down AutoDeploy also calls);
    apollo510 does not, since its HAL variant lacks the symbol.
    """
    ap510_power = _render("apollo510", "rtt", "tflm", power_only=True)
    assert "AM_HAL_PWRCTRL_PERIPH_CRYPTO" in ap510_power
    assert "AM_HAL_PWRCTRL_PERIPH_OTP" in ap510_power
    assert "am_hal_pwrctrl_rss_pwroff" not in ap510_power  # not on plain apollo510's HAL

    ap510_transport = _render("apollo510", "rtt", "tflm", power_only=False)
    assert "AM_HAL_PWRCTRL_PERIPH_CRYPTO" not in ap510_transport

    ap330_power = _render("apollo330P", "rtt", "tflm", power_only=True)
    assert "am_hal_pwrctrl_rss_pwroff" in ap330_power

    # AP4 doesn't get this narrow block -- broad_peripheral_shutdown already
    # covers crypto/VCOMP there.
    ap4_power = _render("apollo4p", "rtt", "tflm", power_only=True)
    assert "am_hal_pwrctrl_rss_pwroff" not in ap4_power


def test_extreme_mode_power_only_only():
    """extreme_mode (SSRAM off + MRAM collapsed to a single NVM bank) only
    fires in the dedicated power binary (2026-07 finding: it used to fire
    unconditionally in both binaries, risking a firmware-size overflow
    crash in the larger transport-attached PMU-phase binary for zero
    measurement benefit -- DWT/PMU cycle counts don't depend on SSRAM/NVM
    power state). Requires arena+weights both in TCM.
    """
    kwargs = _common_kwargs("apollo510", "rtt")
    kwargs.update(
        engine_header=TFLM_ENGINE_HEADER,
        arena_size=65_536,
        model_size=1024,
        resolver_mode="all",
        resolver_max_ops=2,
        resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
        resource_variable_count=0,
        printf_linkage="",
        arena_region="tcm",
        weights_region="tcm",
        extreme_mode=True,
    )
    power_rendered = _jinja_env.get_template("main.cc.j2").render(**{**kwargs, "power_only": True})
    assert "AM_HAL_PWRCTRL_NVM0_ONLY" in power_rendered
    assert "EXTREME MODE" in power_rendered

    transport_rendered = _jinja_env.get_template("main.cc.j2").render(
        **{**kwargs, "power_only": False}
    )
    assert "AM_HAL_PWRCTRL_NVM0_ONLY" not in transport_rendered
    assert "EXTREME MODE" not in transport_rendered

    # Still requires TCM/TCM even in the power_only binary.
    non_tcm_kwargs = {**kwargs, "arena_region": "sram", "weights_region": "mram"}
    non_tcm_rendered = _jinja_env.get_template("main.cc.j2").render(
        **{**non_tcm_kwargs, "power_only": True}
    )
    assert "AM_HAL_PWRCTRL_NVM0_ONLY" not in non_tcm_rendered


def test_pmu_profiler_sram_placement_transport_only_on_ap5():
    """HpxPmuProfiler (g_profiler) moves to NSX_MEM_SRAM (freeing TCM
    for the model) only in the transport-attached PMU-phase binary, and
    only takes effect where it matters (AP5 family, which needs the
    shared SSRAM domain explicitly powered). The dedicated power binary
    keeps g_profiler in default .bss unconditionally -- even though it's
    unused there -- to avoid adding an SSRAM power-on step that would
    perturb the very power measurement that binary exists to keep clean.
    """
    ap510_transport = _render("apollo510", "rtt", "tflm", power_only=False)
    # NSX_MEM_SRAM (initialized .shared, copied from MRAM), NOT SRAM_BSS
    # (NOLOAD zero-fill would discard the polymorphic object's vtable
    # pointer image -- NULL-vptr bus fault at the first virtual call,
    # found on real Apollo330mP hardware 2026-07).
    assert "NSX_MEM_SRAM static HpxPmuProfiler g_profiler;" in ap510_transport
    assert "AM_HAL_PWRCTRL_SRAM_3M" in ap510_transport  # SSRAM powered on

    ap510_power = _render("apollo510", "rtt", "tflm", power_only=True)
    assert "NSX_MEM_SRAM static HpxPmuProfiler g_profiler;" not in ap510_power
    assert "static HpxPmuProfiler g_profiler;" in ap510_power
    assert "Shared SSRAM power-on" not in ap510_power

    # AP3 has no shared-SSRAM concept at all -- NSX_MEM_SRAM_BSS still
    # applies (falls back gracefully per nsx_mem.h), but there is no
    # SSRAM power-on step to add since manages_shared_ssram_power is
    # AP5-only.
    ap3_transport = _render("apollo3p", "rtt", "tflm", power_only=False)
    assert "NSX_MEM_SRAM static HpxPmuProfiler g_profiler;" in ap3_transport
    assert "Shared SSRAM power-on" not in ap3_transport


def test_ssram_full_power_enum_is_per_soc():
    """The AmbiqSuite HAL enum for "power on the entire shared SSRAM array"
    varies by SoC (it encodes each part's actual SSRAM capacity) even
    though it maps to the same underlying register value on every AP5
    part. AP510 has 3 MB (AM_HAL_PWRCTRL_SRAM_3M); apollo330P's real
    SSRAM is only ~1.75 MB and its HAL does not define SRAM_3M at all
    (confirmed 2026-07 against the real synced HAL headers) -- it must
    use AM_HAL_PWRCTRL_SRAM_1P75M instead, or the generated firmware
    fails to compile on that board.
    """
    kwargs = _common_kwargs("apollo330P", "rtt")
    kwargs.update(
        engine_header=TFLM_ENGINE_HEADER,
        arena_size=65_536,
        model_size=1024,
        resolver_mode="all",
        resolver_max_ops=2,
        resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
        resource_variable_count=0,
        printf_linkage="",
        arena_region="sram",
        weights_region="mram",
    )
    ap330_rendered = _jinja_env.get_template("main.cc.j2").render(**kwargs)
    assert "AM_HAL_PWRCTRL_SRAM_1P75M" in ap330_rendered
    assert "AM_HAL_PWRCTRL_SRAM_3M" not in ap330_rendered

    ap510_rendered = _render("apollo510", "rtt", "tflm")
    assert "AM_HAL_PWRCTRL_SRAM_3M" in ap510_rendered
    assert "AM_HAL_PWRCTRL_SRAM_1P75M" not in ap510_rendered


    # AP3/AP5 never emit this block, even in the power_only binary --
    # matches AutoDeploy's own per-family ns_power_down_peripherals().
    ap3_power = _render("apollo3p", "rtt", "tflm", power_only=True)
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" not in ap3_power
    ap510_power = _render("apollo510", "rtt", "tflm", power_only=True)
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" not in ap510_power


def test_peripheral_power_down_skips_mspi_when_psram_in_use():
    """MSPI0-2 must stay enabled when PSRAM actually backs the arena/weights
    -- disabling them would break a live PSRAM-resident power capture.
    """
    kwargs = _common_kwargs("apollo4p", "rtt")
    kwargs.update(
        engine_header=TFLM_ENGINE_HEADER,
        arena_size=65_536,
        model_size=1024,
        resolver_mode="all",
        resolver_max_ops=2,
        resolver_registrations=["r.AddConv2D();", "r.AddSoftmax();"],
        resource_variable_count=0,
        printf_linkage="",
        arena_region="psram",
        power_only=True,
    )
    rendered = _jinja_env.get_template("main.cc.j2").render(**kwargs)
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" in rendered  # rest of the block still fires
    assert "AM_HAL_PWRCTRL_PERIPH_MSPI0" not in rendered
