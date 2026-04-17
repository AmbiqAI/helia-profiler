"""hpx doctor — check toolchain and dependencies."""

from __future__ import annotations

import shutil


def run_doctor() -> None:
    """Check that required host tools are available."""
    checks = [
        ("arm-none-eabi-gcc", "ARM GCC toolchain"),
        ("cmake", "CMake (>= 3.24)"),
        ("ninja", "Ninja build system"),
        ("JLinkExe", "SEGGER J-Link commander"),
        ("JLinkSWOViewerCL", "SEGGER SWO viewer"),
        ("nsx", "neuralspotx CLI"),
    ]

    all_ok = True
    for binary, label in checks:
        path = shutil.which(binary)
        if path:
            print(f"  ✓ {label}: {path}")
        else:
            print(f"  ✗ {label}: {binary} not found")
            all_ok = False

    # Optional dependencies
    optional = [
        ("joulescope", "Joulescope (optional, for --power)"),
    ]
    for pkg_name, label in optional:
        try:
            __import__(pkg_name)
            print(f"  ✓ {label}: available")
        except ImportError:
            print(f"  - {label}: not installed")

    if all_ok:
        print("\nAll required tools found.")
    else:
        print("\nSome required tools are missing. Install them before profiling.")
