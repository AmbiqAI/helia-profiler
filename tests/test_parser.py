"""Tests for capture/parser.py — multi-pass parsing and per-group merging."""

from __future__ import annotations

from helia_profiler.capture.parser import parse_firmware_output, _infer_group


# ---------------------------------------------------------------------------
# Helpers to build synthetic HPX protocol streams
# ---------------------------------------------------------------------------


def _make_preset_block(
    name: str,
    header: list[str],
    rows: list[list[str]],
    iterations: int = 1,
) -> list[str]:
    """Build HPX protocol lines for a single preset with *iterations* repeats."""
    lines = [f"--- HPX_PRESET {name} ---"]
    for it in range(iterations):
        lines.append(f"--- HPX_ITER {it} ---")
        lines.append(",".join(header))
        for row in rows:
            lines.append(",".join(row))
    return lines


def _wrap_session(
    meta: dict[str, str],
    preset_blocks: list[list[str]],
) -> list[str]:
    """Wrap preset blocks in HPX_START / HPX_END with metadata."""
    lines = ["--- HPX_START ---"]
    for k, v in meta.items():
        lines.append(f"HPX_{k.upper()}={v}")
    for block in preset_blocks:
        lines.extend(block)
    lines.append("--- HPX_END ---")
    return lines


# ---------------------------------------------------------------------------
# _infer_group
# ---------------------------------------------------------------------------


def test_infer_group_new_style():
    assert _infer_group("mve_0") == "mve"
    assert _infer_group("mve_1") == "mve"
    assert _infer_group("cpu_0") == "cpu"
    assert _infer_group("memory_2") == "memory"


def test_infer_group_legacy():
    assert _infer_group("basic_cpu") == "basic_cpu"
    assert _infer_group("mve") == "mve"
    assert _infer_group("memory") == "memory"


# ---------------------------------------------------------------------------
# Single-preset parsing (legacy)
# ---------------------------------------------------------------------------


def test_single_preset_basic():
    header = ["Layer", "Op", "ARM_PMU_CPU_CYCLES"]
    rows = [
        ["0", "CONV_2D", "1000"],
        ["1", "DEPTHWISE_CONV_2D", "2000"],
    ]
    lines = _wrap_session(
        {"presets": "basic_cpu", "model_size": "1024"},
        [_make_preset_block("basic_cpu", header, rows)],
    )
    result = parse_firmware_output(lines)

    assert result.meta.model_size == 1024
    assert "basic_cpu" in result.presets
    assert len(result.layers) == 2
    assert result.layers[0].cycles == 1000
    assert result.layers[1].cycles == 2000
    assert not result.overflow_detected


def test_target_profiled_infer_timing_metadata():
    lines = _wrap_session(
        {
            "presets": "basic_cpu",
            "profiled_infer_count": "6",
            "profiled_infer_total_us": "48000",
            "profiled_infer_avg_us": "8000",
        },
        [_make_preset_block("basic_cpu", ["Layer", "Op", "ARM_PMU_CPU_CYCLES"], [["0", "CONV_2D", "1000"]])],
    )

    result = parse_firmware_output(lines)

    assert result.meta.profiled_infer_count == 6
    assert result.meta.profiled_infer_total_us == 48000
    assert result.meta.profiled_infer_avg_us == 8000


def test_clean_infer_timing_metadata():
    lines = _wrap_session(
        {
            "presets": "basic_cpu",
            "clean_infer_count": "5",
            "clean_infer_total_cycles": "10000",
            "clean_infer_avg_cycles": "2000",
            "clean_infer_avg_us": "21",
        },
        [_make_preset_block("basic_cpu", ["Layer", "Op", "ARM_PMU_CPU_CYCLES"], [["0", "CONV_2D", "1000"]])],
    )

    result = parse_firmware_output(lines)

    assert result.meta.clean_infer_count == 5
    assert result.meta.clean_infer_total_cycles == 10000
    assert result.meta.clean_infer_avg_cycles == 2000
    assert result.meta.clean_infer_avg_us == 21


def test_system_clock_hz_metadata():
    lines = _wrap_session(
        {"presets": "basic_cpu", "system_clock_hz": "48000000"},
        [_make_preset_block("basic_cpu", ["Layer", "Op", "ARM_PMU_CPU_CYCLES"], [["0", "CONV_2D", "1000"]])],
    )

    result = parse_firmware_output(lines)

    assert result.meta.system_clock_hz == 48000000


# ---------------------------------------------------------------------------
# Multi-pass parsing (new-style)
# ---------------------------------------------------------------------------


def test_multi_pass_same_group_merged():
    """Two MVE passes should merge into a single 'mve' group."""
    header_a = ["Layer", "Op", "ARM_PMU_MVE_INST_RETIRED"]
    rows_a = [["0", "CONV_2D", "500"], ["1", "ADD", "100"]]

    header_b = ["Layer", "Op", "ARM_PMU_MVE_STALL"]
    rows_b = [["0", "CONV_2D", "50"], ["1", "ADD", "10"]]

    lines = _wrap_session(
        {"presets": "mve_0,mve_1"},
        [
            _make_preset_block("mve_0", header_a, rows_a),
            _make_preset_block("mve_1", header_b, rows_b),
        ],
    )
    result = parse_firmware_output(lines)

    assert "mve" in result.groups
    mve_layers = result.groups["mve"]
    assert len(mve_layers) == 2
    # Both counters should be present in the merged layers
    assert mve_layers[0].counters["ARM_PMU_MVE_INST_RETIRED"] == 500
    assert mve_layers[0].counters["ARM_PMU_MVE_STALL"] == 50
    assert mve_layers[1].counters["ARM_PMU_MVE_INST_RETIRED"] == 100
    assert mve_layers[1].counters["ARM_PMU_MVE_STALL"] == 10


def test_multi_group_separate():
    """cpu_0 and mve_0 should produce separate groups."""
    header_cpu = ["Layer", "Op", "ARM_PMU_CPU_CYCLES"]
    rows_cpu = [["0", "CONV_2D", "3000"]]

    header_mve = ["Layer", "Op", "ARM_PMU_MVE_INST_RETIRED"]
    rows_mve = [["0", "CONV_2D", "800"]]

    lines = _wrap_session(
        {"presets": "cpu_0,mve_0"},
        [
            _make_preset_block("cpu_0", header_cpu, rows_cpu),
            _make_preset_block("mve_0", header_mve, rows_mve),
        ],
    )
    result = parse_firmware_output(lines)

    assert "cpu" in result.groups
    assert "mve" in result.groups
    assert result.groups["cpu"][0].counters["ARM_PMU_CPU_CYCLES"] == 3000
    assert result.groups["mve"][0].counters["ARM_PMU_MVE_INST_RETIRED"] == 800

    # Merged layers (all-groups) should have both counters
    assert result.layers[0].counters["ARM_PMU_CPU_CYCLES"] == 3000
    assert result.layers[0].counters["ARM_PMU_MVE_INST_RETIRED"] == 800


# ---------------------------------------------------------------------------
# Iteration averaging
# ---------------------------------------------------------------------------


def test_iteration_averaging():
    """Multiple iterations should be averaged."""
    header = ["Layer", "Op", "ARM_PMU_CPU_CYCLES"]
    rows = [["0", "CONV_2D", "1000"]]
    # Build manually with 2 iterations where the second has a different value
    lines = [
        "--- HPX_START ---",
        "HPX_PRESETS=basic_cpu",
        "--- HPX_PRESET basic_cpu ---",
        "--- HPX_ITER 0 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES",
        "0,CONV_2D,1000",
        "--- HPX_ITER 1 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES",
        "0,CONV_2D,3000",
        "--- HPX_END ---",
    ]
    result = parse_firmware_output(lines)

    # Average of 1000 and 3000 = 2000
    assert result.layers[0].cycles == 2000


# ---------------------------------------------------------------------------
# Overflow detection
# ---------------------------------------------------------------------------


def test_overflow_detection():
    lines = [
        "--- HPX_START ---",
        "--- HPX_PRESET basic_cpu ---",
        "--- HPX_ITER 0 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES,overflow",
        "0,CONV_2D,999999,yes",
        "--- HPX_END ---",
    ]
    result = parse_firmware_output(lines)
    assert result.overflow_detected
    assert result.layers[0].overflow


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_session():
    lines = ["--- HPX_START ---", "--- HPX_END ---"]
    result = parse_firmware_output(lines)
    assert len(result.presets) == 0
    assert len(result.layers) == 0
    assert len(result.groups) == 0


def test_legacy_default_preset():
    """Stream with no HPX_PRESET marker should create _default preset."""
    lines = [
        "--- HPX_START ---",
        "--- HPX_ITER 0 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES",
        "0,CONV_2D,500",
        "--- HPX_END ---",
    ]
    result = parse_firmware_output(lines)
    assert "_default" in result.presets
    assert len(result.layers) == 1


def test_heartbeat_lines_are_ignored_by_csv_parser():
    """HPX_HEARTBEAT lines must not feed the CSV parser.

    Regression test: the heartbeat line ``HPX_HEARTBEAT phase=infer pass=0
    iter=0 layer=3`` starts with ``HPX_`` but is not a metadata ``KEY=val``
    line, and it appears mid-iteration.  If the parser treats it as a CSV
    row the preset ends up with spurious layers or malformed rows.
    """
    lines = [
        "--- HPX_START ---",
        "HPX_VERSION=1",
        "HPX_HEARTBEAT phase=init",
        "--- HPX_PRESET cpu ---",
        "--- HPX_ITER 0 ---",
        "Layer,Op,ARM_PMU_CPU_CYCLES,overflow",
        "0,CONV_2D,100,0",
        "HPX_HEARTBEAT phase=infer pass=0 iter=0 layer=1",
        "1,ADD,50,0",
        "HPX_HEARTBEAT phase=infer pass=0 iter=0 layer=2",
        "--- HPX_END ---",
    ]
    result = parse_firmware_output(lines)
    assert "cpu" in result.presets
    # Exactly 2 data rows; heartbeats did not create ghost layers.
    assert len(result.presets["cpu"].layers) == 2
    ops = [layer.op for layer in result.presets["cpu"].layers]
    assert ops == ["CONV_2D", "ADD"]
