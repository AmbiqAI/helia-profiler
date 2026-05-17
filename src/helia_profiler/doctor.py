"""hpx doctor — check toolchain and dependencies."""

from __future__ import annotations

import shutil


def collect_checks() -> tuple[
    list[tuple[str, str, str | None]],
    list[tuple[str, str, bool]],
]:
    """Check required host tools and return structured results.

    Returns a tuple of ``(checks, optional)`` where:
    - *checks*: ``[(label, binary_name, path_or_none), ...]``
    - *optional*: ``[(label, package_name, available), ...]``
    """
    tool_specs = [
        ("ARM GCC toolchain", "arm-none-eabi-gcc"),
        ("CMake (>= 3.24)", "cmake"),
        ("Ninja build system", "ninja"),
        ("SEGGER J-Link commander", "JLinkExe"),
        ("SEGGER SWO viewer", "JLinkSWOViewerCL"),
        ("neuralspotx CLI", "nsx"),
    ]

    checks: list[tuple[str, str, str | None]] = []
    for label, binary in tool_specs:
        path = shutil.which(binary)
        checks.append((label, binary, path))

    optional_specs = [
        ("heliaAOT compiler", "helia_aot"),
    ]
    # Optional toolchains (checked as binaries on PATH)
    optional_tools = [
        ("ARM Compiler (armclang)", "armclang"),
        ("ARM fromelf (armclang)", "fromelf"),
    ]
    optional: list[tuple[str, str, bool]] = []
    for label, pkg_name in optional_specs:
        try:
            __import__(pkg_name)
            available = True
        except ImportError:
            available = False
        optional.append((label, pkg_name, available))

    for label, binary in optional_tools:
        optional.append((label, binary, shutil.which(binary) is not None))

    return checks, optional


def run_doctor() -> None:
    """Legacy entry point — prints doctor results to stdout."""
    checks, optional = collect_checks()

    all_ok = True
    for label, _binary, path in checks:
        if path:
            print(f"  ✓ {label}: {path}")
        else:
            print(f"  ✗ {label}: not found")
            all_ok = False

    for label, _pkg, available in optional:
        if available:
            print(f"  ✓ {label}: available")
        else:
            print(f"  - {label}: not installed")

    if all_ok:
        print("\nAll required tools found.")
    else:
        print("\nSome required tools are missing. Install them before profiling.")
