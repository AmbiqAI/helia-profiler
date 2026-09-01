"""Cross-layer configuration vocabulary — the bottom-layer enum leaf.

These StrEnums are named by config, rendered into firmware, and dispatched
on by the transport and capture layers. They live here — stdlib-only, no
hpx imports — so a backend can import an enum without executing the whole
config resolver (#229 D2). :mod:`helia_profiler.config` re-exports them, so
``from helia_profiler.config import Transport`` remains the public spelling.

The no-imports rule is contract-tested (``tests/test_package_layout.py``);
resist adding anything here that is not shared vocabulary.
"""

from __future__ import annotations

from enum import StrEnum


class Toolchain(StrEnum):
    """Supported cross-compiler toolchains for the profiler firmware.

    ``GCC`` and ``ARM_NONE_EABI_GCC`` are aliases — both resolve to the
    GNU Arm Embedded toolchain.  ``ARMCLANG`` is Arm Compiler 6 (Keil),
    ``ATFE`` is the Arm Toolchain for Embedded (LLVM).
    """

    ARM_NONE_EABI_GCC = "arm-none-eabi-gcc"
    GCC = "gcc"
    ARMCLANG = "armclang"
    ATFE = "atfe"


class Transport(StrEnum):
    """Host↔target transport for capture and heartbeat traffic."""

    RTT = "rtt"
    USB_CDC = "usb_cdc"
    SWO = "swo"
    UART = "uart"


class Aggregation(StrEnum):
    """Per-iteration aggregation estimator for per-layer counters.

    ``MEDIAN`` is the default because it rejects the occasional corrupted
    iteration (e.g. an Apollo4 DWT->CYCCNT uint32 wrap or a frozen-zero read
    while the host probe is still settling) that a plain ``MEAN`` would smear
    across the whole layer.  ``TRIMMED`` drops the high/low extremes, then
    means.
    """

    MEAN = "mean"
    MEDIAN = "median"
    TRIMMED = "trimmed"
