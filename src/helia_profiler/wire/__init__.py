"""The HPX wire protocol: one declaration of everything firmware and host exchange.

Every byte the profiler firmware puts on a transport, and the one command the
host writes back, is declared here once. Before this module the protocol lived
as string literals scattered across sixteen Jinja templates and six host
modules — ``"--- HPX_START ---"`` alone existed as three independent copies —
and nothing anywhere said which keys a given engine, transport, or binary is
supposed to produce. :data:`WIRE_REGISTRY` is that statement, and
``tests/contracts/test_wire_protocol.py`` holds the rendered firmware to it.

Five grammars
-------------

1. **Key/value** (:attr:`WireKind.KEY_VALUE`) — ``HPX_<KEY>=<value>`` lines in
   the profile stream, parsed by :data:`KEY_VALUE_RE` into ``meta_kv`` with the
   key lower-cased. :class:`WireKey` *values are those lower-cased names*, so
   ``meta_kv[WireKey.CLEAN_INFER_COUNT]`` is the same lookup the hand-written
   string did; :attr:`WireKey.wire` recovers the ``HPX_…`` spelling.
   heliaAOT's per-index ``HPX_INPUT_%d_SIZE`` / ``HPX_OUTPUT_%d_SIZE`` are
   :attr:`WireKind.KEY_VALUE_INDEXED`: one printf format, one meta key per
   model input.
2. **Sentinels** (:attr:`WireKind.SENTINEL`) — ``--- HPX_START ---`` and its
   siblings, which frame the stream, plus the two power-terminal markers.
   :attr:`WireSpec.literal` carries the exact line.
3. **Heartbeats** (:attr:`WireKind.HEARTBEAT`) — ``HPX_HEARTBEAT phase=<p> …``
   progress records, one spec per :class:`HeartbeatPhase`. Matched *before* the
   key/value regex by the parser because their payloads contain ``k=v`` pairs.
4. **Error / warning catalogue** (:attr:`WireKind.ERROR`, :attr:`WireKind.WARN`)
   — ``HPX_ERROR=<code> …`` and ``HPX_WARN=<code> …``; see
   :class:`FirmwareErrorCode` and :class:`FirmwareWarnCode`.
5. **Power terminal record** (:attr:`WireKind.TERMINAL`) — the versioned
   envelope between ``--- HPX_POWER_TERMINAL_START/END ---``, which is the
   *entire* output of the dedicated power binary. See :class:`PowerTerminalKey`.

A sixth shape, the per-layer CSV body, carries no ``HPX_`` token of its own and
so gets no per-key spec; its per-engine grammar is documented in
:data:`CSV_GRAMMAR` and pinned by the census contracts.

The power-binary rule
---------------------

``hpx_printf`` compiles to an empty function when ``power_only=true``, so the
dedicated power binary emits **only** the terminal record — every
transport-stream token above is either template-excluded or present-but-silent
there. :attr:`WireSpec.binary` states that runtime truth
(:attr:`WireBinary.TRANSPORT` vs :attr:`WireBinary.POWER`);
:attr:`WireSpec.condition` is a different axis — it names the *template* gate
that decides whether the token's source text is rendered at all, which is what
the census can observe. A token can therefore be ``binary=TRANSPORT`` with
``condition=None`` (its source is in both binaries; only one of them can print).

The ``HPX_ERROR`` shadow
------------------------

``HPX_ERROR=<code>`` and ``HPX_WARN=<code>`` also satisfy the generic
``^HPX_(\\w+)=(.+)$`` key/value regex, so the parser incidentally stores
``meta_kv["error"]`` / ``meta_kv["warn"]`` holding the *payload* of the last
such line. Nothing reads them (``FirmwareMeta`` names neither), and the real
consumer is ``capture._raise_on_firmware_error``, which scans raw lines before
parsing. The shadow is harmless and deliberately left alone; it is recorded
here so a future reader does not mistake it for a real key.

Registry-documented gaps
------------------------

These are true of the shipped protocol and deliberately *not* fixed here (wire
bytes are frozen this phase):

* ExecuTorch's ``HPX_ARENA_SIZE`` counts only the planned arena — its method
  and temporary arenas are excluded, so the figure is not comparable with
  TFLM's single-arena number.
* The ``clean_window_begin`` heartbeat carries ``est_ms=0`` on every
  STIMER-timed fixed window — every apollo510 profile build, and therefore
  every ExecuTorch build — so the host's window-budget extension never fires
  there and falls back to the flat heartbeat timeout.
* ``HPX_VERSION`` is checked against :data:`HPX_PROTOCOL_VERSION` and then
  discarded — it never reaches ``FirmwareMeta`` or ``summary.json``.
* 6 of the 12 :class:`FirmwareErrorCode` members carry no host hint
  (:attr:`WireSpec.has_host_hint`); those failures reach the user with a
  generic message.
* ``HPX_POWER_SAMPLE_COUNT`` is accepted by the host envelope parser but no
  template emits it (:attr:`WireSpec.emitted_by_firmware` is ``False``).
* ``HPX_CONST_BLOB_LOADED region=… size=…`` looks like a key/value line but is
  space-separated, so the generic regex never matches it: it is
  :attr:`WireKind.RECORD` and reaches no consumer at all.

``docs/reference/wire-protocol.md`` is generated from this module by
``tools/gen_wire_protocol_reference.py`` and drift-tested.

The registry is split across ``_model`` (vocabulary) and three spec catalogues
(``_stream``, ``_faults``, ``_power``) purely to stay under the repository's
per-module size ceiling; ``helia_profiler.wire`` remains the single import
path and the single place the protocol is declared.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from ..engines import EngineType
from ._faults import ERROR_SPECS, WARN_SPECS
from ._model import (
    ALL_ENGINES,
    AOT_ENGINES,
    ET_ENGINES,
    HPX_END_SENTINEL,
    HPX_ERROR_PREFIX,
    HPX_GO_COMMAND,
    HPX_HEARTBEAT_PREFIX,
    HPX_ITER_SENTINEL_PATTERN,
    HPX_ITER_SENTINEL_PREFIX,
    HPX_ITER_SENTINEL_RE,
    HPX_POWER_PREFIX,
    HPX_PREFIX,
    HPX_PRESET_SENTINEL_PATTERN,
    HPX_PRESET_SENTINEL_PREFIX,
    HPX_PRESET_SENTINEL_RE,
    HPX_PROTOCOL_VERSION,
    HPX_READY_LINE,
    HPX_START_SENTINEL,
    HPX_WARN_PREFIX,
    KEY_VALUE_PATTERN,
    KEY_VALUE_RE,
    POWER_BINARY_ENGINES,
    POWER_TERMINAL_END_SENTINEL,
    POWER_TERMINAL_START_SENTINEL,
    POWER_TERMINAL_VERSION,
    TFLM_ENGINES,
    FirmwareErrorCode,
    FirmwareWarnCode,
    HeartbeatPhase,
    PowerTerminalKey,
    WireBinary,
    WireConsumer,
    WireCriticality,
    WireDirection,
    WireKey,
    WireKind,
    WireSpec,
    error_token,
    heartbeat_token,
    warn_token,
)
from ._power import TERMINAL_SPECS
from ._stream import (
    CLEAN_WINDOW_SPECS,
    HANDSHAKE_SPECS,
    HEARTBEAT_SPECS,
    MODEL_MEMORY_SPECS,
    PSRAM_SPECS,
    SENTINEL_SPECS,
    START_HEADER_SPECS,
)


_ALL_SPECS: tuple[WireSpec, ...] = (
    SENTINEL_SPECS
    + HANDSHAKE_SPECS
    + START_HEADER_SPECS
    + MODEL_MEMORY_SPECS
    + PSRAM_SPECS
    + CLEAN_WINDOW_SPECS
    + HEARTBEAT_SPECS
    + ERROR_SPECS
    + WARN_SPECS
    + TERMINAL_SPECS
)

#: Every wire token, keyed by the literal token the firmware prints.
WIRE_REGISTRY: Mapping[str, WireSpec] = MappingProxyType(
    {spec.token: spec for spec in _ALL_SPECS}
)

#: Reverse index for the key/value grammar.
KEY_SPECS: Mapping[WireKey, WireSpec] = MappingProxyType(
    {spec.key: spec for spec in _ALL_SPECS if spec.key is not None}
)

#: Every declarative template gate used by a spec.
WIRE_CONDITIONS: frozenset[str] = frozenset(
    condition
    for spec in _ALL_SPECS
    for condition in (
        spec.condition,
        *spec.engine_conditions.values(),
    )
    if condition is not None
)

#: Required envelope fields — ``capture.power_terminal`` derives its schema
#: from these rather than restating them.
POWER_TERMINAL_REQUIRED_KEYS: frozenset[str] = frozenset(
    spec.token
    for spec in _ALL_SPECS
    if spec.kind is WireKind.TERMINAL and spec.required is True
)

#: Optional measurement fields (all-or-none as a group).
POWER_TERMINAL_OPTIONAL_KEYS: frozenset[str] = frozenset(
    spec.token
    for spec in _ALL_SPECS
    if spec.kind is WireKind.TERMINAL and spec.required is False
)


def spec_for(key: WireKey) -> WireSpec:
    """The spec for one key/value key."""
    return KEY_SPECS[key]


def specs_of_kind(kind: WireKind) -> tuple[WireSpec, ...]:
    """Every spec of one grammar, in registry order."""
    return tuple(spec for spec in _ALL_SPECS if spec.kind is kind)


#: The per-layer CSV body, per engine. Every engine prints the same header
#: shape — ``"Layer","Op"``, one quoted column per enabled counter, then
#: ``"overflow"`` — and differs only in how the Op column identifies a layer.
#: Counter columns fall back to ``"0x%04lx"`` (the raw event id) when the pass
#: supplied no name, and non-Armv8-M parts print the single DWT cycle counter
#: as ``"ARM_PMU_CPU_CYCLES"``.
CSV_GRAMMAR: Mapping[str, str] = MappingProxyType(
    {
        EngineType.TFLM.value: (
            'rows are `<index>,<tag>,<counters...>,<overflow>`; the tag is '
            "TFLM's per-op tag string (or `?`)."
        ),
        EngineType.HELIA_RT.value: "Identical to tflm — same template, same profiler class.",
        EngineType.HELIA_AOT.value: (
            "rows are `<index>,<OP_TYPE>:<op_id>,<counters...>,<overflow>`; the "
            "operator id disambiguates repeated op types."
        ),
        EngineType.EXECUTORCH.value: (
            "rows are `<index>,OPERATOR_CALL|DELEGATE_CALL:c<chain>i<instr>,"
            "<counters...>,<overflow>`."
        ),
    }
)


__all__ = [
    "ALL_ENGINES",
    "AOT_ENGINES",
    "CSV_GRAMMAR",
    "ET_ENGINES",
    "FirmwareErrorCode",
    "FirmwareWarnCode",
    "HPX_END_SENTINEL",
    "HPX_ERROR_PREFIX",
    "HPX_GO_COMMAND",
    "HPX_HEARTBEAT_PREFIX",
    "HPX_ITER_SENTINEL_PATTERN",
    "HPX_ITER_SENTINEL_PREFIX",
    "HPX_ITER_SENTINEL_RE",
    "HPX_POWER_PREFIX",
    "HPX_PREFIX",
    "HPX_PRESET_SENTINEL_PATTERN",
    "HPX_PRESET_SENTINEL_PREFIX",
    "HPX_PRESET_SENTINEL_RE",
    "HPX_PROTOCOL_VERSION",
    "HPX_READY_LINE",
    "HPX_START_SENTINEL",
    "HPX_WARN_PREFIX",
    "HeartbeatPhase",
    "KEY_SPECS",
    "KEY_VALUE_PATTERN",
    "KEY_VALUE_RE",
    "POWER_BINARY_ENGINES",
    "POWER_TERMINAL_END_SENTINEL",
    "POWER_TERMINAL_OPTIONAL_KEYS",
    "POWER_TERMINAL_REQUIRED_KEYS",
    "POWER_TERMINAL_START_SENTINEL",
    "POWER_TERMINAL_VERSION",
    "PowerTerminalKey",
    "TFLM_ENGINES",
    "WIRE_CONDITIONS",
    "WIRE_REGISTRY",
    "WireBinary",
    "WireConsumer",
    "WireCriticality",
    "WireDirection",
    "WireKey",
    "WireKind",
    "WireSpec",
    "error_token",
    "heartbeat_token",
    "spec_for",
    "specs_of_kind",
    "warn_token",
]
