"""The power binary's three STIMER brackets, pinned on the rendered source (#299).

The whole window, the INA228 accumulation interval and the GPIO gate are read
from one STIMER in that nesting order, so the host can require
gate <= accumulation <= whole window without tolerances. Each published number
divides by the bracket that matches it, which only holds while the reads stay
in this order and the gate reads stay beside the edges they time.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

from helia_profiler.wire import POWER_TERMINAL_VERSION

from .test_firmware_render_snapshots import _PROBES, _power_combos, _render
from .test_wire_protocol import _INA228_VARS

_TEMPLATES = (
    Path(__file__).resolve().parents[2] / "src" / "helia_profiler" / "firmware" / "templates"
)

_CASES = [
    pytest.param(
        soc,
        transport,
        engine,
        probe,
        ina228,
        id=f"{soc}|{transport}|{engine}|{probe}|{'ina228' if ina228 else 'no-monitor'}",
    )
    for soc, transport, engine in _power_combos()
    for probe in _PROBES
    for ina228 in (False, True)
]


def _power_render(soc: str, transport: str, engine: str, probe: str, ina228: bool) -> str:
    return _render(
        soc,
        transport,
        engine,
        power_only=True,
        clean_window_probe=probe,
        overrides=dict(_INA228_VARS) if ina228 else None,
    )


def _window(rendered: str) -> list[str]:
    """Code lines from the whole-window open to the gate interval's computation."""
    lines = [line.strip() for line in rendered.splitlines()]
    start = lines.index("uint32_t clean_stimer_t0 = hpx_stimer_ticks();")
    end = next(
        i
        for i in range(start, len(lines))
        if lines[i].startswith("uint64_t clean_stimer_gate_us =")
    )
    return [line for line in lines[start : end + 2] if line and not line.startswith("//")]


def _index(lines: list[str], text: str) -> int:
    hits = [i for i, line in enumerate(lines) if text in line]
    assert len(hits) == 1, f"expected one line containing {text!r}, found {len(hits)}"
    return hits[0]


@pytest.mark.parametrize(("soc", "transport", "engine", "probe", "ina228"), _CASES)
def test_brackets_nest_in_read_order(soc, transport, engine, probe, ina228):
    window = _window(_power_render(soc, transport, engine, probe, ina228))
    order = ["uint32_t clean_stimer_t0 = hpx_stimer_ticks();"]
    if ina228:
        order += ["hpx_ina228_window_begin()", "uint32_t clean_stimer_acc_t0 = hpx_stimer_ticks();"]
    order += [
        "uint32_t clean_stimer_gate_t0 = hpx_stimer_ticks();",
        "hpx_sync_window_begin();",
        "hpx_sync_window_end();",
        "uint32_t clean_stimer_gate_t1 = hpx_stimer_ticks();",
    ]
    if ina228:
        order += ["g_hpx_ina228_window_us = hpx_stimer_ticks_to_us(", "hpx_ina228_window_end()"]
    order += ["uint64_t clean_stimer_total_us ="]
    positions = [_index(window, text) for text in order]
    assert positions == sorted(positions), list(zip(order, positions, strict=True))


@pytest.mark.parametrize(("soc", "transport", "engine", "probe", "ina228"), _CASES)
def test_gate_reads_sit_beside_the_edges_and_never_inside(soc, transport, engine, probe, ina228):
    window = _window(_power_render(soc, transport, engine, probe, ina228))
    begin = _index(window, "hpx_sync_window_begin();")
    end = _index(window, "hpx_sync_window_end();")
    assert window[begin - 1] == "uint32_t clean_stimer_gate_t0 = hpx_stimer_ticks();"
    assert window[end + 1] == "uint32_t clean_stimer_gate_t1 = hpx_stimer_ticks();"
    assert not any("hpx_stimer_ticks()" in line for line in window[begin + 1 : end])


@pytest.mark.parametrize(("soc", "transport", "engine", "probe", "ina228"), _CASES)
def test_accumulation_interval_is_the_measurement_duration(soc, transport, engine, probe, ina228):
    rendered = _power_render(soc, transport, engine, probe, ina228)
    window = _window(rendered)
    gate = _index(window, "uint64_t clean_stimer_gate_us = hpx_stimer_ticks_to_us(")
    assert window[gate + 1] == "(uint64_t)(uint32_t)(clean_stimer_gate_t1 - clean_stimer_gate_t0));"
    if ina228:
        acc = _index(window, "g_hpx_ina228_window_us = hpx_stimer_ticks_to_us(")
        assert window[acc + 1] == "(uint64_t)(uint32_t)(hpx_stimer_ticks() - clean_stimer_acc_t0));"
        # The measurement payload's second value is MEASUREMENT_DURATION_US.
        payload = rendered[rendered.index('"HPX_POWER_MEASUREMENT_SOURCE=ina228\\n"') :]
        args = re.findall(r"\(unsigned long long\)(\w+),", payload)
        assert args[:2] == ["g_hpx_ina228_energy_nj", "g_hpx_ina228_window_us"]
    else:
        assert "g_hpx_ina228_window_us" not in rendered


@pytest.mark.parametrize(("soc", "transport", "engine", "probe", "ina228"), _CASES)
def test_success_report_passes_window_then_gate(soc, transport, engine, probe, ina228):
    rendered = _power_render(soc, transport, engine, probe, ina228)
    call = rendered[rendered.rindex("hpx_power_terminal_report(\n") :]
    call = call[: call.index(");")]
    values = [line.strip().rstrip(",") for line in call.splitlines()[1:]]
    assert values[3:5] == ["clean_stimer_total_us", "clean_stimer_gate_us"]


@pytest.mark.parametrize("ina228", [False, True], ids=["no-monitor", "ina228"])
def test_envelope_orders_keys_and_values_alike(ina228):
    rendered = _power_render("apollo510", "rtt", "tflm", "infer", ina228)
    body = rendered[rendered.index("static void hpx_power_terminal_report(") :]
    body = body[: body.index("static void hpx_power_terminal_fail(")]
    keys = re.findall(r'"HPX_POWER_(ELAPSED_US|GATE_ELAPSED_US)=%llu\\n"', body)
    values = re.findall(r"\(unsigned long long\)(elapsed_us|gate_elapsed_us),", body)
    assert keys == ["ELAPSED_US", "GATE_ELAPSED_US"]
    assert values == ["elapsed_us", "gate_elapsed_us"]


@pytest.mark.parametrize("ina228", [False, True], ids=["no-monitor", "ina228"])
def test_failure_envelope_reports_zero_for_both_intervals(ina228):
    rendered = _power_render("apollo510", "rtt", "tflm", "infer", ina228)
    fail = rendered[rendered.index("static void hpx_power_terminal_fail(") :]
    call = fail[fail.index("hpx_power_terminal_report(\n") :]
    call = call[: call.index(");")]
    values = [line.strip().rstrip(",") for line in call.splitlines()[1:]]
    # success, requested, completed, elapsed, gate.
    assert values[0] == "false"
    assert values[2:5] == ["0U", "0ULL", "0ULL"]


@pytest.mark.parametrize("ina228", [False, True], ids=["no-monitor", "ina228"])
def test_envelope_version_is_the_parser_version(ina228):
    rendered = _power_render("apollo510", "rtt", "tflm", "infer", ina228)
    assert re.findall(r"HPX_POWER_TERMINAL_VERSION=(\w+)\\n", rendered) == [
        str(POWER_TERMINAL_VERSION)
    ]


def test_templates_never_spell_the_envelope_version():
    for template in _TEMPLATES.glob("*.j2"):
        assert not re.search(
            r"HPX_POWER_TERMINAL_VERSION=\d", template.read_text(encoding="utf-8")
        ), template.name


def test_power_render_refuses_a_non_stimer_window():
    with pytest.raises(
        jinja2.UndefinedError, match="power binaries must time their clean window with STIMER"
    ):
        _render(
            "apollo510",
            "rtt",
            "tflm",
            power_only=True,
            overrides={"power_window_timer": "dwt"},
        )
