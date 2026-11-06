"""Contract: the rendered hpx_printf never sends past the end of its line buffer.

``vsnprintf`` returns the length the formatted text would have had, not the
length it wrote. The RTT and USB CDC transports pass that length to their
write call, so it has to be clamped to what ``line_buf`` holds. This module
renders each transport's ``hpx_printf`` and runs it on host against stubs that
record the length handed to the transport.

The transports' declarations come from the compile-check stubs in
``tests/fixtures/compile_stubs`` (as in ``test_pmu_profiler_sim.py``); macros
then point the calls this test observes at a recorder.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from helia_profiler.firmware import _jinja_env

_GXX = shutil.which("g++")
# AddressSanitizer: Linux only, like the UBSan test in test_counter_names.py (MinGW has no ASan).
pytestmark = [
    pytest.mark.host_integration,
    pytest.mark.skipif(
        sys.platform != "linux" or _GXX is None,
        reason="requires Linux host g++ with AddressSanitizer",
    ),
]

_STUB_DIR = Path(__file__).parent.parent / "fixtures" / "compile_stubs"

_COMMON_PRELUDE = r"""
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>

#define __WFI() ((void)0)

static unsigned g_sim_max_len = 0;
static unsigned g_sim_writes = 0;
static char g_sim_last[1024];

static void hpx_sim_record(const void *buf, unsigned len) {
    if (len > g_sim_max_len) g_sim_max_len = len;
    g_sim_writes++;
    const unsigned copy = len < sizeof(g_sim_last) ? len : (unsigned)sizeof(g_sim_last);
    std::memcpy(g_sim_last, buf, copy);
}
"""

# The stub's SEGGER_RTT_Write discards the data and its GetAvailWriteSpace
# reports a full buffer; record the writes and report room instead.
_RTT_PRELUDE = r"""
#include "SEGGER_RTT.h"
SEGGER_RTT_CB _SEGGER_RTT;
#define SEGGER_RTT_Write(index, buf, len) hpx_sim_record((buf), (len))
#define SEGGER_RTT_GetAvailWriteSpace(index) (1U << 20)
static uint32_t SystemCoreClock = 96000000U;
#define __NOP() ((void)0)
#define HPX_CLEAN_DCACHE() ((void)0)
"""

_USB_PRELUDE = r"""
#include "nsx_usb.h"
#define nsx_usb_send(cfg, buf, len, sent) (hpx_sim_record((buf), (len)), *(sent) = (len), 0U)
"""

# A line longer than the 512-byte line_buf, in the transport's normal mode
# and, for RTT, in the lossless mode used for CSV dumps and HPX_END.
_MAIN = r"""
int main(void) {
    char longline[700];
    std::memset(longline, 'x', sizeof(longline) - 1);
    longline[sizeof(longline) - 1] = '\0';
    hpx_printf("%s", longline);
#ifdef HPX_SIM_RTT
    hpx_rtt_set_blocking();
    hpx_printf("%s", longline);
    hpx_rtt_set_nonblocking();
#endif
    hpx_printf("short %d\n", 7);
    std::printf("writes=%u max_len=%u last=%.*s\n", g_sim_writes, g_sim_max_len, 5, g_sim_last);
    return 0;
}
"""


def _run(tmp_path: Path, transport: str) -> str:
    rendered = _jinja_env.get_template("_hpx_printf.j2").render(
        transport=transport,
        printf_linkage="static ",
        power_only=False,
        power_sync_enabled=False,
    )
    prelude = _RTT_PRELUDE if transport == "rtt" else _USB_PRELUDE
    define = "#define HPX_SIM_RTT 1\n" if transport == "rtt" else ""
    src = tmp_path / "hpx_printf_sim.cc"
    src.write_text(define + _COMMON_PRELUDE + prelude + rendered + _MAIN, encoding="utf-8")
    exe = tmp_path / "hpx_printf_sim"
    assert _GXX is not None
    subprocess.run(
        [
            _GXX,
            "-std=gnu++17",
            "-fsanitize=address",
            "-I",
            str(_STUB_DIR),
            "-o",
            str(exe),
            str(src),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout


@pytest.mark.parametrize("transport", ["rtt", "usb_cdc"])
def test_long_line_is_clamped_to_line_buf(tmp_path: Path, transport: str) -> None:
    out = _run(tmp_path, transport)
    fields = dict(part.split("=", 1) for part in out.split())
    expected_writes = 3 if transport == "rtt" else 2
    assert int(fields["writes"]) == expected_writes
    assert int(fields["max_len"]) == 511
    assert fields["last"] == "short"
