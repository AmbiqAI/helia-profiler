"""Contract: the rendered hpx_pmu_profiler.cc behaves right when run on host.

The Tier 1 gate only syntax-checks renders. This module compiles the TFLM /
heliaRT profiler TU with host g++ against a tiny simulated PMU and runs it,
so two runtime properties are checked end to end:

* Layer capacity: ops past ``kMaxLayers`` must not touch recorded layers,
  and the profiler must report that capacity was exceeded.
* Overflow: NSX builds each 32-bit event from a 16-bit counter plus a
  16-bit CHAIN counter, and ``nsx_pmu_get_counters()`` clears PMOVSSET as
  part of its read. Overflow must be sampled before that read, and only a
  CHAIN (odd index) overflow truncates the logical count.

The simulated ``nsx_pmu_get_counters`` mirrors the NSX implementation: it
ends with ``nsx_pmu_reset_counters()``, which writes PMOVSCLR.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from helia_profiler.firmware import _jinja_env

_GXX = shutil.which("g++")
if _GXX is None:
    pytest.skip("no host g++", allow_module_level=True)

_STUB_DIR = Path(__file__).parent.parent / "fixtures" / "compile_stubs"

_SIM_DEVICE_H = r"""
#pragma once
#include <stdint.h>
#define __CORTEX_M 55U
typedef struct { volatile uint32_t CYCCNT; } HpxSimDwt;
extern HpxSimDwt hpx_sim_dwt;
#define DWT (&hpx_sim_dwt)
extern uint32_t hpx_sim_ovsset;
static inline uint32_t ARM_PMU_Get_CNTR_OVS(void) { return hpx_sim_ovsset; }
static inline void ARM_PMU_Set_CNTR_OVS(uint32_t mask) { hpx_sim_ovsset &= ~mask; }
"""

_SIM_PMU_H = r"""
#pragma once
#include <stdint.h>
#include "nsx_core.h"
#define NSX_PMU_MAX_COUNTERS 8
extern const nsx_core_api_t nsx_pmu_V1_0_0;
typedef enum {
    NSX_PMU_EVENT_COUNTER_SIZE_16 = 0,
    NSX_PMU_EVENT_COUNTER_SIZE_32 = 1,
} nsx_pmu_event_counter_size_e;
typedef struct { bool enabled; uint32_t eventId; nsx_pmu_event_counter_size_e counterSize; }
    nsx_pmu_event_t;
typedef struct { bool added; uint32_t mapIndex; uint32_t counterValue; } nsx_pmu_counter_t;
typedef struct nsx_pmu_config {
    const nsx_core_api_t *api;
    nsx_pmu_event_t events[NSX_PMU_MAX_COUNTERS];
    nsx_pmu_counter_t counter[NSX_PMU_MAX_COUNTERS];
} nsx_pmu_config_t;
typedef enum { NSX_PMU_PRESET_ML_DEFAULT = 3 } nsx_pmu_preset_e;
extern uint32_t hpx_sim_count;
static inline void nsx_pmu_event_create(nsx_pmu_event_t *e, uint32_t id,
                                        nsx_pmu_event_counter_size_e size) {
    e->enabled = true;
    e->eventId = id;
    e->counterSize = size;
}
static inline void nsx_pmu_reset_config(nsx_pmu_config_t *cfg) { *cfg = nsx_pmu_config_t{}; }
static inline uint32_t nsx_pmu_apply_preset(nsx_pmu_config_t *cfg, nsx_pmu_preset_e) {
    for (int i = 0; i < 4; ++i)
        nsx_pmu_event_create(&cfg->events[i], 0x11U + i, NSX_PMU_EVENT_COUNTER_SIZE_32);
    return 0U;
}
static inline uint32_t nsx_pmu_init(nsx_pmu_config_t *) { return 0U; }
// Mirrors NSX: a reset also writes PMOVSCLR.
static inline void nsx_pmu_reset_counters(void) { ARM_PMU_Set_CNTR_OVS(0xFFFFFFFFU); }
static inline uint32_t nsx_pmu_get_counters(nsx_pmu_config_t *cfg) {
    for (int i = 0; i < NSX_PMU_MAX_COUNTERS; ++i) cfg->counter[i].counterValue = hpx_sim_count;
    nsx_pmu_reset_counters();
    return 0U;
}
"""

_DRIVER_CC = r"""
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include "hpx_pmu_profiler.h"

HpxSimDwt hpx_sim_dwt;
uint32_t hpx_sim_ovsset;
uint32_t hpx_sim_count;
const nsx_core_api_t nsx_pmu_V1_0_0 = {};

void hpx_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
}

static HpxPmuProfiler g_profiler;

int main(int argc, char **argv) {
    g_profiler.Init();
    // Each arg is one op: "<cycles>:<ovsset>".
    for (int i = 1; i < argc; ++i) {
        unsigned long cycles = strtoul(argv[i], &argv[i], 10);
        unsigned long ovs = strtoul(argv[i] + 1, nullptr, 0);
        uint32_t handle = g_profiler.BeginEvent("op");
        hpx_sim_dwt.CYCCNT += (uint32_t)cycles;
        hpx_sim_count = (uint32_t)cycles;
        hpx_sim_ovsset |= (uint32_t)ovs;
        g_profiler.EndEvent(handle);
        hpx_sim_dwt.CYCCNT += 7U;
    }
    g_profiler.PrintCsv();
    printf("capacity_exceeded=%d\n", g_profiler.capacity_exceeded() ? 1 : 0);
    return 0;
}
"""


def _build(tmp_path: Path, *, armv8m: bool, max_ops: int) -> Path:
    header = _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
        cmsis_device_header="hpx_sim_device.h",
        profiling_backends=["armv8m_pmu"] if armv8m else ["dwt"],
        has_armv8m_pmu=armv8m,
        has_ethos_u=False,
        pmu_max_ops=max_ops,
    )
    source = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
        profiling_backends=["armv8m_pmu"] if armv8m else ["dwt"],
        has_armv8m_pmu=armv8m,
    )
    (tmp_path / "hpx_pmu_profiler.h").write_text(header)
    (tmp_path / "hpx_pmu_profiler.cc").write_text(source)
    (tmp_path / "hpx_sim_device.h").write_text(_SIM_DEVICE_H)
    (tmp_path / "nsx_pmu_utils.h").write_text(_SIM_PMU_H)
    (tmp_path / "driver.cc").write_text(_DRIVER_CC)
    exe = tmp_path / "sim"
    assert _GXX is not None
    subprocess.run(
        [
            _GXX,
            "-std=gnu++17",
            "-Wall",
            # Sim headers shadow the stub tree.
            "-I",
            str(tmp_path),
            "-I",
            str(_STUB_DIR),
            str(tmp_path / "hpx_pmu_profiler.cc"),
            str(tmp_path / "driver.cc"),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return exe


def _run(exe: Path, *ops: str) -> tuple[list[list[str]], bool]:
    out = subprocess.run([str(exe), *ops], check=True, capture_output=True, text=True).stdout
    lines = out.strip().splitlines()
    rows = [line.split(",") for line in lines[1:-1]]
    return rows, lines[-1] == "capacity_exceeded=1"


@pytest.mark.parametrize("armv8m", [True, False], ids=["armv8m_pmu", "dwt"])
def test_ops_past_capacity_leave_recorded_layers_intact(tmp_path, armv8m):
    exe = _build(tmp_path, armv8m=armv8m, max_ops=2)

    rows, exceeded = _run(exe, "100:0", "200:0", "5000:0", "9000:0")

    assert [row[2] for row in rows] == ["100", "200"]
    assert exceeded


@pytest.mark.parametrize("armv8m", [True, False], ids=["armv8m_pmu", "dwt"])
def test_ops_within_capacity_do_not_flag(tmp_path, armv8m):
    exe = _build(tmp_path, armv8m=armv8m, max_ops=2)

    rows, exceeded = _run(exe, "100:0", "200:0")

    assert [row[2] for row in rows] == ["100", "200"]
    assert not exceeded


def test_only_chain_overflow_sets_the_layer_flag(tmp_path):
    exe = _build(tmp_path, armv8m=True, max_ops=8)

    # Bit 0: counter 0 low-half carry. Bit 3: counter 1 CHAIN wrap.
    rows, _ = _run(exe, "10:0x0", "10:0x1", "10:0x8", "10:0x100")

    assert [row[-1] for row in rows] == ["0", "0", "1", "0"]


_TEMPLATES = Path(__file__).parents[2] / "src" / "helia_profiler" / "firmware" / "templates"


@pytest.mark.parametrize(
    "template", ["hpx_pmu_profiler.cc.j2", "main_aot.cc.j2", "main_executorch.cc.j2"]
)
def test_every_engine_reads_layers_through_the_shared_helper(template):
    source = (_TEMPLATES / template).read_text()

    assert '{% include "_pmu_read.j2" %}' in source
    assert "hpx_pmu_read_layer(" in source
    assert "ARM_PMU_Get_CNTR_OVS" not in source
