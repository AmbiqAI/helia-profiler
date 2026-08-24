"""Contract: the rendered firmware emits exactly what the wire registry says.

``helia_profiler.wire`` is the single declaration of the HPX protocol. This
file is the proof that the declaration matches the firmware, by rendering the
production templates across a matrix that flips every declarative condition in
:data:`WIRE_CONDITIONS` at least once each way and comparing the tokens that
appear against the tokens the registry predicts — in *both* directions:

* **No undeclared emission.** Every ``HPX_`` token inside a C string literal of
  a render has a spec. (Tokens outside string literals are ``#define`` names —
  ``HPX_STIMER_HZ``, ``HPX_CLEAN_DCACHE`` and friends — which are firmware
  internals, never wire tokens; :func:`test_no_macro_name_ever_reaches_a_string`
  keeps the two namespaces from crossing.)
* **No missing emission.** For every spec, presence matches its declarative
  condition exactly, per render. A template that stops emitting
  ``HPX_ENGINE``, or a new engine that never learns to, fails here.
* **No dead spec.** Every spec fires somewhere in the matrix, so a token
  deleted from the templates cannot linger in the registry as documentation of
  something that no longer exists.

Plus literal catalogue pins (error codes, heartbeat phases, power-terminal key
sets, the CSV header shape) in the style of #154 Phase 3, and the grep-guard
that keeps bare ``HPX_`` protocol literals out of ``src/``.

Stated limit of the census: every assertion here is *set-valued* per render —
"this render can print this token", never "this render prints it from these N
sites". A token with more than one emission site is therefore only protected
against losing *all* of them; delete one site and the set is unchanged. The
known case is ``HPX_ERROR=unsupported_op``, which the TFLM template prints from
a custom-op and a builtin-op site with different payload fields, and the
per-operator ``HPX_HEARTBEAT phase=infer``, which has three sites across three
engines. Where losing one site matters, it is pinned by hand
(:func:`test_tflm_init_heartbeat_keeps_its_t0_payload` and the CSV row-format
pins are that shape).
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

from helia_profiler.capture import _ERROR_HINTS
from helia_profiler.engines import EngineType
from helia_profiler.firmware import _jinja_env
from helia_profiler.wire import (
    EST_MS_GAP,
    HPX_ITER_SENTINEL_PATTERN,
    HPX_PRESET_SENTINEL_PATTERN,
    KEY_VALUE_PATTERN,
    POWER_BINARY_ENGINES,
    POWER_TERMINAL_OPTIONAL_KEYS,
    POWER_TERMINAL_REQUIRED_KEYS,
    WIRE_CONDITIONS,
    WIRE_REGISTRY,
    FirmwareErrorCode,
    FirmwareWarnCode,
    HeartbeatPhase,
    PowerTerminalKey,
    WireBinary,
    WireDirection,
    WireKey,
    WireKind,
    error_token,
    heartbeat_token,
    spec_for,
    warn_token,
)

from .test_firmware_render_snapshots import _common_kwargs, _finalize, _render

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "helia_profiler"


# ---------------------------------------------------------------------------
# Extraction: HPX_ tokens that live inside C string literals
# ---------------------------------------------------------------------------

#: A token as it appears in a printf format: upper-case identifier characters,
#: plus the ``%d`` conversions heliaAOT embeds in its per-index key names.
_TOKEN_RE = re.compile(r"HPX_(?:[A-Z0-9_]|%[a-z])+")
_HEARTBEAT_RE = re.compile(r"HPX_HEARTBEAT phase=([a-z_]+)")
_ERROR_RE = re.compile(r"HPX_ERROR=([a-z0-9_]+)")
_WARN_RE = re.compile(r"HPX_WARN=([a-z0-9_]+)")


def _split_c(text: str) -> tuple[list[str], str]:
    """Split rendered C into (string literals, code with neither strings nor comments).

    Scanned rather than regexed because the alternative — grepping the whole
    render — cannot tell a wire token from a ``#define`` name, and because the
    templates are full of comments that name wire tokens in prose (one even
    quotes ``"HPX_GO"``, which the firmware never puts on any wire: it counts
    six received characters instead).

    Adjacent literals separated only by whitespace are *concatenated*, because
    C concatenates them: ``"HPX_POWER_" "ELAPSED_US=%llu\\n"`` is one format
    string at runtime and has to be one string here, or a token split across
    the join reaches the wire while the census sees two harmless fragments.
    The templates are full of the shape — an INA228 power render joins twenty
    adjacent literals, and the whole terminal record (eleven wire lines, both
    envelope markers included) is a single such concatenation — so this is the
    normal case rather than a corner.
    """
    literals: list[str] = []
    code: list[str] = []
    pending: list[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif char == "/" and text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif char == "'":
            i += 1
            while i < n and text[i] != "'":
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif char == '"':
            i += 1
            start = i
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            pending.append(text[start:i])
            i += 1
            # Only whitespace may separate two halves of one C string literal;
            # anything else (a comma, an operator, an identifier) ends it.
            ahead = i
            while ahead < n and text[ahead] in " \t\r\n":
                ahead += 1
            if ahead < n and text[ahead] == '"':
                i = ahead
                continue
            literals.append("".join(pending))
            pending = []
        else:
            code.append(char)
            i += 1
    if pending:  # pragma: no cover - unterminated literal in a render
        literals.append("".join(pending))
    return literals, "".join(code)


def _string_literals(text: str) -> list[str]:
    return _split_c(text)[0]


def _emitted_tokens(text: str) -> set[str]:
    """Registry keys for every wire token *text* can print.

    Heartbeat, error and warning lines resolve to their phase/code-qualified
    registry key; everything else is the bare token. A bare ``HPX_ERROR`` with
    no recognisable code stays bare on purpose, so it surfaces as an
    undeclared emission rather than being silently absorbed.

    The absorption is decided **per position**, not per literal: one C literal
    routinely carries several tokens (and, since adjacent literals are joined,
    now carries whole records), so "some code matched somewhere in this string"
    is not evidence that *this* ``HPX_ERROR`` occurrence had one. Suppressing
    per literal hid a codeless ``HPX_ERROR=%s`` sharing a format string with a
    concrete one.
    """
    tokens: set[str] = set()
    for literal in _string_literals(text):
        qualified: dict[int, str] = {}
        for pattern, shape in (
            (_HEARTBEAT_RE, "HPX_HEARTBEAT phase={}"),
            (_ERROR_RE, "HPX_ERROR={}"),
            (_WARN_RE, "HPX_WARN={}"),
        ):
            for match in pattern.finditer(literal):
                qualified[match.start()] = shape.format(match.group(1))
        tokens |= set(qualified.values())
        for match in _TOKEN_RE.finditer(literal):
            # The qualified match starts exactly where the bare token does, so
            # a same-position hit means this occurrence *is* the qualified one.
            if match.start() in qualified:
                continue
            tokens.add(match.group(0))
    return tokens


def _bare_tokens(text: str) -> set[str]:
    """``HPX_`` identifiers in the code itself — the ``#define`` namespace."""
    return set(_TOKEN_RE.findall(_split_c(text)[1]))


# ---------------------------------------------------------------------------
# The render matrix
# ---------------------------------------------------------------------------

_SOCS = ("apollo3p", "apollo4p", "apollo510")
_TRANSPORTS = ("rtt", "usb_cdc", "swo", "uart")
_ENGINES = ("tflm", "helia-rt", "helia-aot", "executorch")
_POWER_ENGINES = ("tflm", "helia-rt", "helia-aot")

#: ExecuTorch is Cortex-M55 only and has no power binary (preflight rejects
#: engine.type=executorch with power.enabled), exactly as in the render
#: snapshot matrix.
_ENGINE_SOCS = {"executorch": ("apollo510",)}

#: Render inputs for an on-target INA228, mirroring what
#: ``PowerMonitorContext.from_config`` produces for a 2 mOhm / 500 mA shunt.
_INA228_VARS: dict[str, object] = {
    "power_monitor": "ina228",
    "ina228_required": True,
    "ina228_i2c_iom": 1,
    "ina228_i2c_address": 0x40,
    "ina228_i2c_speed_hz": 400_000,
    "ina228_shunt_micro_ohms": 2_000_000,
    "ina228_max_current_ma": 500,
    "ina228_conversion_time_us": 540,
    "ina228_averaging_count": 16,
    "ina228_adc_range": 0,
    "ina228_shunt_cal": 6250,
    "ina228_current_lsb_divisor": "13107200000.0",
    "ina228_calibration_id": "ina228:r2000000uohm:i500ma:adc0",
}

_TCM_REGION = {
    "region_id": 0,
    "placement": "tcm",
    "alignment": 64,
    "size": 4096,
    "blob_filename": None,
}
_TCM_BLOB_REGION = {**_TCM_REGION, "region_id": 1, "blob_filename": "weights.bin"}
_PSRAM_BLOB_REGION = {
    "region_id": 2,
    "placement": "psram",
    "alignment": 64,
    "size": 8192,
    "blob_filename": "psram_weights.bin",
}
#: A PSRAM arena region with NO sidecar blob. Without it every PSRAM-placed
#: region in the matrix also carried a blob, which made
#: ``GATE_AOT_PSRAM_ARENAS`` and ``GATE_AOT_CONST_BLOBS_IN_PSRAM`` true on
#: exactly the same renders: swapping the two conditions between specs left the
#: census green.
_PSRAM_REGION = {**_PSRAM_BLOB_REGION, "region_id": 3, "blob_filename": None}


class _Render:
    """One rendered firmware app plus the render inputs its gates read."""

    def __init__(
        self,
        label: str,
        soc: str,
        transport: str,
        engine: str,
        *,
        power_only: bool = False,
        clean_window_probe: str = "infer",
        window_mode: str = "fixed",
        overrides: dict | None = None,
    ) -> None:
        self.label = label
        self.engine = EngineType(engine)
        self.text = _render(
            soc,
            transport,
            engine,
            power_only=power_only,
            clean_window_probe=clean_window_probe,
            window_mode=window_mode,
            overrides=overrides,
        )
        if engine in ("tflm", "helia-rt"):
            # A TFLM/heliaRT app is two translation units, and both binaries
            # link both of them (see CMakeLists.txt.j2): the per-operator
            # heartbeat lives in the profiler class, not in main.cc.
            self.text += _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
                profiling_backends=list(_common_kwargs(soc, transport)["profiling_backends"]),
                has_armv8m_pmu=_common_kwargs(soc, transport)["has_armv8m_pmu"],
            )

        gate_vars = _common_kwargs(soc, transport)
        gate_vars["clean_window_probe"] = clean_window_probe
        gate_vars["window_mode"] = window_mode
        if power_only:
            gate_vars["power_only"] = True
        if engine == "helia-aot":
            # The two heliaAOT inputs _render() supplies that gates read.
            gate_vars.update(allocate_arenas=False, arena_regions=[])
        gate_vars.update(overrides or {})
        self.vars = _finalize(gate_vars)
        self.tokens = _emitted_tokens(self.text)

    def __repr__(self) -> str:  # pragma: no cover - assertion output only
        return self.label


def _matrix() -> list[_Render]:
    renders: list[_Render] = []

    # Base matrix: SoC x transport x engine, transport binary.
    for soc in _SOCS:
        for transport in _TRANSPORTS:
            for engine in _ENGINES:
                if soc not in _ENGINE_SOCS.get(engine, _SOCS):
                    continue
                renders.append(
                    _Render(f"{soc}|{transport}|{engine}", soc, transport, engine)
                )

    # Power binary, every SoC family x engine that has one.
    for soc in _SOCS:
        for engine in _POWER_ENGINES:
            renders.append(
                _Render(f"{soc}|rtt|{engine}|power", soc, "rtt", engine, power_only=True)
            )

    # --- targeted condition variants ------------------------------------
    # Each entry flips one declarative condition; the census below asserts
    # that WIRE_CONDITIONS is exactly the set these renders exercise.
    renders += [
        # psram_needed, both placements, both binaries.
        _Render(
            "ap510|rtt|tflm|psram-arena",
            "apollo510", "rtt", "tflm",
            overrides={"arena_region": "psram"},
        ),
        _Render(
            "ap510|rtt|tflm|psram-weights",
            "apollo510", "rtt", "tflm",
            overrides={"weights_region": "psram"},
        ),
        _Render(
            "ap510|swo|tflm|psram-weights",
            "apollo510", "swo", "tflm",
            overrides={"weights_region": "psram"},
        ),
        _Render(
            "ap510|rtt|tflm|psram-arena|power",
            "apollo510", "rtt", "tflm", power_only=True,
            overrides={"arena_region": "psram"},
        ),
        _Render(
            "ap510|rtt|executorch|psram-weights",
            "apollo510", "rtt", "executorch",
            overrides={"weights_region": "psram"},
        ),
        # heliaAOT external arenas: bound only, blob in TCM, blob in PSRAM.
        _Render(
            "ap510|rtt|helia-aot|arenas-tcm",
            "apollo510", "rtt", "helia-aot",
            overrides={"arena_regions": [_TCM_REGION]},
        ),
        _Render(
            "ap510|rtt|helia-aot|arenas-blob",
            "apollo510", "rtt", "helia-aot",
            overrides={"arena_regions": [_TCM_REGION, _TCM_BLOB_REGION]},
        ),
        _Render(
            "ap510|rtt|helia-aot|arenas-psram-blob",
            "apollo510", "rtt", "helia-aot",
            overrides={"arena_regions": [_TCM_REGION, _PSRAM_BLOB_REGION]},
        ),
        _Render(
            "ap510|rtt|helia-aot|arenas-psram-blob|power",
            "apollo510", "rtt", "helia-aot", power_only=True,
            overrides={"arena_regions": [_TCM_REGION, _PSRAM_BLOB_REGION]},
        ),
        # A PSRAM arena with no blob: the one render where the psram-arena and
        # blob-in-psram gates disagree, which is what makes them separable.
        _Render(
            "ap510|rtt|helia-aot|arenas-psram-noblob",
            "apollo510", "rtt", "helia-aot",
            overrides={"arena_regions": [_TCM_REGION, _PSRAM_REGION]},
        ),
        # Apollo3 burst, both engines that can reach an Apollo3 build (the
        # gate is per-engine, and heliaAOT renders its own template).
        _Render(
            "ap3p|rtt|tflm|burst",
            "apollo3p", "rtt", "tflm",
            overrides={"apollo3_burst": True},
        ),
        _Render(
            "ap3p|rtt|helia-aot|burst",
            "apollo3p", "rtt", "helia-aot",
            overrides={"apollo3_burst": True},
        ),
        # Clean-window trace: emitted on RTT, suppressed on SWO/UART.
        _Render(
            "ap3p|rtt|tflm|trace",
            "apollo3p", "rtt", "tflm",
            overrides={"clean_window_trace": True},
        ),
        _Render(
            "ap3p|swo|tflm|trace",
            "apollo3p", "swo", "tflm",
            overrides={"clean_window_trace": True},
        ),
        _Render(
            "ap510|rtt|executorch|trace",
            "apollo510", "rtt", "executorch",
            overrides={"clean_window_trace": True},
        ),
        # The trace marker has THREE emission sites, one per window body, and
        # only two were covered: ap3p reaches the DWT body and ExecuTorch its
        # own override, leaving the STIMER body's site (_main_base.cc.j2, the
        # `elif use_stimer_window` branch) emitted by no render in the matrix.
        # Both engines that render the base window on a STIMER SoC are here,
        # because they take that branch through different templates.
        _Render(
            "ap510|rtt|tflm|trace",
            "apollo510", "rtt", "tflm",
            overrides={"clean_window_trace": True},
        ),
        _Render(
            "ap510|rtt|helia-aot|trace",
            "apollo510", "rtt", "helia-aot",
            overrides={"clean_window_trace": True},
        ),
        # Busy-loop probe: replaces the window body and forces the STIMER
        # clock on every SoC (the busy window cannot poll DWT — #112), which
        # is also the only way ExecuTorch reaches the shared window.
        _Render(
            "ap510|rtt|tflm|busy-loop",
            "apollo510", "rtt", "tflm", clean_window_probe="busy_loop",
        ),
        _Render(
            "ap3p|rtt|helia-aot|busy-loop",
            "apollo3p", "rtt", "helia-aot", clean_window_probe="busy_loop",
        ),
        _Render(
            "ap510|rtt|executorch|busy-loop",
            "apollo510", "rtt", "executorch", clean_window_probe="busy_loop",
        ),
        _Render(
            "ap510|usb_cdc|executorch|busy-loop",
            "apollo510", "usb_cdc", "executorch", clean_window_probe="busy_loop",
        ),
        _Render(
            "ap3p|rtt|tflm|busy-loop|power",
            "apollo3p", "rtt", "tflm", power_only=True, clean_window_probe="busy_loop",
        ),
        # Adaptive window sizing — the DEFAULT (config.DEFAULT_WINDOW_MODE is
        # "auto"; the matrix above pins "fixed" everywhere else). Rendered on
        # the STIMER SoC too, because that is where the auto branch is
        # interesting: it measures a warm DWT reference before the window
        # whatever clock times the window itself. Since #164 the fixed+STIMER
        # profile (infer) arm measures the same way, so both modes send a real
        # est_ms; the hardcoded 0 survives only in power and busy-loop
        # renders (see EST_MS_GAP and its census test below).
        _Render(
            "ap3p|rtt|tflm|auto-window",
            "apollo3p", "rtt", "tflm", window_mode="auto",
        ),
        _Render(
            "ap510|rtt|tflm|auto-window",
            "apollo510", "rtt", "tflm", window_mode="auto",
        ),
        _Render(
            "ap510|rtt|executorch|auto-window",
            "apollo510", "rtt", "executorch", window_mode="auto",
        ),
        # GPIO power gate armed.
        _Render(
            "ap510|rtt|tflm|power-sync",
            "apollo510", "rtt", "tflm",
            overrides={"power_sync_enabled": True},
        ),
        # Heartbeat by elapsed time rather than operator count.
        _Render(
            "ap510|rtt|tflm|hb-ms",
            "apollo510", "rtt", "tflm",
            overrides={"heartbeat_every_ms": 500, "heartbeat_every_n_ops": 0},
        ),
        # On-target INA228: the only source of the measurement payload.
        _Render(
            "ap510|rtt|tflm|ina228|power",
            "apollo510", "rtt", "tflm", power_only=True, overrides=dict(_INA228_VARS),
        ),
        _Render(
            "ap510|uart|helia-aot|ina228|power",
            "apollo510", "uart", "helia-aot", power_only=True,
            overrides=dict(_INA228_VARS),
        ),
        # Bystander INA228: an external instrument owns the measurement, so a
        # monitor failure is recorded and the run continues. The wire scope is
        # unchanged (the terminal record's shape is a power_monitor question,
        # not an ina228_required one) — which is the point: the difference is
        # entirely in the runtime gates, so the census must see the same
        # tokens from both.
        _Render(
            "ap510|rtt|tflm|ina228-bystander|power",
            "apollo510", "rtt", "tflm", power_only=True,
            overrides={**_INA228_VARS, "ina228_required": False},
        ),
    ]
    return renders


_MATRIX = _matrix()


# ---------------------------------------------------------------------------
# The declarative conditions, as predicates over the render inputs
# ---------------------------------------------------------------------------


def _regions(v: dict) -> list[dict]:
    return list(v.get("arena_regions") or [])


def _psram_needed(v: dict) -> bool:
    return (
        v.get("arena_region") == "psram"
        or v.get("weights_region") == "psram"
        or any(r["placement"] == "psram" for r in _regions(v))
    )


def _aot_external(v: dict) -> bool:
    return not v.get("allocate_arenas", True) and bool(_regions(v))


def _aot_psram(v: dict) -> bool:
    return _aot_external(v) and any(r["placement"] == "psram" for r in _regions(v))


def _aot_blobs(v: dict) -> bool:
    return _aot_external(v) and any(r["blob_filename"] for r in _regions(v))


def _aot_psram_blobs(v: dict) -> bool:
    return _aot_external(v) and any(
        r["blob_filename"] and r["placement"] == "psram" for r in _regions(v)
    )


def _power(v: dict) -> bool:
    return bool(v.get("power_only", False))


def _attach_wait(v: dict) -> bool:
    return (
        bool(v["clean_window_needs_probe_attach"])
        and v["transport"] == "rtt"
        and not _power(v)
        and not v["use_stimer_window"]
    )


#: Maps every declarative condition string in the registry to the predicate
#: that decides it. The census asserts this table's keys are exactly
#: ``WIRE_CONDITIONS`` — a condition invented in ``wire.py`` without a
#: predicate here cannot slip through unchecked.
_PREDICATES = {
    "not power_only": lambda v: not _power(v),
    "not power_only and transport != usb_cdc": (
        lambda v: not _power(v) and v["transport"] != "usb_cdc"
    ),
    "not power_only and transport == rtt": (
        lambda v: not _power(v) and v["transport"] == "rtt"
    ),
    "power_only": _power,
    "power_only and power_monitor == ina228": (
        lambda v: _power(v) and v.get("power_monitor") == "ina228"
    ),
    "apollo3_burst": lambda v: bool(v["apollo3_burst"]),
    "psram_needed": _psram_needed,
    "psram_needed and not power_only": lambda v: _psram_needed(v) and not _power(v),
    "arena_region == psram": lambda v: v.get("arena_region") == "psram",
    "weights_region == psram": lambda v: v.get("weights_region") == "psram",
    "weights_region == psram and transport == rtt and not power_only": (
        lambda v: v.get("weights_region") == "psram"
        and v["transport"] == "rtt"
        and not _power(v)
    ),
    "not allocate_arenas and arena_regions": _aot_external,
    "not allocate_arenas and arena_regions with placement == psram": _aot_psram,
    (
        "not allocate_arenas and arena_regions with placement == psram "
        "and not power_only"
    ): lambda v: _aot_psram(v) and not _power(v),
    "not allocate_arenas and arena_regions with blob_filename": _aot_blobs,
    (
        "not allocate_arenas and arena_regions with blob_filename "
        "and placement == psram"
    ): _aot_psram_blobs,
    "busy_loop_probe": lambda v: bool(v["busy_loop_probe"]),
    "clean_window_trace and transport not in (swo, uart)": (
        lambda v: bool(v.get("clean_window_trace"))
        and v["transport"] not in ("swo", "uart")
    ),
    "not use_stimer_window": lambda v: not v["use_stimer_window"],
    "use_stimer_window and not power_only": (
        lambda v: bool(v["use_stimer_window"]) and not v.get("power_only")
    ),
    (
        "clean_window_needs_probe_attach and transport == rtt and not power_only "
        "and not use_stimer_window"
    ): _attach_wait,
}


def _expected_tokens(render: _Render) -> set[str]:
    expected: set[str] = set()
    for token, spec in WIRE_REGISTRY.items():
        if spec.direction is not WireDirection.DEVICE_TO_HOST:
            continue
        if not spec.emitted_by_firmware:
            continue
        if render.engine not in spec.engines:
            continue
        condition = spec.condition_for(render.engine)
        if condition is None or _PREDICATES[condition](render.vars):
            expected.add(token)
    return expected


# ---------------------------------------------------------------------------
# Registry self-consistency
# ---------------------------------------------------------------------------


def test_every_condition_has_a_predicate_and_vice_versa():
    assert set(_PREDICATES) == set(WIRE_CONDITIONS)


def test_every_condition_is_flipped_both_ways_by_the_matrix():
    """A condition only ever true (or only ever false) proves nothing."""
    never_true = []
    never_false = []
    for condition, predicate in _PREDICATES.items():
        results = {predicate(render.vars) for render in _MATRIX}
        if True not in results:
            never_true.append(condition)
        if False not in results:
            never_false.append(condition)
    assert not never_true, f"conditions never satisfied by any render: {never_true}"
    assert not never_false, f"conditions satisfied by every render: {never_false}"


#: (condition, engine) pairs the matrix cannot flip both ways because the
#: combination does not exist in production. Pinned literally, with the reason,
#: because the alternative — letting the gate-flip test skip whatever it cannot
#: reach — is how a condition ends up proven for one engine and assumed for the
#: other two. Reachability depends only on the condition and the engine, so
#: specs sharing a condition share an entry.
#:
#: Every entry is asserted to still be unreachable, so widening the matrix
#: turns a stale exemption into a failure rather than dead weight.
_UNFLIPPABLE_PAIRS: dict[tuple[str, str], str] = {
    ("not power_only", "executorch"): (
        "ExecuTorch has no power binary at all: preflight rejects "
        "engine.type=executorch with power.enabled, so power_only is never "
        "true for it and the gate has only one reachable side."
    ),
    ("busy_loop_probe", "helia-rt"): (
        "heliaRT renders main.cc.j2 byte-identically to tflm, which carries "
        "the busy-loop renders; a second identical render would prove nothing."
    ),
    ("apollo3_burst", "executorch"): (
        "ExecuTorch is Cortex-M55 (apollo510) only and burst is an Apollo3 "
        "feature, so no build has both."
    ),
    ("apollo3_burst", "helia-rt"): (
        "heliaRT renders main.cc.j2 byte-identically to tflm, which carries "
        "the burst render; a second identical render would prove nothing."
    ),
    ("weights_region == psram and transport == rtt and not power_only", "helia-rt"): (
        "Same template as tflm, which carries the PSRAM-weights renders."
    ),
    ("weights_region == psram", "helia-rt"): (
        "Same template as tflm, which carries the PSRAM-weights renders."
    ),
    ("arena_region == psram", "helia-rt"): (
        "Same template as tflm, which carries the PSRAM-arena renders."
    ),
    ("psram_needed", "helia-rt"): (
        "Same template as tflm, which carries every PSRAM render."
    ),
    ("psram_needed and not power_only", "helia-rt"): (
        "Same template as tflm, which carries every PSRAM render."
    ),
    ("power_only and power_monitor == ina228", "helia-rt"): (
        "Same template as tflm, which carries the INA228 power renders."
    ),
    ("clean_window_trace and transport not in (swo, uart)", "helia-rt"): (
        "Same template as tflm, which carries the trace renders on both the "
        "DWT and STIMER window bodies."
    ),
}


def test_helia_rt_renders_identically_to_tflm_but_for_the_engine_id():
    """The premise most of :data:`_UNFLIPPABLE_PAIRS` rests on, proven.

    Eight exemptions read "same template as tflm, which flips it both ways".
    That is a claim about the firmware, not an excuse, so it is checked rather
    than trusted: if heliaRT ever grows a branch of its own, those exemptions
    stop being harmless and this test says so before the census silently loses
    an engine's coverage.
    """
    for soc in _SOCS:
        for transport in _TRANSPORTS:
            tflm = _render(soc, transport, "tflm")
            helia_rt = _render(soc, transport, "helia-rt")
            assert helia_rt.replace("HPX_ENGINE=helia_rt", "HPX_ENGINE=tflm") == tflm, (
                f"{soc}|{transport}: heliaRT no longer renders main.cc.j2 "
                "identically to tflm — the _UNFLIPPABLE_PAIRS entries that "
                "defer heliaRT coverage to tflm need real renders now"
            )


def test_every_gate_is_flipped_both_ways_for_every_engine_in_its_scope():
    """Per (spec, engine), not per condition.

    ``test_every_condition_is_flipped_both_ways_by_the_matrix`` asks only
    whether the matrix contains *some* render on each side of each condition.
    That is satisfied by a single engine: ``psram_needed`` flipped by tflm
    renders says nothing about whether heliaAOT's template still honours it,
    and the two do not even share a template. This asks the sharper question —
    for every spec, and every engine that spec claims to be in scope for, does
    the matrix hold a render of *that engine* on each side of the gate?

    Unreachable combinations are whitelisted literally in
    :data:`_UNFLIPPABLE_PAIRS` with a reason each, and the whitelist is checked
    for staleness in both directions.
    """
    gaps: list[str] = []
    exercised: set[tuple[str, str]] = set()
    for token, spec in WIRE_REGISTRY.items():
        if spec.direction is not WireDirection.DEVICE_TO_HOST:
            continue
        if not spec.emitted_by_firmware:
            continue
        for engine in sorted(spec.engines, key=lambda e: e.value):
            condition = spec.condition_for(engine)
            if condition is None:
                continue
            predicate = _PREDICATES[condition]
            results = {
                predicate(render.vars)
                for render in _MATRIX
                if render.engine is engine
            }
            if True in results and False in results:
                exercised.add((condition, engine.value))
                continue
            if (condition, engine.value) in _UNFLIPPABLE_PAIRS:
                continue
            side = "never true" if True not in results else "never false"
            gaps.append(f"{token} x {engine.value}: `{condition}` is {side}")

    assert not gaps, (
        "these gates are asserted for an engine the matrix never flips them "
        "for, so the spec's engine scope is documentation rather than a "
        "tested claim — add a render, or whitelist the pair in "
        "_UNFLIPPABLE_PAIRS with the reason it cannot exist:\n  "
        + "\n  ".join(sorted(gaps))
    )
    stale = sorted(pair for pair in _UNFLIPPABLE_PAIRS if pair in exercised)
    assert not stale, (
        "_UNFLIPPABLE_PAIRS claims these cannot be flipped, but the matrix "
        f"now flips them — drop the exemption: {stale}"
    )
    unknown = sorted(
        pair
        for pair in _UNFLIPPABLE_PAIRS
        if pair[0] not in _PREDICATES or pair[1] not in _ENGINES
    )
    assert not unknown, f"_UNFLIPPABLE_PAIRS names a condition or engine that no longer exists: {unknown}"


def test_wire_key_round_trips_between_its_two_spellings():
    for key in WireKey:
        spec = WIRE_REGISTRY[key.wire]
        assert spec.key is key
        assert spec_for(key) is spec
        if spec.kind is WireKind.KEY_VALUE:
            # The parser lower-cases the matched key, so the member NAME is
            # the wire spelling for every \w+ key.
            assert key.wire == f"HPX_{key.name}"
            assert key.value == key.name.lower()
        else:
            # The two printf-indexed heliaAOT keys are the documented
            # exception: the %d survives the case conversion.
            assert spec.kind is WireKind.KEY_VALUE_INDEXED
            assert "%d" in key.wire


def test_every_registry_key_is_the_specs_own_token():
    for token, spec in WIRE_REGISTRY.items():
        assert token == spec.token


#: The gates that place a token's source inside the dedicated power binary.
_POWER_ONLY_GATES = frozenset({"power_only", "power_only and power_monitor == ina228"})


def test_the_binary_axis_agrees_with_the_condition():
    """``binary`` is derivable, so it must never be hand-set out of step.

    :attr:`WireSpec.binary` is the one axis the render census cannot check: it
    records which firmware *prints* the token at runtime, and the power
    binary's silence comes from ``hpx_printf`` compiling to a no-op rather than
    from anything visible in the render. Left unchecked it is a free-text field
    that drifts — and it is the field the generated reference turns into
    "Power binary only", so a wrong value misinforms every reader.

    It is not free-text in practice: a token is power-binary iff it belongs to
    the terminal grammar or its condition is one of the power-only gates.
    Everything else belongs to the transport-attached profiler, including the
    many tokens whose source is compiled into both.
    """
    for token, spec in WIRE_REGISTRY.items():
        conditions = {spec.condition, *spec.engine_conditions.values()}
        power_shaped = spec.kind is WireKind.TERMINAL or bool(
            conditions & _POWER_ONLY_GATES
        )
        expected = WireBinary.POWER if power_shaped else WireBinary.TRANSPORT
        assert spec.binary is expected, (
            f"{token}: binary={spec.binary.value} but its grammar/condition "
            f"({spec.kind.value}, {sorted(c for c in conditions if c)}) puts it "
            f"in the {expected.value} binary"
        )
        if spec.binary is WireBinary.POWER:
            assert spec.engines == POWER_BINARY_ENGINES, (
                f"{token}: a power-binary token scoped to {sorted(e.value for e in spec.engines)}; "
                "the power binary exists for exactly POWER_BINARY_ENGINES"
            )


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("render", _MATRIX, ids=lambda r: r.label)
def test_render_emits_exactly_what_the_registry_declares(render: _Render):
    expected = _expected_tokens(render)
    actual = render.tokens

    undeclared = sorted(actual - set(WIRE_REGISTRY))
    assert not undeclared, (
        f"{render.label}: firmware prints tokens with no spec in "
        f"helia_profiler.wire: {undeclared}"
    )
    missing = sorted(expected - actual)
    assert not missing, (
        f"{render.label}: the registry says these are emitted here and they "
        f"are not: {missing}"
    )
    unexpected = sorted(actual - expected)
    assert not unexpected, (
        f"{render.label}: emitted although the registry's condition says "
        f"otherwise (wrong engine scope or wrong condition): {unexpected}"
    )


def test_no_spec_is_dead():
    """Every declared token is emitted by at least one render in the matrix."""
    seen: set[str] = set()
    for render in _MATRIX:
        seen |= render.tokens
    declared = {
        token
        for token, spec in WIRE_REGISTRY.items()
        if spec.direction is WireDirection.DEVICE_TO_HOST and spec.emitted_by_firmware
    }
    assert not sorted(declared - seen), (
        "declared but emitted by no render — either the matrix lost a "
        f"variant or the templates dropped the token: {sorted(declared - seen)}"
    )


def test_tokens_the_matrix_cannot_show_are_pinned_by_hand():
    """The one spec deliberately outside the emitted census, stated here.

    HPX_GO is a real part of the contract that no render can demonstrate, so
    it is asserted directly rather than being quietly exempted. (The other
    census-invisible spec, the never-emitted HPX_POWER_SAMPLE_COUNT, was
    retired by #165 — an envelope carrying it is now rejected as unknown.)
    """
    go = WIRE_REGISTRY["HPX_GO"]
    assert go.direction is WireDirection.HOST_TO_DEVICE
    assert not go.emitted_by_firmware

    assert "HPX_POWER_SAMPLE_COUNT" not in WIRE_REGISTRY
    assert not any(key.value == "HPX_POWER_SAMPLE_COUNT" for key in PowerTerminalKey)


def test_no_macro_name_ever_reaches_a_string():
    """The ``#define`` namespace and the wire namespace must not cross.

    ``HPX_SWO_SYNC_PREAMBLE_LINES``, ``HPX_CLEAN_DCACHE`` and the rest are
    firmware internals. If one turned up inside a printf format it would
    become an undeclared wire token overnight, and the reverse — a wire token
    demoted to a bare macro reference — would silently stop being emitted.
    """
    for render in _MATRIX:
        bare = _bare_tokens(render.text)
        crossed = sorted(bare & render.tokens)
        assert not crossed, (
            f"{render.label}: these appear both as macro names and inside "
            f"string literals: {crossed}"
        )
        for token in bare:
            assert token not in WIRE_REGISTRY, (
                f"{render.label}: {token} is a registered wire token but this "
                "render references it as a bare identifier"
            )


# ---------------------------------------------------------------------------
# Catalogue pins
# ---------------------------------------------------------------------------


def test_error_code_catalogue():
    assert {code.value for code in FirmwareErrorCode} == {
        "schema_mismatch",
        "unsupported_op",
        "missing_ops",
        "alloc_tensors_failed",
        "psram_init_failed",
        "psram_info_failed",
        "bind_arena_failed",
        "const_blob_psram_write_failed",
        "model_init_failed",
        "executorch",
        "operator_count_exceeds_capacity",
        "pmu_init_or_selftest_failed",
        "stimer_dead",
    }
    assert {spec.token for spec in WIRE_REGISTRY.values() if spec.kind is WireKind.ERROR} == {
        error_token(code) for code in FirmwareErrorCode
    }


def test_warn_code_catalogue():
    assert {code.value for code in FirmwareWarnCode} == {"unusual_dtype"}
    assert {spec.token for spec in WIRE_REGISTRY.values() if spec.kind is WireKind.WARN} == {
        warn_token(code) for code in FirmwareWarnCode
    }


def test_error_hints_are_keyed_by_the_enum_and_agree_with_the_registry():
    assert set(_ERROR_HINTS) <= set(FirmwareErrorCode)
    hinted = {
        FirmwareErrorCode(spec.token.removeprefix("HPX_ERROR="))
        for spec in WIRE_REGISTRY.values()
        if spec.kind is WireKind.ERROR and spec.has_host_hint
    }
    assert hinted == set(_ERROR_HINTS)


def test_every_error_code_carries_a_hint():
    """Adding a code without deciding on a hint has to be a conscious act.

    #163 disclosed six codes that reached the user with a generic "the
    payload is shown above" message; #165 closed that gap. This pin makes
    reopening it — an error code whose remediation nobody wrote down — a
    review decision rather than an accident.
    """
    hintless = {code.value for code in FirmwareErrorCode} - {
        code.value for code in _ERROR_HINTS
    }
    assert hintless == set()


def test_heartbeat_phase_catalogue():
    assert [phase.value for phase in HeartbeatPhase] == [
        "clean_window_begin",
        "init",
        "allocate",
        "allocated",
        "model_init_done",
        "infer",
        "warmup_done",
        "flushing",
    ]
    assert {
        spec.token for spec in WIRE_REGISTRY.values() if spec.kind is WireKind.HEARTBEAT
    } == {heartbeat_token(phase) for phase in HeartbeatPhase}


def test_tflm_init_heartbeat_keeps_its_t0_payload():
    """The one payload variant the phase catalogue cannot express."""
    tflm = _render("apollo510", "rtt", "tflm")
    aot = _render("apollo510", "rtt", "helia-aot")
    assert 'HPX_HEARTBEAT phase=init t=0\\n' in tflm
    assert 'HPX_HEARTBEAT phase=init\\n' in aot


def test_clean_window_begin_is_the_protocol_critical_phase():
    """The host parses iters= and est_ms= out of exactly this line."""
    text = _render("apollo3p", "rtt", "tflm")
    assert "HPX_HEARTBEAT phase=clean_window_begin iters=%d est_ms=" in text


def test_the_est_ms_gap_is_told_once_and_is_true_of_the_firmware():
    """The gap statement is single-sourced, and the firmware agrees with it.

    The claim has narrowed three times. First (#163) from "every apollo510
    profile build" to "fixed+STIMER only": ``config.DEFAULT_WINDOW_MODE`` is
    ``auto``, and the auto branch measures a warm DWT reference and sends a
    real estimate whatever clock times the window. Then (#164) the
    fixed+STIMER profile *infer* arm gained the same pre-window DWT
    measurement — the debug domain is gated only inside the window, so DWT is
    valid where the measurement happens. Then (#170) busy-loop windows gained
    the honest compile-time ``window_target_ms`` announce in both window
    modes, and ``power_only`` became the template's first arm — so the
    hardcoded zero survives only in dedicated power binaries (announce
    compiled to a no-op, no host listener). The statement lives once, in
    :data:`EST_MS_GAP`. The renders below prove its printf-placement clauses;
    the *runtime and host-policy* clauses (the no-op ``hpx_printf``
    definition, an estimate degrading to 0 under a frozen DWT, the host's
    hold-floor and cap) are firmware/runtime/host facts a render census
    cannot observe — the host half is pinned by ``tests/test_transport.py``.
    """
    assert EST_MS_GAP in WIRE_REGISTRY[
        heartbeat_token(HeartbeatPhase.CLEAN_WINDOW_BEGIN)
    ].note

    zero = "HPX_HEARTBEAT phase=clean_window_begin iters=%d est_ms=0\\n"
    measured = (
        "HPX_HEARTBEAT phase=clean_window_begin iters=%d est_ms=%llu\\n"
    )
    # iters=1 is part of the busy contract: the window completes exactly one
    # busy pass, and on a lossy transport that drops HPX_CLEAN_INFER_COUNT
    # the host's iters fallback feeds the gate-duration check — a planned
    # count would fail a healthy run as a duration mismatch (#170).
    target_literal = (
        f"HPX_HEARTBEAT phase=clean_window_begin iters=1 "
        f"est_ms={_common_kwargs('apollo510', 'rtt')['window_target_ms']}\\n"
    )
    # Every infer profile render announces a measured estimate in BOTH window
    # modes — the #164 false-timeout needed exactly one configuration,
    # apollo510 (STIMER window) with window_mode: fixed, and these are the
    # renders that prove it closed.
    for engine in ("tflm", "helia-aot", "executorch"):
        fixed = _render("apollo510", "rtt", engine, window_mode="fixed")
        auto = _render("apollo510", "rtt", engine, window_mode="auto")
        assert measured in fixed, engine
        assert zero not in fixed, engine
        assert measured in auto, engine
        assert zero not in auto, engine
    # ...a DWT-timed fixed window derives one from its stall-check warmup...
    assert measured in _render("apollo3p", "rtt", "tflm", window_mode="fixed")
    # ...busy-loop windows announce the compile-time target in BOTH window
    # modes — the only duration statement that describes a busy loop sized to
    # fill window_target_ms (#170); never the measured shape, never zero —
    # on the STIMER SoC and a DWT SoC alike (busy forces STIMER everywhere)...
    for soc in ("apollo510", "apollo4p"):
        for wm in ("fixed", "auto"):
            busy = _render(
                soc, "rtt", "tflm",
                clean_window_probe="busy_loop", window_mode=wm,
            )
            assert target_literal in busy, (soc, wm)
            assert measured not in busy, (soc, wm)
            assert zero not in busy, (soc, wm)
    # ...and the hardcoded zero survives ONLY in dedicated power binaries
    # (minimal power image, no listener — hpx_printf is a no-op), whose
    # first-arm exclusion also strips the measured shape in every mode/probe.
    for wm in ("fixed", "auto"):
        for probe in ("infer", "busy_loop"):
            power = _render(
                "apollo510", "rtt", "tflm",
                power_only=True, clean_window_probe=probe, window_mode=wm,
            )
            assert zero in power, (wm, probe)
            assert measured not in power, (wm, probe)
            assert target_literal not in power, (wm, probe)


def test_power_renders_measure_nothing_pre_window():
    """The power arm is the template's FIRST branch of the warmup as well as
    the announce — EST_MS_GAP's structural claim. The announce half is pinned
    by the est_ms census above; this pins the warmup half, which a mutation
    probe in the #171 review showed was otherwise unguarded: re-ordering the
    arms so auto+power fell back into the auto arm's measurement left the
    whole suite green, because the snapshot matrices render fixed-only.

    ``dwt_init();`` never renders in a power render at all — the boot call
    is gated off power builds too (#161: DWT has no power-render consumer,
    and TRCENA/CYCCNTENA there was debug circuitry enabled for nothing) —
    and the warmup DWT bracket (``wt0 = DWT->CYCCNT``) never renders, in
    either window mode, either probe. On AP4 power the debug domain is off
    at boot, so a leaked pre-window read would be frozen garbage feeding
    the announce or sizing.
    """
    for wm in ("fixed", "auto"):
        for probe in ("infer", "busy_loop"):
            power = _render(
                "apollo4p", "rtt", "tflm",
                power_only=True, clean_window_probe=probe, window_mode=wm,
            )
            assert power.count("dwt_init();") == 0, (wm, probe)
            assert "uint32_t wt0 = DWT->CYCCNT;" not in power, (wm, probe)
            assert "target_cyc" not in power, (wm, probe)


def test_power_terminal_key_sets():
    assert POWER_TERMINAL_REQUIRED_KEYS == {
        "HPX_POWER_TERMINAL_VERSION",
        "HPX_POWER_STATUS",
        "HPX_POWER_REQUESTED_COUNT",
        "HPX_POWER_COMPLETED_COUNT",
        "HPX_POWER_ELAPSED_US",
        "HPX_POWER_FINAL_PHASE",
        "HPX_POWER_ERROR_CODE",
        "HPX_POWER_GATE_ASSERTED",
        "HPX_POWER_GATE_LOWERED",
    }
    assert POWER_TERMINAL_OPTIONAL_KEYS == {
        "HPX_POWER_MEASUREMENT_SOURCE",
        "HPX_POWER_MEASUREMENT_SCOPE",
        "HPX_POWER_ENERGY_NJ",
        "HPX_POWER_MEASUREMENT_DURATION_US",
        "HPX_POWER_MEASUREMENT_COUNT",
        "HPX_POWER_MEASUREMENT_OVERFLOW",
        "HPX_POWER_CHARGE_NC",
        "HPX_POWER_BUS_VOLTAGE_UV",
        "HPX_POWER_CALIBRATION_ID",
    }
    assert not POWER_TERMINAL_REQUIRED_KEYS & POWER_TERMINAL_OPTIONAL_KEYS
    assert {key.value for key in PowerTerminalKey} == (
        POWER_TERMINAL_REQUIRED_KEYS
        | POWER_TERMINAL_OPTIONAL_KEYS
        | {"HPX_POWER_INA228_DIAG", "HPX_POWER_INA228_BYSTANDER_FAILED"}
    )


def test_power_terminal_parser_derives_its_schema_from_the_registry():
    from helia_profiler.capture import power_terminal

    assert power_terminal._REQUIRED_KEYS == POWER_TERMINAL_REQUIRED_KEYS
    assert power_terminal._OPTIONAL_KEYS == POWER_TERMINAL_OPTIONAL_KEYS


def _csv_emitter(soc: str, engine: str, *, has_armv8m_pmu: bool = True) -> str:
    """Everything that can print a CSV row for this engine.

    TFLM/heliaRT print theirs from the profiler class rather than from
    main.cc, so both translation units are concatenated — the earlier version
    of this helper *replaced* the app render with the profiler render, which
    meant the tflm case silently asserted nothing about main.cc and the
    profiler case asserted nothing about the SoC it claimed to cover.
    """
    text = _render(soc, "rtt", engine)
    if engine in ("tflm", "helia-rt"):
        text += _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
            profiling_backends=["armv8m-pmu"] if has_armv8m_pmu else ["dwt"],
            has_armv8m_pmu=has_armv8m_pmu,
        )
    return text


def test_csv_header_shape_is_the_same_for_every_engine():
    """The CSV body has no HPX_ token, so the census cannot see it."""
    for soc, engine in (
        ("apollo510", "tflm"),
        ("apollo510", "helia-aot"),
        ("apollo510", "executorch"),
    ):
        text = _csv_emitter(soc, engine)
        assert '\\"Layer\\",\\"Op\\"' in text, engine
        assert ',\\"overflow\\"\\n' in text, engine

    # The Cortex-M4 profiler is a second, independent emitter: no Armv8-M PMU,
    # so it prints a fixed single-counter header from its own printf rather
    # than looping over the pass's counter names. Rendering only the Armv8-M
    # variant left that whole branch — half the file — unpinned.
    m4 = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
        profiling_backends=["dwt"], has_armv8m_pmu=False
    )
    assert '\\"Layer\\",\\"Op\\",\\"ARM_PMU_CPU_CYCLES\\",\\"overflow\\"\\n' in m4


def test_csv_row_format_is_pinned_per_engine():
    """The Op column, which is the only thing the engines differ in.

    ``CSV_GRAMMAR`` says these row shapes are "pinned by the census
    contracts"; before this test that sentence was aspirational — the header
    was pinned and the rows were not, so an engine could start emitting a bare
    index where the host expects ``OPTYPE:opid`` and nothing would notice
    until a parse produced unlabelled layers.
    """
    tflm = _csv_emitter("apollo510", "tflm")
    # The tag comes from TFLM's per-op tag string, with `?` where it has none.
    assert 'hpx_printf("%d,%s", i, rec.tag ? rec.tag : "?");' in tflm
    # ...and the non-Armv8-M profiler prints the whole row in one call.
    m4 = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
        profiling_backends=["dwt"], has_armv8m_pmu=False
    )
    assert 'hpx_printf("%d,%s,%lu,%d\\n",' in m4

    aot = _render("apollo510", "rtt", "helia-aot")
    assert 'hpx_printf("%d,%s:%ld", i, aot_op_name(i), (long)aot_op_id(i));' in aot

    et = _render("apollo510", "rtt", "executorch")
    assert 'hpx_printf("%d,%s:c%ldi%lu", i,' in et
    assert '? "OPERATOR_CALL" : "DELEGATE_CALL",' in et

    # The three loop-based emitters close the row with the overflow flag; the
    # Cortex-M4 profiler folds it into the single call pinned above.
    for label, text in (("tflm", tflm), ("helia-aot", aot), ("executorch", et)):
        assert 'hpx_printf(",%d\\n"' in text, label


# ---------------------------------------------------------------------------
# Emission discipline
# ---------------------------------------------------------------------------


def _dunder_all_positions(source: str) -> set[tuple[int, int]]:
    """Source positions of every string constant inside an ``__all__`` list."""
    positions: set[tuple[int, int]] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            continue
        for element in ast.walk(node.value):
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                positions.add((element.lineno, element.col_offset))
    return positions


#: The f-string token types, absent before 3.12 (where an f-string arrives as
#: one ``STRING`` token instead and is recovered through the AST below).
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _placeholder_free_fstring(text: str) -> str | None:
    """The content of ``f"..."`` when it interpolates nothing, else ``None``."""
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return None
    if isinstance(node, ast.JoinedStr) and all(
        isinstance(value, ast.Constant) and isinstance(value.value, str)
        for value in node.values
    ):
        return "".join(value.value for value in node.values)
    return None


def _constant_strings(source: str) -> list[tuple[tuple[int, int], str, str]]:
    """Every string constant in *source* as (position, content, source text).

    Constant f-strings are included: ``f"HPX_START"`` is a wire literal wearing
    a disguise the tokenizer used to hide, because CPython 3.12 splits an
    f-string into FSTRING_START / FSTRING_MIDDLE / FSTRING_END and the guard
    only looked at ``STRING``. F-strings that *do* interpolate are excluded —
    ``f"HPX_{name}"`` composes a token rather than duplicating one, which is
    the documented limit of this scan and the reason the registry ships
    ``heartbeat_token()`` and friends.
    """
    found: list[tuple[tuple[int, int], str, str]] = []
    stack: list[dict] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if _FSTRING_START is not None and token.type == _FSTRING_START:
            stack.append(
                {"pos": token.start, "parts": [], "text": [token.string], "interp": False}
            )
            continue
        if stack:
            frame = stack[-1]
            if token.type == _FSTRING_MIDDLE:
                frame["parts"].append(token.string)
                frame["text"].append(token.string)
                continue
            if token.type == _FSTRING_END:
                stack.pop()
                frame["text"].append(token.string)
                if not frame["interp"]:
                    found.append(
                        (frame["pos"], "".join(frame["parts"]), "".join(frame["text"]))
                    )
                continue
            # Anything else inside the braces makes this an interpolation —
            # but a plain string nested in a placeholder is still a literal in
            # its own right, so fall through rather than skipping it.
            frame["interp"] = True
        if token.type is not tokenize.STRING:
            continue
        try:
            content = ast.literal_eval(token.string)
        except (ValueError, SyntaxError):
            recovered = _placeholder_free_fstring(token.string)
            if recovered is None:
                continue
            found.append((token.start, recovered, token.string))
            continue
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        if isinstance(content, str):
            found.append((token.start, content, token.string))
    return found


def test_no_bare_wire_literal_survives_in_src():
    """No module but ``wire.py`` may spell a protocol token as a literal.

    The inventory that preceded this registry found ``"--- HPX_START ---"``
    living in three modules independently; that is the class of drift this
    guard closes. Every module is tokenized (so a token named in a comment or
    inside a docstring is prose, not a duplicate) and each string literal is
    flagged when its whole content *is* a registered token, a token with its
    ``=``, or a ``--- HPX_`` sentinel frame.

    The *grammars* are covered too, not just the tokens: a reintroduced
    ``r"^HPX_(\\w+)=(.+)$"`` or ``r"^--- HPX_ITER (\\d+) ---$"`` is a second
    copy of the protocol exactly as a duplicated sentinel is, so the registry's
    three pattern constants are matched as literals and the leading ``^`` is
    stripped before the sentinel-frame check.

    Stated limits, as in ``tests/test_issue_codes.py``: a token mentioned
    mid-sentence in a log message is fine and not flagged, and an f-string that
    *interpolates* (``f"HPX_{name}"``) composes a token rather than duplicating
    one — the registry's helper functions are the guard for that shape. A
    placeholder-free f-string is a plain literal and is flagged like one.
    """
    exact = set(WIRE_REGISTRY) | {f"{token}=" for token in WIRE_REGISTRY}
    exact |= {spec.literal for spec in WIRE_REGISTRY.values() if spec.literal}
    exact |= {"HPX_", "HPX_HEARTBEAT", "HPX_ERROR=", "HPX_WARN="}
    exact |= {
        KEY_VALUE_PATTERN,
        HPX_PRESET_SENTINEL_PATTERN,
        HPX_ITER_SENTINEL_PATTERN,
    }

    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.is_relative_to(SRC / "wire"):
            continue
        source = path.read_text(encoding="utf-8")
        # ``__all__`` entries are Python symbol names that happen to spell a
        # token (transport re-exports HPX_START/HPX_END under those names);
        # they are not wire literals.
        exported = _dunder_all_positions(source)
        for position, content, text in _constant_strings(source):
            if position in exported:
                continue
            if content in exact or content.lstrip("^").startswith("--- HPX_"):
                offenders.append(f"{path.relative_to(ROOT)}:{position[0]}: {text}")
    assert not offenders, (
        "bare HPX wire literals found in src/ — import them from "
        "helia_profiler.wire:\n" + "\n".join(offenders)
    )
