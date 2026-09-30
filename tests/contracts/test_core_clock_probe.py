"""Run the core clock probe on the host against fake timers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from helia_profiler.firmware import _jinja_env

_GXX = shutil.which("g++")

_HARNESS = """
#include <cstdint>
#include <cstdio>
#define HPX_STIMER_HZ 32768U
static uint32_t g_calls = 0;
static uint32_t hpx_stimer_init(void) { return 1U; }
// STIMER_CALLS_PER_TICK == 0 models a stopped crystal.
static uint32_t hpx_stimer_ticks(void) {
    ++g_calls;
    return STIMER_CALLS_PER_TICK ? g_calls / STIMER_CALLS_PER_TICK : 0U;
}
// CYCCNT tracks STIMER calls; the first FROZEN_READS reads return 0.
static uint32_t g_cyccnt_reads = 0;
struct FakeCyccnt {
    operator uint32_t() const {
        return ++g_cyccnt_reads <= FROZEN_READS ? 0U : g_calls * CYCLES_PER_CALL;
    }
};
struct FakeDwt { FakeCyccnt CYCCNT; };
static FakeDwt g_dwt;
#define DWT (&g_dwt)
#define hpx_printf std::printf
static void dwt_init(void) {}
int main(void) {
%s
    return 0;
}
"""


def _run_probe(
    tmp_path: Path, *, calls_per_tick: int, cycles_per_call: int, frozen_reads: int = 0
) -> str:
    probe = _jinja_env.get_template("_core_clock_probe.j2").render()
    source = tmp_path / "probe.cc"
    source.write_text(_HARNESS % probe, encoding="utf-8")
    binary = tmp_path / "probe"
    subprocess.run(
        [
            str(_GXX),
            "-std=gnu++17",
            "-O0",
            f"-DSTIMER_CALLS_PER_TICK={calls_per_tick}U",
            f"-DCYCLES_PER_CALL={cycles_per_call}U",
            f"-DFROZEN_READS={frozen_reads}U",
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
    )
    return subprocess.run(
        [str(binary)], capture_output=True, text=True, check=True, timeout=20
    ).stdout


@pytest.mark.skipif(_GXX is None, reason="needs a host g++")
def test_probe_reports_the_fake_clock(tmp_path: Path):
    # 10 calls per tick x 763 cycles per call = 7630 cycles per tick.
    out = _run_probe(tmp_path, calls_per_tick=10, cycles_per_call=763)
    assert out.strip() == f"HPX_MEASURED_CLOCK_HZ={7630 * 32768}"


@pytest.mark.skipif(_GXX is None, reason="needs a host g++")
def test_probe_gives_up_on_a_stopped_stimer(tmp_path: Path):
    out = _run_probe(tmp_path, calls_per_tick=0, cycles_per_call=1)
    assert out.strip() == "HPX_MEASURED_CLOCK_HZ=0"


@pytest.mark.skipif(_GXX is None, reason="needs a host g++")
def test_probe_rejects_a_frozen_first_sample(tmp_path: Path):
    out = _run_probe(tmp_path, calls_per_tick=10, cycles_per_call=763, frozen_reads=2)
    assert out.strip() == f"HPX_MEASURED_CLOCK_HZ={7630 * 32768}"
