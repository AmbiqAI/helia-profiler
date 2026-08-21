"""Generate docs/reference/wire-protocol.md from the wire registry.

The generated page is the authoritative description of the HPX wire protocol:
every token firmware and host exchange, with its grammar, engine and binary
scope, the condition that gates it, who consumes it and what losing it costs.
It is derived mechanically from ``helia_profiler.wire`` so it can never drift
from the firmware the census contracts hold to that registry. Regenerate after
any registry change:

    uv run python tools/gen_wire_protocol_reference.py

``tests/test_wire_protocol_reference_docs.py`` fails CI if the committed page
is stale relative to the registry.
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.engines import EngineType
from helia_profiler.wire import (
    ALL_ENGINES,
    CSV_GRAMMAR,
    EST_MS_GAP,
    POWER_TERMINAL_OPTIONAL_KEYS,
    POWER_TERMINAL_REQUIRED_KEYS,
    HeartbeatPhase,
    WireBinary,
    WireDirection,
    WireKind,
    WireSpec,
    specs_of_kind,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "docs" / "reference" / "wire-protocol.md"

_HEADER = """\
# Wire protocol

<!-- GENERATED FILE — do not edit by hand.
     Source: src/helia_profiler/wire/
     Regenerate: uv run python tools/gen_wire_protocol_reference.py -->

Every line the profiler firmware puts on a transport, and the one command the
host writes back, generated from the registry in `helia_profiler.wire`.
`tests/contracts/test_wire_protocol.py` renders the firmware templates across a
matrix of SoCs, transports, engines and binaries and asserts that what they
emit is exactly what this page says.

## How to read the tables

* **Scope** — which engines emit the token. `all` means every engine.
* **Condition** — the firmware render gate. `always` means the token's source
  is in every build within its scope. This is a *template* condition: the
  dedicated power binary compiles `hpx_printf` to a no-op, so a token whose
  source is present there still prints nothing (see the power-binary rule
  below).
* **Consumer** — what on the host reads it. `unconsumed` means firmware emits
  it and nothing currently reads it: honest provenance, not a bug.
* **Criticality** — `protocol` breaks capture or a verdict, `metric` silently
  degrades a reported number, `diagnostic` costs only diagnosability.

## The power-binary rule

`hpx_printf` compiles to an empty function when the dedicated power binary is
built, so that binary emits **only** the power terminal record. Every
transport-stream token below is either excluded from its source or present and
silent there.
"""

_KIND_SECTIONS: tuple[tuple[WireKind, str, str], ...] = (
    (
        WireKind.SENTINEL,
        "Sentinels",
        "Frame the stream. The parser ignores everything before the start "
        "sentinel and stops at the end sentinel.",
    ),
    (
        WireKind.HANDSHAKE,
        "Handshake lines",
        "Valueless lines that coordinate host and firmware around attach and "
        "model upload.",
    ),
    (
        WireKind.KEY_VALUE,
        "Metadata keys",
        "`HPX_<KEY>=<value>` lines under the start header, parsed by "
        "`^HPX_(\\w+)=(.+)$` into a key/value map with the key lower-cased.",
    ),
    (
        WireKind.KEY_VALUE_INDEXED,
        "Indexed metadata keys",
        "One printf format, one line per index — heliaAOT reports per-input "
        "and per-output sizes where the other engines report one static key.",
    ),
    (
        WireKind.RECORD,
        "Records",
        "Token-then-payload lines. The space after the token defeats the "
        "key/value regex, so these reach no consumer.",
    ),
    (
        WireKind.HEARTBEAT,
        "Heartbeat phases",
        "`HPX_HEARTBEAT phase=<phase> …` progress records. Any heartbeat "
        "refreshes the host's inactivity deadline; only `clean_window_begin` "
        "carries data the host acts on.",
    ),
    (
        WireKind.ERROR,
        "Error codes",
        "`HPX_ERROR=<code> …`. The host raises on the first one it sees, with "
        "a code-specific hint where it has one.",
    ),
    (
        WireKind.WARN,
        "Warning codes",
        "`HPX_WARN=<code> …`. Non-fatal; the run continues.",
    ),
    (
        WireKind.TERMINAL,
        "Power terminal record",
        "The dedicated power binary's entire output: a versioned envelope "
        "between its own start/end markers, optionally carrying an on-device "
        "measurement payload, preceded by monitor diagnostics the envelope "
        "parser ignores.",
    ),
)


#: Kinds this page narrates in prose instead of tabulating. The CSV body
#: carries no ``HPX_`` token, so no spec has that kind and ``_rows`` would emit
#: an empty table for it; its grammar is written out under "CSV body" instead.
_NARRATED_KINDS: frozenset[WireKind] = frozenset({WireKind.CSV})


def _check_every_kind_is_covered() -> None:
    """Fail loudly if a new :class:`WireKind` has no home on the page.

    Without this, adding a kind and forgetting to add a section here produces a
    page that is silently missing every token of that kind — the exact failure
    the generated-from-the-registry design exists to prevent, arriving as a
    quiet omission rather than an error.
    """
    covered = {kind for kind, _, _ in _KIND_SECTIONS} | _NARRATED_KINDS
    uncovered = sorted(kind.value for kind in set(WireKind) - covered)
    if uncovered:
        raise AssertionError(
            "these WireKinds have neither a table section nor prose on the "
            f"generated page: {uncovered}. Add them to _KIND_SECTIONS (or to "
            "_NARRATED_KINDS with the prose that covers them)."
        )


def _cell(text: str) -> str:
    """Escape a value for a Markdown table cell (pipes end columns)."""
    return text.replace("|", "\\|")


def _scope(spec: WireSpec) -> str:
    if spec.engines == ALL_ENGINES:
        return "all"
    return ", ".join(sorted(engine.value for engine in spec.engines))


def _condition(spec: WireSpec) -> str:
    parts: list[str] = []
    default = spec.condition or "always"
    if spec.engine_conditions:
        overridden = set(spec.engine_conditions)
        others = sorted(
            engine.value for engine in spec.engines if engine not in overridden
        )
        if others:
            parts.append(f"`{default}` ({', '.join(others)})")
        for engine in sorted(overridden, key=lambda e: e.value):
            value = spec.engine_conditions[engine] or "always"
            parts.append(f"`{value}` ({engine.value})")
    else:
        parts.append(f"`{default}`")
    if spec.runtime_gate:
        parts.append(f"runtime: {spec.runtime_gate}")
    return "; ".join(parts)


def _details(spec: WireSpec) -> str:
    parts: list[str] = [spec.description]
    if spec.literal and spec.kind is not WireKind.HANDSHAKE:
        parts.append(f"Line: `{spec.literal}`.")
    if spec.value_shape:
        parts.append(f"Value: `{spec.value_shape}`.")
    if spec.direction is WireDirection.HOST_TO_DEVICE:
        parts.append("Direction: host to device.")
    if spec.binary is WireBinary.POWER:
        parts.append("Power binary only.")
    if spec.kind is WireKind.ERROR:
        parts.append("Host hint: " + ("yes" if spec.has_host_hint else "**no**") + ".")
    if not spec.emitted_by_firmware:
        parts.append("**No template emits this.**")
    if spec.note:
        parts.append(spec.note)
    return " ".join(parts)


def _rows(kind: WireKind) -> list[str]:
    rows = ["| Token | Scope | Condition | Consumer | Criticality | Notes |",
            "| --- | --- | --- | --- | --- | --- |"]
    for spec in specs_of_kind(kind):
        rows.append(
            f"| `{spec.token}` | {_cell(_scope(spec))} | {_cell(_condition(spec))} | "
            f"`{spec.consumer.value}` | `{spec.criticality.value}` | "
            f"{_cell(_details(spec))} |"
        )
    return rows


def render() -> str:
    _check_every_kind_is_covered()
    lines: list[str] = [_HEADER]

    for kind, title, blurb in _KIND_SECTIONS:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(blurb)
        lines.append("")
        lines.extend(_rows(kind))
        lines.append("")

    lines.append("### Power terminal key groups")
    lines.append("")
    lines.append(
        "The envelope's required fields must all be present or the record is "
        "rejected. The measurement fields are an all-or-none group, emitted "
        "only for a successful window read from an on-target monitor."
    )
    lines.append("")
    required = ", ".join(f"`{key}`" for key in sorted(POWER_TERMINAL_REQUIRED_KEYS))
    optional = ", ".join(f"`{key}`" for key in sorted(POWER_TERMINAL_OPTIONAL_KEYS))
    lines.append(f"- **Required:** {required}")
    lines.append(f"- **Optional (all-or-none):** {optional}")
    lines.append("")

    lines.append("## CSV body")
    lines.append("")
    lines.append(
        "The per-layer rows between two iteration sentinels carry no `HPX_` "
        "token of their own. Every engine prints the same header shape — "
        '`"Layer","Op"`, one quoted column per enabled counter, then '
        '`"overflow"` — and differs only in how the Op column identifies a '
        "layer. Counter columns fall back to the raw event id (`\"0x%04lx\"`) "
        "when a pass supplied no name, and parts without an Armv8-M PMU print "
        'the single cycle counter as `"ARM_PMU_CPU_CYCLES"`.'
    )
    lines.append("")
    for engine in EngineType:
        grammar = CSV_GRAMMAR.get(engine.value)
        if grammar:
            lines.append(f"- **{engine.value}** — {grammar}")
    lines.append("")

    lines.append("## Heartbeat phases")
    lines.append("")
    lines.append(
        "The complete phase vocabulary — see the table above for which "
        "engines emit which, and when: "
        + ", ".join(f"`{phase.value}`" for phase in HeartbeatPhase)
        + "."
    )
    lines.append("")

    lines.append("## Documented gaps")
    lines.append("")
    lines.append(
        "These are true of the shipped protocol and recorded rather than "
        "silently fixed; changing any of them changes wire bytes."
    )
    lines.append("")
    for gap in _GAPS:
        lines.append(f"- {gap}")
    lines.append("")
    return "\n".join(lines)


_GAPS = (
    "ExecuTorch's `HPX_ARENA_SIZE` counts only the planned arena — its method "
    "and temporary arenas are excluded, so the figure is not comparable with "
    "TFLM's single-arena number.",
    EST_MS_GAP,
    "`HPX_VERSION` is checked against the expected protocol version and then "
    "discarded: it never reaches `FirmwareMeta` or `summary.json`.",
    "Six of the twelve error codes carry no host hint, so those failures reach "
    "the user with a generic message.",
    "`HPX_POWER_SAMPLE_COUNT` is accepted by the host envelope parser but no "
    "template emits it, so the on-device summary's sample count is always "
    "absent.",
    "`HPX_CONST_BLOB_LOADED region=… size=…` looks like a metadata key but is "
    "space-separated, so the generic key/value regex never matches it and it "
    "reaches no consumer.",
    "`HPX_ERROR=` and `HPX_WARN=` lines also satisfy the generic key/value "
    "regex, so the parser incidentally stores the payload of the last one "
    "under the keys `error` and `warn`. Nothing reads them.",
)


def main() -> None:
    OUTPUT_PATH.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
