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
    POWER_TERMINAL_OPTIONAL_KEYS,
    POWER_TERMINAL_REQUIRED_KEYS,
    WIRE_CONDITIONS,
    WIRE_REGISTRY,
    FirmwareErrorCode,
    FirmwareWarnCode,
    HeartbeatPhase,
    PowerTerminalKey,
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
    """
    literals: list[str] = []
    code: list[str] = []
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
            literals.append(text[start:i])
            i += 1
        else:
            code.append(char)
            i += 1
    return literals, "".join(code)


def _string_literals(text: str) -> list[str]:
    return _split_c(text)[0]


def _emitted_tokens(text: str) -> set[str]:
    """Registry keys for every wire token *text* can print.

    Heartbeat, error and warning lines resolve to their phase/code-qualified
    registry key; everything else is the bare token. A bare ``HPX_ERROR`` with
    no recognisable code stays bare on purpose, so it surfaces as an
    undeclared emission rather than being silently absorbed.
    """
    tokens: set[str] = set()
    for literal in _string_literals(text):
        phases = _HEARTBEAT_RE.findall(literal)
        errors = _ERROR_RE.findall(literal)
        warns = _WARN_RE.findall(literal)
        tokens |= {f"HPX_HEARTBEAT phase={phase}" for phase in phases}
        tokens |= {f"HPX_ERROR={code}" for code in errors}
        tokens |= {f"HPX_WARN={code}" for code in warns}
        for token in _TOKEN_RE.findall(literal):
            if token == "HPX_HEARTBEAT" and phases:
                continue
            if token == "HPX_ERROR" and errors:
                continue
            if token == "HPX_WARN" and warns:
                continue
            tokens.add(token)
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
        # Apollo3 burst.
        _Render(
            "ap3p|rtt|tflm|burst",
            "apollo3p", "rtt", "tflm",
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
        # Busy-loop probe: replaces the window body and forces the DWT clock,
        # which is also the only way ExecuTorch reaches the shared window.
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
        # Adaptive window sizing.
        _Render(
            "ap3p|rtt|tflm|auto-window",
            "apollo3p", "rtt", "tflm", window_mode="auto",
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
    """The two specs deliberately outside the emitted census, stated here.

    Both are real parts of the contract that no render can demonstrate, so
    they are asserted directly rather than being quietly exempted.
    """
    go = WIRE_REGISTRY["HPX_GO"]
    assert go.direction is WireDirection.HOST_TO_DEVICE
    assert not go.emitted_by_firmware

    sample_count = WIRE_REGISTRY[PowerTerminalKey.SAMPLE_COUNT.value]
    assert not sample_count.emitted_by_firmware
    assert sample_count.token in POWER_TERMINAL_OPTIONAL_KEYS
    assert not any(
        sample_count.token in render.tokens for render in _MATRIX
    ), "HPX_POWER_SAMPLE_COUNT is now emitted — update its spec and the docs gap"


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


def test_hintless_error_codes_are_the_disclosed_six():
    """Adding a code without deciding on a hint has to be a conscious act.

    Six of the twelve reach the user with a generic "the payload is shown
    above" message. That is a known gap (#162 Phase 1 records it rather than
    smuggling in hints); this pin makes growing it a review decision.
    """
    hintless = {code.value for code in FirmwareErrorCode} - {
        code.value for code in _ERROR_HINTS
    }
    assert hintless == {
        "psram_info_failed",
        "bind_arena_failed",
        "const_blob_psram_write_failed",
        "executorch",
        "operator_count_exceeds_capacity",
        "pmu_init_or_selftest_failed",
    }


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
        "HPX_POWER_SAMPLE_COUNT",
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


def test_csv_header_shape_is_the_same_for_every_engine():
    """The CSV body has no HPX_ token, so the census cannot see it."""
    for soc, engine in (
        ("apollo510", "tflm"),
        ("apollo510", "helia-aot"),
        ("apollo510", "executorch"),
    ):
        text = _render(soc, "rtt", engine)
        if engine == "tflm":
            text = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
                profiling_backends=["armv8m-pmu"], has_armv8m_pmu=True
            )
        assert '\\"Layer\\",\\"Op\\"' in text
        assert ',\\"overflow\\"\\n' in text


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


def test_no_bare_wire_literal_survives_in_src():
    """No module but ``wire.py`` may spell a protocol token as a literal.

    The inventory that preceded this registry found ``"--- HPX_START ---"``
    living in three modules independently; that is the class of drift this
    guard closes. Every module is tokenized (so a token named in a comment or
    inside a docstring is prose, not a duplicate) and each string literal is
    flagged when its whole content *is* a registered token, a token with its
    ``=``, or a ``--- HPX_`` sentinel frame.

    Stated limits, as in ``tests/test_issue_codes.py``: a token mentioned
    mid-sentence in a log message is fine and not flagged, and a composed
    string (``f"HPX_{name}"``) is beyond a literal scan — the registry's
    helper functions are the guard for that shape.
    """
    exact = set(WIRE_REGISTRY) | {f"{token}=" for token in WIRE_REGISTRY}
    exact |= {spec.literal for spec in WIRE_REGISTRY.values() if spec.literal}
    exact |= {"HPX_", "HPX_HEARTBEAT", "HPX_ERROR=", "HPX_WARN="}

    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.is_relative_to(SRC / "wire"):
            continue
        source = path.read_text(encoding="utf-8")
        # ``__all__`` entries are Python symbol names that happen to spell a
        # token (transport re-exports HPX_START/HPX_END under those names);
        # they are not wire literals.
        exported = _dunder_all_positions(source)
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.start in exported:
                continue
            if token.type is not tokenize.STRING:
                continue
            try:
                content = ast.literal_eval(token.string)
            except (ValueError, SyntaxError):  # pragma: no cover - f-strings
                continue
            if not isinstance(content, (str, bytes)):
                continue
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            if content in exact or content.startswith("--- HPX_"):
                offenders.append(
                    f"{path.relative_to(ROOT)}:{token.start[0]}: {token.string}"
                )
    assert not offenders, (
        "bare HPX wire literals found in src/ — import them from "
        "helia_profiler.wire:\n" + "\n".join(offenders)
    )
