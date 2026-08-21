"""Contract: firmware render snapshots across SoC x transport x engine.

Renders the real firmware templates (``main.cc.j2`` for TFLM/heliaRT,
``main_aot.cc.j2`` for heliaAOT and ``main_executorch.cc.j2`` for ExecuTorch)
through the profiler's real Jinja environment for the supported
(SoC x transport x engine) matrix, with template variables sourced from
platform metadata exactly as ``firmware.generate_app`` sources them.

The matrix is not a full cross product: ExecuTorch is Cortex-M55-only in
production and has no dedicated power binary, so it pairs with apollo510
alone and appears in neither the power nor the busy-loop matrices (see
``_ENGINE_SOCS`` / ``_MATRIX_ENGINES``).

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

from helia_profiler.engines import TFLM_ENGINE_HEADER, EngineType
from helia_profiler.firmware import _jinja_env
from helia_profiler.firmware.context import resolve_window_timer
from helia_profiler.platform import get_soc, list_socs

_SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "firmware_render.json"
_UPDATE = os.environ.get("HPX_UPDATE_SNAPSHOTS") == "1"

# Representative SoC per family.
_SOCS = ["apollo3p", "apollo4p", "apollo510"]
_TRANSPORTS = ["rtt", "usb_cdc", "swo", "uart"]
# tflm and helia-rt both render main.cc.j2 with the same engine header, so they
# produce identical output; helia-aot and executorch render their own templates.
_ENGINES = ["tflm", "helia-rt", "helia-aot", "executorch"]

#: Engines whose supported SoC set is narrower than ``_SOCS``.  ExecuTorch is
#: built for Cortex-M55 only (the NSX ExecuTorch runtime needs Helium; every
#: AP3/AP4 part in this matrix is Cortex-M4F), so pairing it with apollo3p or
#: apollo4p would snapshot a render production can never produce.
_ENGINE_SOCS: dict[str, list[str]] = {"executorch": ["apollo510"]}

#: Engines that appear in the power_only and busy-loop matrices.  ExecuTorch is
#: excluded from both: ``stages.preflight._check_transport_support`` rejects
#: engine.type=executorch with power.enabled (pinned by
#: ``test_executorch_power_tripwire.py``), and the busy_loop clean-window probe
#: exists only to gate an external power capture, so neither combination is
#: reachable from a supported configuration.
_MATRIX_ENGINES = [e for e in _ENGINES if e != "executorch"]


def _socs_for(engine: str) -> list[str]:
    return _ENGINE_SOCS.get(engine, _SOCS)

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
    # Which clock times the measured window.  Keyed on the in-window BRACKET,
    # not on hpx_stimer_init( -- the helper's `static inline` definitions
    # render whenever window_timer == "stimer" even on paths that then time
    # the window some other way, so keying on the definition made the marker
    # read true for renders whose window STIMER never touched (it could not
    # tell #112's fixed and unfixed busy_loop renders apart). clean_stimer_t0
    # exists only where the window is actually bracketed by STIMER.
    "stimer_window": "clean_stimer_t0",
    # Whether the clean window holds until the host debug probe has attached
    # (#121), whether it self-checks for a stalled cycle counter, and whether
    # it calibrates that counter against an independent clock first. Semantic,
    # not cosmetic: without markers, losing any of them would show up only as a
    # sha256 change -- which is how the DWT->STIMER switch nearly slipped past
    # the reviewable layer of this snapshot.
    "clean_window_attach_wait": "HPX_CLEAN_WINDOW_ATTACH_WAIT_MS",
    "clean_window_stall_check": "HPX_CLEAN_STALLED_ITERS",
    "clean_window_rate_probe": "HPX_CLEAN_DWT_RATE_CYC",
    # Whether the render announces which engine produced it. Emitted by the
    # shared skeleton for every transport-attached binary and by none of the
    # power binaries (which never bring a transport up), so this marker also
    # says which of the two a render is. The per-engine SPELLING is pinned
    # separately by test_every_render_declares_its_engine_on_the_wire below --
    # a marker can only see presence, and every engine sharing one wrong name
    # would look identical here.
    "engine_wire_id": "HPX_ENGINE=",
}


def test_no_test_builds_a_look_alike_env_over_production_templates():
    """Firmware/engine templates are only rendered through production envs (#119).

    Tests used to build their own ``jinja2.Environment`` with ``trim_blocks``
    and ``lstrip_blocks`` enabled, which production does not set. Output then
    differed from what ships -- 42 lines on the ExecuTorch template alone -- so
    those tests were structurally blind to whitespace-control mistakes. Three
    such near-misses were caught in this arc only by hand-diffing full renders.

    The first version of this guard matched ``jinja2.Environment(...)`` and was
    proven evadable three ways by adversarial review: a loader hoisted into a
    variable (the non-greedy match stopped at PackageLoader's own paren), an
    env built in a helper module (only ``test_*.py`` was scanned), and
    ``conftest.py`` (same glob gap) -- plus ``from jinja2 import Environment``
    and ``_jinja_env.overlay(trim_blocks=True)``.

    So key on what an evader cannot avoid instead: to render production
    templates you must construct a loader over a production template package
    (or overlay the production env with the divergent flags). Scan EVERY
    Python file under tests/ for those constructions, wherever the Environment
    itself is assembled. Production template packages are
    ``helia_profiler.firmware`` and ``helia_profiler.engines`` (the heliaAOT
    compile env) -- both envs set neither trim flag.

    Known limits, stated rather than implied: a loader built outside tests/
    or a path assembled dynamically at runtime is not caught. Byte-level
    protection against actual render drift lives in the sha256 snapshots;
    this guard polices the authoring pattern that made tests blind to it.
    """
    import re

    tests_root = Path(__file__).resolve()
    while tests_root.name != "tests":
        tests_root = tests_root.parent
        assert tests_root != tests_root.parent, "no tests/ ancestor found"
    self_path = Path(__file__).resolve()

    package_loader = re.compile(
        r"PackageLoader\(\s*[\"']helia_profiler\.(?:firmware|engines)[\"']",
        re.DOTALL,
    )
    fs_loader = re.compile(
        r"FileSystemLoader\([^)]*(?:firmware|engines)[/\\]+templates",
        re.DOTALL,
    )
    overlay = re.compile(r"\.overlay\(([^)]*)\)", re.DOTALL)

    offenders = []
    for path in sorted(tests_root.rglob("*.py")):
        if path.resolve() == self_path:
            continue  # this file spells the patterns out
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(tests_root)
        for match in package_loader.finditer(text):
            offenders.append(f"{rel}: {match.group(0)[:60]}")
        for match in fs_loader.finditer(text):
            offenders.append(f"{rel}: {match.group(0)[:60]}")
        for match in overlay.finditer(text):
            if "trim_blocks" in match.group(1) or "lstrip_blocks" in match.group(1):
                offenders.append(f"{rel}: .overlay({match.group(1)[:50]}")

    assert not offenders, (
        "these files build a loader (or overlay) over production templates "
        "instead of importing the production env "
        "(helia_profiler.firmware._jinja_env / the heliaAOT compile env), so "
        "their renders differ from what ships and cannot see "
        "whitespace-control mistakes:\n  " + "\n  ".join(offenders)
    )


def _sample_pmu_passes() -> list[dict[str, object]]:
    """One pass, in the shape production actually emits.

    ``FirmwareRenderContext._resolve_pmu_passes`` builds every pass with
    ``custom=True``, real ``0xNNNN`` event ids and ``c_enum=None`` -- the
    preset branch of the pass-init blocks is unreachable from a real run.
    This used to pin ``custom=False`` with an EMPTY ``event_ids``, which is
    not a shape production can produce, and on the ExecuTorch template (whose
    ``engine_pass_init`` has no preset branch) it snapshotted invalid C: a
    zero-size ``static const uint32_t ids[]`` that ``profiler_init`` then
    indexes. Mirroring production keeps the pinned renders compilable and
    keeps the snapshotted branch the one that ships.

    tests/test_template_render.py keeps a ``custom=False`` sample on purpose --
    its smoke coverage of the preset branch is the only thing exercising it.
    """
    return [
        {
            "name": "Cache",
            "custom": True,
            "event_ids": ["0x0011", "0x0008", "0x0023", "0x0024"],
            "counter_names": [
                "ARM_PMU_CPU_CYCLES",
                "ARM_PMU_INST_RETIRED",
                "ARM_PMU_STALL_FRONTEND",
                "ARM_PMU_STALL_BACKEND",
            ],
            "num_counters": 4,
            "c_enum": None,
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
        # Sourced from the capability exactly as FirmwareRenderContext does, so
        # the snapshot exercises the real per-SoC value rather than a default.
        "clean_window_needs_probe_attach": soc.capabilities.clean_window_needs_probe_attach,
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
        # HPX_ENGINE= is emitted by the shared skeleton for every engine, so
        # every render needs the wire name.  Defaulted to the tflm spelling
        # here and overridden per engine by _render(); the direct render sites
        # in this file all go through _common_kwargs and render main.cc.j2.
        "engine_wire_name": "tflm",
    }


# EngineType value -> HPX_ENGINE= wire spelling (hyphens are not legal in the
# host parser's HPX_(\w+)=value key, so they become underscores; mirrors
# FirmwareRenderContext.to_template_vars()).
_ENGINE_WIRE_NAMES = {
    "tflm": "tflm",
    "helia-rt": "helia_rt",
    "helia-aot": "helia_aot",
    "executorch": "executorch",
}


def test_engine_wire_names_mirror_the_engine_type_property():
    """Bind this file's hand-mirrored wire names to the production derivation.

    ``FirmwareRenderContext.to_template_vars`` sources ``engine_wire_name``
    from ``EngineType.wire_name``; the map above mirrors it so renders can be
    built without a PipelineContext.  Unbound, the HPX_ENGINE contract below
    would only prove the mirror is self-consistent -- exactly the dead-branch
    shape #162 Phase 2 review found.  The literals are pinned too: these
    values go out on the wire and label every result for their engine.
    """
    assert _ENGINE_WIRE_NAMES == {
        engine.value: engine.wire_name for engine in EngineType
    }


def _finalize(kwargs: dict) -> dict:
    """Derive the host-side window variables exactly as production does.

    The window-clock resolution moved from a template prelude to
    ``FirmwareRenderContext.to_template_vars()`` (#118); every render now
    receives ``busy_loop_probe`` / ``window_timer`` / ``use_stimer_window``
    as inputs. Deriving them through the production resolver keeps these
    snapshots pinned to the values production ships, while the rendered-text
    guard tests below (window-timed-by-dead-domain, free-running-DWT) remain
    the independent check on the resolution itself.
    """
    return {
        **kwargs,
        **resolve_window_timer(
            clean_window_probe=str(kwargs.get("clean_window_probe", "infer")),
            power_only=bool(kwargs.get("power_only", False)),
            power_window_timer=str(kwargs["power_window_timer"]),
            clean_window_timer=str(kwargs["clean_window_timer"]),
        ),
    }


def _render(
    soc_name: str,
    transport: str,
    engine: str,
    power_only: bool = False,
    clean_window_probe: str = "infer",
    window_mode: str = "fixed",
    overrides: dict | None = None,
) -> str:
    """Render one production template for this (soc, transport, engine).

    ``overrides`` is applied last, after the engine-specific variables and
    before the window resolution, so a caller can flip one render input
    (a PSRAM placement, a burst flag, an INA228 block) without restating the
    engine branches below. The snapshot matrices pass none, so their renders
    are unaffected; ``test_wire_protocol.py`` uses it to reach the condition
    variants the wire census has to flip.
    """
    kwargs = _common_kwargs(soc_name, transport)

    def _resolve(kw: dict) -> dict:
        if overrides:
            kw.update(overrides)
        return _finalize(kw)

    kwargs["clean_window_probe"] = clean_window_probe
    kwargs["window_mode"] = window_mode
    # .get() with the production derivation as the fallback so an unknown
    # engine still reaches the informative AssertionError below.
    kwargs["engine_wire_name"] = _ENGINE_WIRE_NAMES.get(engine, engine.replace("-", "_"))
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
        return _jinja_env.get_template("main_aot.cc.j2").render(**_resolve(kwargs))
    if engine == "executorch":
        # Mirrors the executorch_* block of FirmwareRenderContext.to_template_vars()
        # (EngineContext), which is what production hands
        # main_executorch.cc.j2 in firmware/__init__.py.  Sizes are arbitrary
        # but distinct so a buffer swapped between two placements is visible in
        # the sha256.
        kwargs.update(
            printf_linkage="",
            executorch_planned_arena_size=65_536,
            executorch_method_arena_size=16_384,
            executorch_temporary_arena_size=8_192,
            executorch_input_size=1_960,
            executorch_output_size=12,
            executorch_planned_arena_region="tcm",
            executorch_method_arena_region="tcm",
            executorch_temporary_arena_region="tcm",
            executorch_io_region="tcm",
        )
        return _jinja_env.get_template("main_executorch.cc.j2").render(
            **_resolve(kwargs)
        )
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
    return _jinja_env.get_template("main.cc.j2").render(**_resolve(kwargs))


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
        if soc in _socs_for(engine)
    ]


# power_only variant matrix (WP1): dedicated power binary, no transport ever
# initialized.  Only rendered for "rtt" — power_only forces NSX_DEBUG_NONE
# regardless of the requested transport, so varying transport here would not
# exercise any additional code path (see main.cc.j2/main_aot.cc.j2 power_only
# guards).  Covers every SoC family x engine per the WP1 verification matrix.
_POWER_TRANSPORT = "rtt"


def _power_combos() -> list[tuple[str, str, str]]:
    return [
        (soc, _POWER_TRANSPORT, engine) for soc in _SOCS for engine in _MATRIX_ENGINES
    ]


# The matrices above pin clean_window_probe="infer" (the default).  The opt-in
# busy_loop diagnostic replaces the whole window body AND adds a calibration
# pass ahead of it, so it is a genuinely different render -- and it went
# unsnapshotted long enough for the calibration to end up reading a debug
# domain the same binary had already powered down (issue #112).
#
# Snapshotted for BOTH binaries.  The power binary is where a dead calibration
# clock is fatal, but #112 also moved the profile binary onto STIMER, and with
# only the power half pinned that change was guarded by nothing: reverting the
# whole `power_only=False` half of the fix -- 48 of the 96 moved renders --
# left the suite green.  The profile half uses the same rtt-only reduction as
# the power matrix; transport interacts with printf teardown, not with which
# clock times the window.
_POWER_BUSY_LOOP_PROBE = "busy_loop"
#: Every clean-window probe, for the invariant tests that must hold for all of
#: them (not just the default the snapshot matrix above pins).
_PROBES = ("infer", "busy_loop")
_BUSY_LOOP_TRANSPORT = "rtt"


def _power_busy_loop_combos() -> list[tuple[str, str, str]]:
    return _power_combos()


def _profile_busy_loop_combos() -> list[tuple[str, str, str]]:
    return [
        (soc, _BUSY_LOOP_TRANSPORT, engine)
        for soc in _SOCS
        for engine in _MATRIX_ENGINES
    ]


def _key(
    soc: str,
    transport: str,
    engine: str,
    power_only: bool = False,
    clean_window_probe: str = "infer",
) -> str:
    suffix = "|power" if power_only else ""
    if clean_window_probe != "infer":
        suffix += f"|{clean_window_probe}"
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
    result.update(
        {
            _key(
                soc,
                transport,
                engine,
                power_only=True,
                clean_window_probe=_POWER_BUSY_LOOP_PROBE,
            ): _digest(
                _render(
                    soc,
                    transport,
                    engine,
                    power_only=True,
                    clean_window_probe=_POWER_BUSY_LOOP_PROBE,
                )
            )
            for soc, transport, engine in _power_busy_loop_combos()
        }
    )
    result.update(
        {
            _key(
                soc,
                transport,
                engine,
                clean_window_probe=_POWER_BUSY_LOOP_PROBE,
            ): _digest(
                _render(
                    soc,
                    transport,
                    engine,
                    clean_window_probe=_POWER_BUSY_LOOP_PROBE,
                )
            )
            for soc, transport, engine in _profile_busy_loop_combos()
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


@pytest.mark.parametrize(
    "soc,transport,engine",
    _power_busy_loop_combos(),
    ids=[
        _key(*c, power_only=True, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
        for c in _power_busy_loop_combos()
    ],
)
def test_power_only_busy_loop_render_matches_snapshot(soc, transport, engine):
    """Dedicated power binary with the opt-in busy_loop clean-window probe.

    The busy_loop probe swaps the whole window body for a calibrated nop loop,
    so it takes template branches no ``infer`` render reaches — including a
    calibration pass that has to be timed by something.  Snapshotting it here
    is what makes {apollo3p, apollo4p} x power_only x busy_loop a reviewed
    render rather than an unexercised one (issue #112).
    """
    assert _SNAPSHOTS, (
        "no firmware render snapshot committed — generate it with "
        "HPX_UPDATE_SNAPSHOTS=1"
    )
    key = _key(
        soc, transport, engine, power_only=True, clean_window_probe=_POWER_BUSY_LOOP_PROBE
    )
    assert key in _SNAPSHOTS, f"{key} missing from snapshot. {_REGEN_HINT}"

    current = _digest(
        _render(
            soc,
            transport,
            engine,
            power_only=True,
            clean_window_probe=_POWER_BUSY_LOOP_PROBE,
        )
    )
    expected = _SNAPSHOTS[key]

    assert current["markers"] == expected["markers"], (
        f"[{key}] active feature blocks changed:\n"
        f"  expected: {expected['markers']}\n"
        f"  actual:   {current['markers']}\n{_REGEN_HINT}"
    )
    assert current["sha256"] == expected["sha256"], f"[{key}] render hash changed. {_REGEN_HINT}"


@pytest.mark.parametrize(
    "soc,transport,engine",
    _profile_busy_loop_combos(),
    ids=[
        _key(*c, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
        for c in _profile_busy_loop_combos()
    ],
)
def test_profile_busy_loop_render_matches_snapshot(soc, transport, engine):
    """Transport-attached profile binary with the busy_loop probe.

    #112 moved this half onto STIMER too, for uniformity rather than necessity
    (a profile binary keeps a debugger attached, so its DWT is readable). Until
    this matrix existed the entire ``power_only=False`` half of that change --
    48 of the 96 renders it moved -- could be reverted with the suite staying
    green.
    """
    assert _SNAPSHOTS, (
        "no firmware render snapshot committed — generate it with "
        "HPX_UPDATE_SNAPSHOTS=1"
    )
    key = _key(soc, transport, engine, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
    assert key in _SNAPSHOTS, f"{key} missing from snapshot. {_REGEN_HINT}"

    current = _digest(
        _render(soc, transport, engine, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
    )
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


def _code_only(src: str) -> str:
    """Drop ``//`` line comments so prose about DWT cannot satisfy a check."""
    import re

    return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())


def _clock_dependent_region(code: str) -> str:
    """The span of a render whose clock reads must survive a dead debug domain.

    Ends at the ``hpx_sync_window_end();`` CALL site.  Starts at whichever
    comes first:

    * the busy-loop probe's calibration pass (anchored on ``busy_calib_t0``),
      which is not inside the gated window but *decides how long it runs* --
      time it with a frozen clock and the tick delta reads 0, the scaling
      branch is skipped, and the iteration count keeps its hardcoded seed
      (issue #112); or
    * ``hpx_sync_window_begin();`` for every other probe.

    Anchor on CALL sites, not bare names: the no-op ``static inline``
    definitions appear earlier in the file (and an end() definition precedes
    the begin() call), so slicing on bare names yields an empty region and
    silently asserts nothing.
    """
    end = code.index("hpx_sync_window_end();", code.index("hpx_sync_window_begin();"))
    start = code.index("hpx_sync_window_begin();")
    if "busy_calib_t0" in code:
        start = min(start, code.index("busy_calib_t0"))
    return code[start:end]


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

    Scope: this pins the invariant across the *render matrix*, for every
    clean-window probe -- any future SoC that gains the broad shutdown must
    not also inherit a DWT-timed window. It is a textual check, keyed on the
    literal am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG) and
    DWT->CYCCNT spellings inside ``_clock_dependent_region``, so it does NOT
    defend against refactors that rename or wrap any of those (a DWT read
    behind a helper, a differently-spelled shutdown).  The region includes the
    busy-loop probe's calibration pass, which sits *before* window_begin but
    is the same bug class one region over (issue #112).
    The OTHER way the domain disappears on Cortex-M4F parts -- a free-running
    binary with no debugger asserting CDBGPWRUPREQ -- is pinned separately, by
    capability rather than by spelling, in
    ``test_free_running_power_binary_never_times_the_window_with_dwt``.
    """
    checked = 0
    for probe in _PROBES:
        for soc, transport, engine in _power_combos():
            code = _code_only(
                _render(
                    soc, transport, engine, power_only=True, clean_window_probe=probe
                )
            )
            if "am_hal_pwrctrl_periph_disable(AM_HAL_PWRCTRL_PERIPH_DEBUG)" not in code:
                continue
            checked += 1
            assert "DWT->CYCCNT" not in _clock_dependent_region(code), (
                f"{soc}|{transport}|{engine}|{probe}: window (or the calibration "
                "that sizes it) timed by DWT while the same binary disables the "
                "debug power domain DWT lives in"
            )
    assert checked, (
        "no power render disables the debug domain — this test lost its subject, "
        "so it is no longer pinning anything"
    )


def test_stimer_init_verifies_the_crystal_against_an_independent_clock():
    """The STIMER window clock must not be trusted before it has settled (#110).

    ``am_hal_stimer_config(AM_HAL_STIMER_CFG_CLEAR)`` drops the XT request and
    the crystal restarts -- Apollo4 datasheet 6.4, "the XT is only enabled when
    an internal module is using it". Reading it immediately gave 584/591/858
    kHz for a 96 MHz clock, a 45% spread across identical builds; a bench sweep
    (#124) put the cold transient at ~500-650 ms.

    Two properties matter, and both are asserted here rather than left to the
    comment:

    1. The init VERIFIES rather than assuming. A fixed delay was rejected -- it
       would tax every power run for a transient that usually does not happen,
       since the XT is normally already up by the time a window opens.
    2. It verifies against ``nsx_delay_us``, a calibrated BOOTROM cycle loop.
       That matters more than it looks: DWT is exactly the clock STIMER exists
       to replace on these paths, so verifying against it would be circular --
       the same mistake as deriving a stall floor from a possibly-stalled
       reference.
    """
    checked = 0
    for soc, transport, engine in _all_combos():
        for power_only in (False, True):
            if power_only and transport != _POWER_TRANSPORT:
                continue
            rendered = _render(soc, transport, engine, power_only=power_only)
            if "hpx_stimer_init(" not in rendered:
                continue
            checked += 1
            case = f"{soc}|{transport}|{engine}{'|power' if power_only else ''}"

            init = rendered[rendered.index("static inline void hpx_stimer_init(void)") :]
            init = init[: init.index("\n}")]

            # Review proved the first version of these assertions vacuous
            # against the exact design this PR rejects: replacing the whole
            # verify loop with a blind nsx_delay_us(1s) fixed delay passed
            # them all (they were substring checks for the constants). So
            # assert the MEASUREMENT, not the vocabulary: the init must read
            # the counter, compare against both band edges, and demand more
            # than one in-band probe.
            assert "HPX_STIMER_SETTLE_MAX_US" in init, (
                f"{case}: hpx_stimer_init() configures the XT and returns without "
                "waiting for it to settle; the first reads land in the restart "
                "transient (#110)"
            )
            assert init.count("am_hal_stimer_counter_get()") >= 2, (
                f"{case}: a settle that never reads the counter is a blind "
                "delay, the design this fix explicitly rejected -- it taxes "
                "every warm run and still cannot notice a dead crystal"
            )
            assert (
                "HPX_STIMER_SETTLE_MIN_TICKS" in init
                and "HPX_STIMER_SETTLE_MAX_TICKS" in init
            ), (
                f"{case}: the probe reading is not compared against the band, "
                "so any tick count -- including zero -- would count as settled"
            )
            assert "settle_in_band" in init and ">= 2U" in init, (
                f"{case}: single-probe acceptance has a bench-demonstrated "
                "blind band just above the ceiling (the 400 ms row's first "
                "reading implied ~+36% with the ceiling at +25%); consecutive "
                "agreement is the settled signature"
            )
            assert "nsx_delay_us" in init, (
                f"{case}: the settle check needs a reference the STIMER fault "
                "cannot affect; nsx_delay_us is the BOOTROM cycle loop"
            )
            assert "DWT->CYCCNT" not in init, (
                f"{case}: verifying STIMER against DWT is circular -- DWT is the "
                "clock STIMER replaces on exactly these paths"
            )

    assert checked, "no render exercised hpx_stimer_init(); this test lost its subject"


def test_no_render_reports_a_clean_cycles_it_never_accumulated():
    """``clean_cycles`` must never be reported without being assigned first.

    This is the single invariant that keeps a fabricated duration off the wire,
    and it spans two independent decisions in each template: whether
    ``uint64_t clean_cycles = 0;`` is DECLARED (``not use_stimer_window``) and
    whether the window body ever ASSIGNS it.  Get those out of step and the
    firmware emits HPX_CLEAN_INFER_TOTAL_CYCLES / AVG_CYCLES / AVG_US derived
    from a variable that is still 0 -- ``elapsed_us == 0`` with completed work,
    which is precisely ``firmware_window_clock_is_frozen()``'s signature.

    Why it needs its own test rather than riding on the snapshots: the busy_loop
    branch's assignment (``clean_cycles = clean_probe_target_cyc;``) was deleted
    in #112 because that path moved to STIMER, so the declaration is now the
    only thing standing between a reverted prelude and a zero duration.  A
    reviewer reverted the STIMER forcing in main_aot.cc.j2 ALONE -- the exact
    single-template drift shape filed as #118 -- and the whole suite stayed
    green while the AOT render did exactly this.

    Swept over every SoC x transport x engine x probe, power and profile, so
    neither template can drift alone and neither half of the probe matrix is
    unguarded.
    """
    import re

    checked = 0
    for probe in _PROBES:
        combos = [(soc, transport, engine, False) for soc, transport, engine in _all_combos()]
        combos += [(soc, transport, engine, True) for soc, transport, engine in _power_combos()]
        for soc, transport, engine, power_only in combos:
            code = _code_only(
                _render(
                    soc,
                    transport,
                    engine,
                    power_only=power_only,
                    clean_window_probe=probe,
                )
            )
            if "uint64_t clean_cycles = 0;" not in code:
                # STIMER path: the variable does not exist, so it cannot be
                # reported stale. Nothing to check.
                continue
            checked += 1
            begin = code.index("hpx_sync_window_begin();")
            window = code[begin : code.index("hpx_sync_window_end();", begin)]
            assert re.search(r"clean_cycles\s*(\+=|=)", window), (
                f"{soc}|{transport}|{engine}|"
                f"{'power' if power_only else 'profile'}|{probe}: declares "
                "clean_cycles and reports it, but never assigns it inside the "
                "measured window — elapsed_us would be a fabricated 0"
            )
    assert checked, (
        "no render declares clean_cycles — this test lost its subject, so it "
        "is no longer pinning anything"
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

    Checked for every clean-window probe, over the region that includes the
    busy-loop calibration pass: AP3 has no broad shutdown for the sibling test
    to key on, so this is the only test that sees a busy_loop calibration
    reading DWT on an Apollo3 power binary (issue #112).
    """
    checked = 0
    for probe in _PROBES:
        for soc, transport, engine in _power_combos():
            caps = get_soc(soc).capabilities
            if not caps.transport.requires_attached_probe_for_cycles:
                continue
            checked += 1
            code = _code_only(
                _render(
                    soc, transport, engine, power_only=True, clean_window_probe=probe
                )
            )
            assert "DWT->CYCCNT" not in _clock_dependent_region(code), (
                f"{soc}|{transport}|{engine}|{probe}: window (or the calibration "
                "that sizes it) timed by DWT in a free-running power binary, "
                "where nothing holds the core debug power domain up"
            )
    assert checked, (
        "no power render targets a probe-dependent-cycles family — this test "
        "lost its subject, so it is no longer pinning anything"
    )


def test_busy_loop_probe_reports_a_measured_duration_not_the_nominal_target():
    """The busy-loop probe must never report ``window_target_ms`` as if it had
    been measured.

    The probe sizes its nop loop from a calibration pass, so the loop's real
    duration is only ever approximately the target -- and is *unrelated* to it
    if the calibration clock was dead.  Assigning the target to ``clean_cycles``
    (what this template did before issue #112) made the terminal report's
    elapsed_us come out at exactly the nominal value no matter how long the
    window actually ran: a fabricated number nothing downstream can flag,
    unlike a merely wrong one.  The fix is that the STIMER bracket around the
    window is the only source of the duration.

    Swept over the power AND profile binaries: the fabrication was identical in
    both, and pinning only the power half left the profile half revertible with
    the suite staying green.
    """
    checked = 0
    combos = [(soc, t, e, True) for soc, t, e in _power_busy_loop_combos()]
    combos += [(soc, t, e, False) for soc, t, e in _profile_busy_loop_combos()]
    for soc, transport, engine, power_only in combos:
        label = f"{soc}|{transport}|{engine}|{'power' if power_only else 'profile'}"
        code = _code_only(
            _render(
                soc,
                transport,
                engine,
                power_only=power_only,
                clean_window_probe=_POWER_BUSY_LOOP_PROBE,
            )
        )
        checked += 1
        assert "busy_loop_iters" in code, (
            f"{label}: busy_loop probe did not render — this test lost its subject"
        )
        # The target may still be COMPUTED (it sizes the loop); what it must
        # never be is assigned into the reported cycle/duration accumulator.
        assert "clean_cycles = clean_probe_target" not in code, (
            f"{label}: busy_loop reports the nominal window target as the "
            "measured duration"
        )
        assert "clean_stimer_total_us" in code, (
            f"{label}: busy_loop window has no measured duration source"
        )
    assert checked, "busy_loop matrix is empty"


def test_busy_loop_terminal_report_requests_one_unit_not_the_inference_count():
    """Firmware's terminal counts must be self-consistent for this probe.

    ``requested_count``/``completed_count`` are units of work the window
    performed.  The busy_loop probe performs exactly one -- a calibrated spin,
    not N inferences -- and the window body sets ``clean_count = 1``, so the
    requested side must be 1 too.

    Rendering ``clean_iters_n`` there is what shipped before: N requested
    against 1 completed, with status "complete".  collect_power_terminal.py
    reads that as "Power firmware reported incomplete inference execution" and
    raises, so no busy_loop run could finish on any board and elapsed_us -- the
    number this probe exists to produce -- was never consumed.

    Pinned semantically here as well as byte-wise in the snapshots, so a
    regression has to argue with a named invariant rather than just regenerate
    a hash.  The host half is
    ``power.diagnostics.expected_terminal_requested_count`` and is tested in
    tests/test_collect_power_terminal.py.
    """
    # Anchor on the CALL, not the name: hpx_power_terminal_report( appears
    # first as the function DEFINITION (whose parameter list contains neither
    # spelling), so slicing on the bare name asserts against the signature and
    # passes or fails for reasons unrelated to the render. The call is the only
    # occurrence followed by the literal `true,` first argument.
    call_anchor = "hpx_power_terminal_report(\n    true,"

    def _report_args(code: str) -> str:
        body = code[code.index(call_anchor) :]
        return body[: body.index(");")]

    checked = 0
    for soc, transport, engine in _power_busy_loop_combos():
        code = _code_only(
            _render(
                soc,
                transport,
                engine,
                power_only=True,
                clean_window_probe=_POWER_BUSY_LOOP_PROBE,
            )
        )
        label = f"{soc}|{transport}|{engine}"
        report = _report_args(code)
        checked += 1
        assert "clean_iters_n" not in report, (
            f"{label}: busy_loop terminal report requests clean_iters_n while "
            "the window completes 1 — the host rejects this run as incomplete"
        )
        assert "1U," in report, f"{label}: busy_loop terminal report requests no unit count"
        # The completed side is unchanged and still comes from the window.
        assert "clean_count," in report, f"{label}: terminal report lost completed_count"

    # The default probe must keep reporting the planned N.
    for soc, transport, engine in _power_combos():
        code = _code_only(_render(soc, transport, engine, power_only=True))
        report = _report_args(code)
        assert "clean_iters_n" in report, (
            f"{soc}|{transport}|{engine}: infer probe stopped reporting the "
            "planned inference count"
        )
    assert checked, "busy_loop power matrix is empty"


def _clean_window_region(code: str) -> str:
    """Code between the clean-window scope opening and the window closing.

    Anchored on statements present in BOTH the fixed and unfixed renders
    (``g_profiler...= false`` / ``hpx_sync_window_end();``) so slicing does not
    depend on the change under test -- the failure mode #120 documented, where
    a slice keyed on new spelling silently matched nothing.

    The opener is searched BACKWARDS from ``hpx_sync_window_begin();``: the AOT
    template's ``g_profiler_enabled = false;`` also appears as a file-scope
    initializer hundreds of lines earlier, and anchoring on that would silently
    widen the region to most of the file (and swallow unrelated DWT reads).

    One opener spelling per engine, since each names its own profiler-arm flag
    (heliaRT ``g_profiler.SetEnabled``, heliaAOT ``g_profiler_enabled``,
    ExecuTorch ``g_profile_enabled``).  All three render from the same
    ``engine_profiler_off`` seam in ``_main_base.cc.j2``, so the list is
    exhaustive by construction: a fourth engine adds a fourth spelling here.
    """
    gate = code.index("hpx_sync_window_begin();")
    starts = [
        found
        for opener in (
            "g_profiler.SetEnabled(false);",
            "g_profiler_enabled = false;",
            "g_profile_enabled = false;",
        )
        if (found := code.rfind(opener, 0, gate)) != -1
    ]
    assert starts, "clean-window scope opener not found before the window gate"
    end = code.index("hpx_sync_window_end();", gate)
    return code[max(starts) : end]


def test_dwt_timed_clean_window_waits_for_the_host_attach():
    """A DWT-timed clean window must not open before the host probe attaches.

    On the Cortex-M4F families DWT->CYCCNT only advances while a debugger
    asserts CDBGPWRUPREQ, and the host does not hold the probe continuously:
    the J-Link reset is a separate JLinkExe subprocess, and nothing holds the
    debug domain up between that process exiting and the pylink attach
    completing. Per-iteration deltas taken across that gap read exactly zero,
    so the window comes back SHORT -- 21% low on two of five Apollo4 Blue Plus
    KBR runs in #121, while the later profiled loop (host fully attached) held
    at 875-876 us in all five.

    The fix is sequencing: wait for the RTT up-buffer to drain, which the host
    can only do with the DAP alive, before touching the counter. This pins that
    the drain precedes the FIRST DWT read of the window -- covering the
    adaptive sizing warmup as well as the measured loop -- and that it is
    scoped to renders that actually need it, so no STIMER-timed or
    probe-independent build inherits a pointless wait.
    """
    import re

    def _code_only(src: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

    waited = 0
    for soc, transport, engine in _all_combos():
        for window_mode in ("fixed", "auto"):
            caps = get_soc(soc).capabilities
            rendered = _render(soc, transport, engine, window_mode=window_mode)
            region = _clean_window_region(_code_only(rendered))
            case = f"{soc}|{transport}|{engine}|{window_mode}"
            needs_wait = caps.clean_window_needs_probe_attach and transport == "rtt"
            if not needs_wait:
                assert "HPX_CLEAN_WINDOW_ATTACH_WAIT_MS" not in rendered, (
                    f"{case}: waits for a probe attach it does not depend on "
                    f"(window timer {caps.clock.clean_window_timer!r}, "
                    f"transport {transport!r})"
                )
                continue
            waited += 1
            drain = region.find("HPX_CLEAN_WINDOW_ATTACH_WAIT_MS * 1000U")
            assert drain != -1, (
                f"{case}: DWT-timed clean window opens without waiting for the "
                "host attach that keeps DWT running"
            )
            first_dwt = region.find("DWT->CYCCNT")
            assert first_dwt != -1, (
                f"{case}: expected a DWT-timed window here; the render no "
                "longer reads DWT->CYCCNT at all, so this test has lost its "
                "subject"
            )
            assert drain < first_dwt, (
                f"{case}: the clean window reads DWT->CYCCNT before waiting "
                "for the host attach, so the reads can still land in the "
                "probe-absence gap. In auto mode the sizing warmup is the "
                "first such read, and it is inside the window this covers."
            )

    assert waited, (
        "no render has a probe-dependent DWT-timed clean window — this test "
        "lost its subject, so it is no longer pinning anything"
    )


def test_dwt_timed_clean_window_counts_stalled_iterations():
    """Every per-iteration DWT-timed clean window must report zero-cycle
    iterations, on every transport.

    The attach wait above needs an observable host-attach signal and so only
    covers RTT; SWO happens to be covered by its ~800 ms sync preamble, and
    UART/USB are covered by nothing. This detector is the part that applies
    everywhere: DWT does not run slow when the debug domain drops, it STOPS, so
    an iteration wholly inside a stall reads a delta of exactly zero -- and an
    inference cannot take zero core cycles. Counting those turns a future
    regression into ``profile.clean_window_stalled`` instead of a plausible
    average.

    Pinned inside the measured window slice, so moving the counter outside the
    loop (where it could never observe a stall) fails here.
    """
    import re

    def _code_only(src: str) -> str:
        return "\n".join(re.sub(r"//.*$", "", line) for line in src.splitlines())

    checked = 0
    for soc, transport, engine in _all_combos():
        if get_soc(soc).capabilities.clock.clean_window_timer != "dwt":
            continue
        for window_mode in ("fixed", "auto"):
            checked += 1
            case = f"{soc}|{transport}|{engine}|{window_mode}"
            rendered = _render(soc, transport, engine, window_mode=window_mode)
            code = _code_only(rendered)
            begin = code.index("hpx_sync_window_begin();")
            window = code[begin : code.index("hpx_sync_window_end();", begin)]
            assert "clean_stalled_iters++" in window, (
                f"{case}: the DWT-timed clean loop does not detect a frozen "
                "cycle counter"
            )
            # The partial check is the one that matters for the shape the
            # exact-zero test cannot see: a counter that keeps advancing at a
            # fraction of the true rate produces small non-zero deltas, which
            # would otherwise accumulate uncounted and let the run report
            # itself healthy while still being wrong.
            assert "clean_partial_iters++" in window, (
                f"{case}: the DWT-timed clean loop detects a frozen counter "
                "but not one that merely runs implausibly slow"
            )
            assert "clean_low_cyc" in window, (
                f"{case}: the partial check has no floor to compare against"
            )
            for line in ("HPX_CLEAN_STALLED_ITERS", "HPX_CLEAN_PARTIAL_ITERS",
                         "HPX_CLEAN_REF_CYCLES"):
                assert line in rendered, (
                    f"{case}: {line} is computed but never reported, so the "
                    "host cannot act on it"
                )

    assert checked, (
        "no render times its clean window with DWT — this test lost its "
        "subject, so it is no longer pinning anything"
    )


def test_partial_floor_comes_from_the_lowest_warm_sample_not_the_highest():
    """The floor must not be derivable from a single cold-cache warm sample.

    ``clean_warm_cyc`` is a MAX, chosen so a warm sample that itself lost time
    to a stall cannot drag the window sizing down. That makes it the wrong
    reference for the partial-stall floor: one cold sample inflates it, the
    floor rises with it, and every healthy iteration in the window is marked
    partial (a 9x sample marks all 1091). The floor therefore comes from the
    lowest NON-ZERO warm sample -- conservative in the only safe direction,
    since it can under-report partials but never invent them -- and skipping
    zeros keeps a frozen warm pass from silently zeroing the floor.

    Pinned semantically rather than left to the snapshot sha256, which changes
    for any edit and so says nothing about which reference was used.
    """
    checked = 0
    for soc, transport, engine in _all_combos():
        if get_soc(soc).capabilities.clock.clean_window_timer != "dwt":
            continue
        for window_mode in ("fixed", "auto"):
            checked += 1
            case = f"{soc}|{transport}|{engine}|{window_mode}"
            code = _code_only(_render(soc, transport, engine, window_mode=window_mode))
            region = _clean_window_region(code)

            assert "clean_low_cyc = clean_warm_min_cyc" in region, (
                f"{case}: the partial floor is derived from the MAX warm "
                "sample, so one cold-cache warmup marks a whole healthy "
                "window partial"
            )
            # The min tracker must skip zero samples, or a frozen warm pass
            # zeroes the floor and disables the check it is meant to arm.
            assert "wc > 0U" in region, (
                f"{case}: the warm minimum does not skip zero samples, so a "
                "frozen warmup silently disables the partial check"
            )
            # And the check itself must not fire when the floor is dead.
            assert "clean_low_cyc > 0U" in region, (
                f"{case}: the partial check does not guard against a zero "
                "floor"
            )

    assert checked, (
        "no render times its clean window with DWT -- this test lost its "
        "subject, so it is no longer pinning anything"
    )


def test_dwt_rate_probe_uses_a_clock_that_cannot_share_the_fault():
    """Every DWT-timed clean window must calibrate DWT before trusting it.

    The in-window counters are DWT-relative: the partial floor is a warm sample
    taken with the same counter in the same fault window, so a uniform slowdown
    scales both sides and cancels exactly. The probe is the only check with an
    outside reference -- ``nsx_delay_us`` resolves to the BOOTROM cycle loop,
    which touches no DWT, no CoreSight register and no debug power domain.

    Rendered on every transport, not just the ones with an attach signal: UART
    and USB CDC have no host-drain signal to wait on, so this is all they get.
    """
    checked = 0
    for soc, transport, engine in _all_combos():
        if get_soc(soc).capabilities.clock.clean_window_timer != "dwt":
            continue
        checked += 1
        case = f"{soc}|{transport}|{engine}"
        rendered = _render(soc, transport, engine)
        region = _clean_window_region(_code_only(rendered))

        probe = region.find("nsx_delay_us(HPX_CLEAN_DWT_RATE_PROBE_US)")
        assert probe != -1, (
            f"{case}: the clean window trusts DWT without ever calibrating it "
            "against an independent clock"
        )
        window_begin = region.find("hpx_sync_window_begin();")
        if window_begin != -1:
            assert probe < window_begin, (
                f"{case}: the rate probe runs inside the measured window"
            )
        for line in ("HPX_CLEAN_DWT_RATE_CYC", "HPX_CLEAN_DWT_RATE_US"):
            assert line in rendered, (
                f"{case}: {line} is measured but never reported, so the host "
                "cannot act on it"
            )

    assert checked, (
        "no render times its clean window with DWT -- this test lost its "
        "subject, so it is no longer pinning anything"
    )


def test_attach_wait_budget_fits_the_power_capture_boot_allowance():
    """The attach wait is reachable-to-timeout on a supported path, so its
    stated budget has to be both small and honest.

    With ``power.firmware: shared`` the profile binary is re-run as the gated
    power window, and that pass free-runs -- ``capture_power`` drives only the
    instrument and the reset, and ``capture_pmu`` has already closed its pylink
    session. Nothing reads RTT, so this poll ALWAYS runs to timeout, and it
    lands after ``capture_gated``'s ``on_started`` hook returns, which charges
    it against the GPI poller's boot allowance. Overrun that and the poller
    stops before or mid-window, reported as ``no_gate_rise``.

    Two things are pinned, because the budget is only as good as the clock
    enforcing it:

    * the nominal budget is a small fraction of ``_BOOT_SETTLE_S``, read from
      the host module rather than restated, so the two cannot drift apart;
    * the loop paces itself with ``nsx_delay_us`` (calibrated) rather than
      ``hpx_rtt_drain`` (whose spin assumes 2 cycles per iteration where a
      Cortex-M4 spends 6-9, so a drain that times out takes 3-4x its stated
      budget -- harmless on the park/failure paths it was written for, not
      here).
    """
    import re

    from helia_profiler.stages.capture_power import _BOOT_SETTLE_S

    rendered = _render("apollo4p", "rtt", "tflm")
    match = re.search(
        r"#define\s+HPX_CLEAN_WINDOW_ATTACH_WAIT_MS\s+(\d+)U", rendered
    )
    assert match, "attach wait renders no nominal budget to check"
    budget_s = int(match.group(1)) / 1000.0

    assert 0 < budget_s <= _BOOT_SETTLE_S / 4, (
        f"attach wait budget {budget_s:.3f}s is too large a share of the "
        f"{_BOOT_SETTLE_S:.1f}s host boot allowance it is charged against on a "
        "free-running shared-firmware power pass"
    )

    region = _clean_window_region(rendered)
    assert "nsx_delay_us(HPX_CLEAN_WINDOW_ATTACH_POLL_US)" in region, (
        "the attach wait must pace itself with a calibrated delay, or its "
        "budget does not bound anything"
    )
    assert "hpx_rtt_drain(" not in region, (
        "the attach wait must not use hpx_rtt_drain's uncalibrated spin: it "
        "overruns its stated budget by 3-4x, which the free-running "
        "shared-firmware pass pays in full"
    )


def test_hal_umbrella_header_is_included_at_most_once():
    """``am_mcu_apollo.h`` has several independent consumers in the main
    templates (Apollo3 burst, the Armv8-M PMU, STIMER window timing, the broad
    peripheral / crypto-otp shutdowns) guarded by separate blocks. They must
    stay mutually exclusive: the shared skeleton (_main_base.cc.j2, extended
    by both main.cc.j2 and main_aot.cc.j2) keeps burst and the Armv8-M PMU as
    their own guards and merges the STIMER and shutdown cases into a third.
    This pins the invariant so the guards cannot silently drift into a double
    include -- or, as the AOT template did before the merged guard existed,
    into no include at all for a render that calls am_hal_stimer_*.

    Swept over both clean-window probes: the busy_loop probe is pinned to
    STIMER on every family (a deliberate simplification -- an AP3/AP4 profile
    binary could still read DWT; see main.cc.j2's prelude), which pulls
    am_hal_stimer_* into AP3/AP4 *profile* renders that the infer probe leaves
    on DWT.
    """
    for probe in _PROBES:
        for soc, transport, engine in _all_combos():
            rendered = _render(soc, transport, engine, clean_window_probe=probe)
            assert rendered.count('#include "am_mcu_apollo.h"') <= 1, (
                soc,
                transport,
                engine,
                probe,
            )
            if "am_hal_stimer_" in rendered:
                assert '#include "am_mcu_apollo.h"' in rendered, (
                    f"{soc}|{transport}|{engine}|{probe}: calls am_hal_stimer_* "
                    "with no AmbiqSuite HAL umbrella header in scope"
                )
        for soc, transport, engine in _power_combos():
            rendered = _render(
                soc, transport, engine, power_only=True, clean_window_probe=probe
            )
            assert rendered.count('#include "am_mcu_apollo.h"') <= 1, (
                soc,
                transport,
                engine,
                probe,
            )
            if "am_hal_stimer_" in rendered:
                assert '#include "am_mcu_apollo.h"' in rendered, (
                    f"{soc}|{transport}|{engine}|{probe}: calls am_hal_stimer_* "
                    "with no AmbiqSuite HAL umbrella header in scope"
                )


def test_snapshot_covers_exactly_the_current_matrix():
    """The committed snapshot must match the code's supported matrix exactly."""
    expected_keys = (
        {_key(*c) for c in _all_combos()}
        | {_key(*c, power_only=True) for c in _power_combos()}
        | {
            _key(*c, power_only=True, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
            for c in _power_busy_loop_combos()
        }
        | {
            _key(*c, clean_window_probe=_POWER_BUSY_LOOP_PROBE)
            for c in _profile_busy_loop_combos()
        }
    )
    assert set(_SNAPSHOTS) == expected_keys, _REGEN_HINT


def test_every_render_declares_its_engine_on_the_wire():
    """``HPX_ENGINE=<wire name>`` must be right for every engine, everywhere.

    The host reads this key to label the run (and to populate the
    ``ComparisonDimension.ENGINE`` field), and the shared skeleton emits it
    from a single ``engine_wire_name`` variable -- so a wrong or missing
    mapping is one edit away from mislabelling an entire engine's results
    while every other assertion in this file stays green. main.cc.j2 is
    rendered for BOTH tflm and helia-rt, which is exactly the case a
    template-side hardcoding would get wrong.

    The power binaries are the negative half: they force
    ``NSX_DEBUG_NONE`` and emit no HPX_* header at all, so ``HPX_ENGINE=``
    must be absent rather than merely unread.
    """
    for probe in _PROBES:
        for soc, transport, engine in _all_combos():
            rendered = _render(soc, transport, engine, clean_window_probe=probe)
            expected = _ENGINE_WIRE_NAMES[engine]
            case = f"{soc}|{transport}|{engine}|{probe}"
            assert f'hpx_printf("HPX_ENGINE={expected}\\n")' in rendered, (
                f"{case}: render does not declare HPX_ENGINE={expected}"
            )
            # Exactly one engine name on the wire, and it is this engine's.
            for other in set(_ENGINE_WIRE_NAMES.values()) - {expected}:
                assert f"HPX_ENGINE={other}\\n" not in rendered, (
                    f"{case}: also declares itself as {other}"
                )

        for soc, transport, engine in _power_combos():
            rendered = _render(
                soc, transport, engine, power_only=True, clean_window_probe=probe
            )
            assert "HPX_ENGINE=" not in rendered, (
                f"{soc}|{transport}|{engine}|{probe}|power: the dedicated power "
                "binary emits an HPX_ENGINE line, but it never brings a "
                "transport up -- nothing can read it"
            )


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

    power_rendered = _jinja_env.get_template("main.cc.j2").render(**_finalize({**kwargs, "power_only": True}))
    assert "bleResetCfg" in power_rendered
    assert "NSX_GPIO_LEVEL_LOW" in power_rendered
    assert "nsx_gpio.h" in power_rendered

    transport_rendered = _jinja_env.get_template("main.cc.j2").render(
        **_finalize({**kwargs, "power_only": False})
    )
    assert "bleResetCfg" not in transport_rendered

    # A board with no Cooper radio (ble_reset_gpio_pin unset) never emits it,
    # even in the power_only binary.
    no_ble_kwargs = dict(kwargs)
    no_ble_kwargs.pop("ble_reset_gpio_pin")
    no_ble_rendered = _jinja_env.get_template("main.cc.j2").render(
        **_finalize({**no_ble_kwargs, "power_only": True})
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
    power_rendered = _jinja_env.get_template("main.cc.j2").render(**_finalize({**kwargs, "power_only": True}))
    assert "AM_HAL_PWRCTRL_NVM0_ONLY" in power_rendered
    assert "EXTREME MODE" in power_rendered

    transport_rendered = _jinja_env.get_template("main.cc.j2").render(
        **_finalize({**kwargs, "power_only": False})
    )
    assert "AM_HAL_PWRCTRL_NVM0_ONLY" not in transport_rendered
    assert "EXTREME MODE" not in transport_rendered

    # Still requires TCM/TCM even in the power_only binary.
    non_tcm_kwargs = {**kwargs, "arena_region": "sram", "weights_region": "mram"}
    non_tcm_rendered = _jinja_env.get_template("main.cc.j2").render(
        **_finalize({**non_tcm_kwargs, "power_only": True})
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

    # main_aot.cc.j2's per-layer storage (g_layers, NSX_MEM_SRAM_BSS -- POD,
    # so NOLOAD zero-fill is safe, unlike the polymorphic g_profiler) follows
    # the same rule: SRAM-resident in the transport binary only, with the
    # SSRAM domain powered on AP5. Regression pin: the AOT template used to
    # leave pmu_profiler_sram_resident unset, so an AP5 render with a
    # TCM-resident arena never powered the domain and per-layer profiling
    # wrote to an unbacked SSRAM bank.
    aot_transport = _render("apollo510", "rtt", "helia-aot", power_only=False)
    assert "NSX_MEM_SRAM_BSS static HpxLayerRecord g_layers[kMaxLayers];" in aot_transport
    assert "Shared SSRAM power-on" in aot_transport

    aot_power = _render("apollo510", "rtt", "helia-aot", power_only=True)
    assert "NSX_MEM_SRAM_BSS static HpxLayerRecord g_layers" not in aot_power
    assert "static HpxLayerRecord g_layers[kMaxLayers];" in aot_power
    assert "Shared SSRAM power-on" not in aot_power

    # main_executorch.cc.j2's g_layers is the third instance of the same rule.
    # It has only a transport half to check -- ExecuTorch has no dedicated
    # power binary (preflight rejects engine.type=executorch with power, see
    # test_executorch_power_tripwire.py), so there is no power render to
    # assert the negative against and none is attempted here.
    #
    # Regression pin for the same bug shape the AOT lines above pin: the flag
    # that drives this (pmu_profiler_sram_resident) is set once by
    # _main_base.cc.j2, and ExecuTorch is the child that used to set it itself.
    et_transport = _render("apollo510", "rtt", "executorch", power_only=False)
    assert "NSX_MEM_SRAM_BSS static LayerRecord g_layers[kMaxLayers];" in et_transport
    assert "Shared SSRAM power-on" in et_transport


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
    ap330_rendered = _jinja_env.get_template("main.cc.j2").render(**_finalize(kwargs))
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
    rendered = _jinja_env.get_template("main.cc.j2").render(**_finalize(kwargs))
    assert "AM_HAL_PWRCTRL_PERIPH_IOM0" in rendered  # rest of the block still fires
    assert "AM_HAL_PWRCTRL_PERIPH_MSPI0" not in rendered
