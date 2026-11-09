"""heliaAOT firmware: the input fill and the digests that compare runs byte for byte.

The rendered helpers and the end-of-capture block are compiled and run on the
host; their output is checked against the same computation in Python.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_template_render import _render_aot

_SEED = 0x48505831


def _lcg(size: int, seed: int) -> bytes:
    state, out = seed, bytearray()
    for _ in range(size):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        out.append(state >> 24)
    return bytes(out)


def _fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return value


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def _run(tmp_path: Path, program: str) -> list[str]:
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("host C++ compiler required")
    path = tmp_path / "io.cc"
    path.write_text(program, encoding="utf-8")
    executable = tmp_path / "io.exe"
    subprocess.run(
        [
            compiler,
            "-std=gnu++17",
            "-Wall",
            "-Werror",
            "-Wno-unused-function",
            "-Wno-unused-variable",
            str(path),
            "-o",
            str(executable),
        ],
        check=True,
    )
    result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


_PREAMBLE = r"""
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
static void hpx_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::vprintf(fmt, ap);
    va_end(ap);
}
static void hpx_rtt_set_blocking(void) {}
struct Tensor { void *data; size_t size; };
static uint8_t input0[4];
static uint8_t output1[1025];
static struct { Tensor inputs[1]; Tensor outputs[2]; } fake_model_ctx = {
    {{input0, sizeof(input0)}},
    {{input0, 3}, {output1, sizeof(output1)}},
};
static const int fake_num_inputs = 1;
static const int fake_num_outputs = 2;
"""


@pytest.mark.host_integration
def test_lcg_fill_and_fnv_digest_match_their_definitions(tmp_path: Path):
    source = _render_aot(transport="rtt", input_fill="lcg")
    program = (
        _PREAMBLE
        + _function(source, "static void hpx_fill_lcg(")
        + _function(source, "static uint64_t hpx_fnv1a64(")
        + r"""
int main() {
    static uint8_t bytes[37];
    hpx_fill_lcg(bytes, sizeof(bytes), 0x48505831U + 1U);
    for (size_t i = 0; i < sizeof(bytes); i++) std::printf("%02x", (unsigned)bytes[i]);
    std::printf("\n%016llx\n", (unsigned long long)hpx_fnv1a64(bytes, sizeof(bytes)));
    return 0;
}
"""
    )
    expected = _lcg(37, _SEED + 1)
    assert _run(tmp_path, program) == [expected.hex(), f"{_fnv1a64(expected):016x}"]


@pytest.mark.host_integration
def test_capture_ends_with_the_output_bytes_then_the_inputs_every_run_saw(tmp_path: Path):
    """Output 0 shares input 0's bytes, as an in-place module's can: its hex
    is printed before the inputs are refilled for their digest. An output
    over 1024 bytes prints no hex."""
    source = _render_aot(transport="rtt", input_fill="lcg")
    start = source.index("    // The last run's outputs in full")
    block = source[start : source.index("    if (profiled_infer_count > 0", start)]
    program = (
        _PREAMBLE
        + _function(source, "static void hpx_fill_lcg(")
        + _function(source, "static uint64_t hpx_fnv1a64(")
        + "int main() {\n"
        + "    const uint8_t last[4] = {0x01, 0x02, 0xab, 0x7f};\n"
        + "    std::memcpy(input0, last, sizeof(last));\n"
        + block
        + "    return 0;\n}\n"
    )
    assert _run(tmp_path, program) == [
        "HPX_OUTPUT_0_HEX=0102ab",
        f"HPX_INPUT_0_FNV1A64={_fnv1a64(_lcg(4, _SEED)):016x}",
    ]


def test_every_input_refill_follows_the_fill_setting():
    zero = _render_aot(transport="rtt", input_fill="zero")
    lcg = _render_aot(transport="rtt", input_fill="lcg")
    refill = "memset(fake_model_ctx.inputs[i].data, 0,"
    sites = zero.count(refill)
    assert sites >= 4
    assert lcg.count(refill) == 0
    assert len(re.findall(r"hpx_fill_lcg\(fake_model_ctx\.inputs\[i\]\.data,", lcg)) == sites
    assert "hpx_fill_lcg" not in zero
    # The clean window's workload identity names a zero refill, so it is
    # declared only for that fill.
    assert '#define HPX_CLEAN_WORKLOAD "aot_raw_zero_refill_included_v1"' in zero
    assert "HPX_CLEAN_WORKLOAD" not in lcg


def test_every_instrumented_run_prints_its_output_digests_outside_timing():
    source = _render_aot(transport="rtt")
    call = source.index("        hpx_print_output_digests();")
    assert source.rindex("profiled_infer_count++;", 0, call) < call
    assert source.rindex("profiler_print_csv();", 0, call) < call
    assert source.count("hpx_print_output_digests();") == 1
    power = _render_aot(transport="rtt", power_only=True, power_window_timer="stimer")
    assert "FNV1A64" not in power
