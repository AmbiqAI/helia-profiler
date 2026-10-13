"""Execute the rendered counter-name helpers with host bounds checks."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helia_profiler.firmware import _jinja_env

from .test_firmware_render_snapshots import _render

_GXX = shutil.which("g++")
pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or _GXX is None, reason="requires Linux host g++ with UBSan"
)

_DRIVER = r"""
#include <cassert>
#include <cstdlib>
{maximum}
struct Slots {{
    const char* before;
    const char* names[kMaxEvents];
    const char* after;
}};
static Slots slots;
#define {destination} slots.names
static void set_names(const char* const names[], int num_names) {{{body}
}}
int main(int argc, char** argv) {{
    assert(argc == 3);
    const int count = std::atoi(argv[1]);
    const int expected = std::atoi(argv[2]);
    const char* names[] = {{"A", "B", "C", "D"}};
    slots.before = "before";
    slots.after = "after";
    const char* before = slots.before;
    const char* after = slots.after;
    for (auto& slot : slots.names) slot = "old";
    set_names(count > 0 ? names : nullptr, count);
    assert(slots.before == before && slots.after == after);
    for (int i = 0; i < kMaxEvents; ++i)
        assert(slots.names[i] == (i < expected ? names[i] : nullptr));
}}
"""


@pytest.fixture(scope="module", params=["profiler", "aot"])
def counter_names_exe(request, tmp_path_factory) -> Path:
    if request.param == "profiler":
        text = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(has_armv8m_pmu=True)
        header = _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
            cmsis_device_header="host_device.h",
            has_armv8m_pmu=True,
            has_ethos_u=False,
            pmu_max_ops=2,
        )
        name, destination = "HpxPmuProfiler::SetCounterNames", "counter_names_"
    else:
        text = header = _render("apollo510", "rtt", "helia-aot")
        name, destination = "profiler_set_counter_names", "g_counter_names"
    maximum = re.search(r"static constexpr int kMaxEvents = \d+;", header)
    assert maximum is not None
    start = text.index("{", text.index(f"void {name}("))
    body = text[start + 1 : text.index("\n}", start)]
    directory = tmp_path_factory.mktemp(request.param)
    source, exe = directory / "counter_names.cc", directory / "counter_names"
    source.write_text(
        _DRIVER.format(maximum=maximum[0], destination=destination, body=body), encoding="utf-8"
    )
    assert _GXX is not None
    subprocess.run(
        [
            _GXX,
            "-std=c++17",
            "-fsanitize=undefined",
            "-fno-sanitize-recover=all",
            str(source),
            "-o",
            str(exe),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return exe


@pytest.mark.parametrize(
    "count,expected",
    [(-(2**31), 0), (-5, 0), (-1, 0), (0, 0), (1, 1), (3, 3), (4, 4), (5, 4), (2**31 - 1, 4)],
)
def test_counter_name_count_stays_within_slots(counter_names_exe: Path, count: int, expected: int):
    subprocess.run(
        [str(counter_names_exe), str(count), str(expected)],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
