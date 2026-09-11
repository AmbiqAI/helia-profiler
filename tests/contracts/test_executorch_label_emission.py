"""Contract: the ExecuTorch per-layer row emitter prints the documented label.

``test_wire_protocol`` pins the emitter's SOURCE text; this test RUNS it.
The rendered ``print_layers()`` is lifted verbatim from the real template
and executed on the host GNU g++ against the compile stubs with a capturing
``hpx_printf``, so the ``%s%s%s`` argument order and the ``.overload``
condition are checked on the bytes a host would parse (#301) — a named
kernel, a named delegate, an empty overload, and both nullptr fallbacks.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Importing the render-compile gate reuses its GNU g++ probe and inherits
# its module-level skip on hosts without one (clang and MinGW rejected).
from .test_render_compile import _GXX, _STUB_DIR, _render

_PRINT_LAYERS = re.compile(r"^static void print_layers\(void\) \{\n.*?^\}\n", re.M | re.S)

_HARNESS = r"""
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <string>

#include "nsx_executorch.h"

static std::string g_out;
static void hpx_printf(const char *fmt, ...) {
    char buf[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    g_out += buf;
}

// Mirrors the template's record and globals (main_executorch.cc.j2
// engine_globals); only print_layers() itself is the code under test.
static constexpr int kMaxLayers = 8;
static constexpr int kMaxCounters = 4;
struct LayerRecord {
    nsx::executorch::OperatorEvent event;
    uint32_t counters[kMaxCounters];
    bool overflow;
};
static LayerRecord g_layers[kMaxLayers];
static const char *g_counter_names[kMaxCounters] = {"ARM_PMU_CPU_CYCLES"};
static int g_num_layers = 0;
static int g_num_counters = 1;

@@PRINT_LAYERS@@

static void add(nsx::executorch::OperatorKind kind, int32_t chain, uint32_t instr,
                const char *name, const char *overload, uint32_t cycles, bool overflow) {
    LayerRecord &r = g_layers[g_num_layers++];
    r.event.kind = kind;
    r.event.chain_index = chain;
    r.event.instruction_index = instr;
    r.event.name = name;
    r.event.overload = overload;
    r.counters[0] = cycles;
    r.overflow = overflow;
}

int main() {
    using nsx::executorch::OperatorKind;
    add(OperatorKind::kKernel, 0, 1, "cortex_m::quantized_conv2d", "out", 1015742, false);
    add(OperatorKind::kDelegate, 0, 2, "XnnpackBackend", nullptr, 7, true);
    add(OperatorKind::kKernel, 1, 0, "my_ops::thing", "", 3, false);
    // Unnamed: the kind-only fallback, and a stray overload must not print.
    add(OperatorKind::kKernel, 0, 3, nullptr, "out", 4, false);
    add(OperatorKind::kDelegate, 2, 5, nullptr, nullptr, 5, false);
    print_layers();
    fputs(g_out.c_str(), stdout);
    return 0;
}
"""

_EXPECTED = (
    '"Layer","Op","ARM_PMU_CPU_CYCLES","overflow"\n'
    "0,cortex_m::quantized_conv2d.out:c0i1,1015742,0\n"
    "1,XnnpackBackend:c0i2,7,1\n"
    "2,my_ops::thing:c1i0,3,0\n"
    "3,OPERATOR_CALL:c0i3,4,0\n"
    "4,DELEGATE_CALL:c2i5,5,0\n"
)


def test_executorch_row_emitter_prints_the_documented_labels(tmp_path: Path):
    rendered = _render("apollo510", "rtt", "executorch")
    match = _PRINT_LAYERS.search(rendered)
    assert match is not None, "print_layers() not found in the rendered ExecuTorch template"

    source = tmp_path / "emit.cc"
    source.write_text(_HARNESS.replace("@@PRINT_LAYERS@@", match.group(0)), encoding="utf-8")
    binary = tmp_path / "emit"
    assert _GXX is not None
    compiled = subprocess.run(
        [
            _GXX,
            "-std=gnu++17",
            "-Wall",
            "-Werror",
            "-Wformat",
            f"-I{_STUB_DIR}",
            str(source),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
    )
    assert compiled.returncode == 0, compiled.stderr

    run = subprocess.run([str(binary)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert run.stdout == _EXPECTED
