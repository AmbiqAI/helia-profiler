"""hpx doctor — check toolchain and dependencies."""

from __future__ import annotations

import importlib.util
import shutil


def collect_checks() -> tuple[
    list[tuple[str, str, str | None]],
    list[tuple[str, str, bool]],
    list[tuple[str, str, bool]],
]:
    """Check required host tools and return structured results.

    Returns a tuple of ``(checks, required_python, optional)`` where:
    - *checks*: ``[(label, binary_name, path_or_none), ...]``
    - *required_python*: ``[(label, package_name, available), ...]``
    - *optional*: ``[(label, package_or_binary_name, available), ...]``
    """
    tool_specs = [
        ("ARM GCC toolchain", "arm-none-eabi-gcc"),
        ("CMake (>= 3.24)", "cmake"),
        ("Ninja build system", "ninja"),
        ("SEGGER J-Link commander", "JLinkExe"),
    ]

    checks: list[tuple[str, str, str | None]] = []
    for label, binary in tool_specs:
        path = shutil.which(binary)
        checks.append((label, binary, path))

    required_python_specs = [
        ("neuralspotx Python package", "neuralspotx"),
    ]
    optional_specs = [
        ("heliaAOT compiler", "helia_aot"),
        ("pylink Python package (RTT/SWO transport)", "pylink"),
    ]
    # Optional toolchains (checked as binaries on PATH)
    optional_tools = [
        ("ARM Compiler (armclang)", "armclang"),
        ("ARM fromelf (armclang)", "fromelf"),
    ]
    required_python: list[tuple[str, str, bool]] = []
    for label, pkg_name in required_python_specs:
        required_python.append((label, pkg_name, importlib.util.find_spec(pkg_name) is not None))

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

    return checks, required_python, optional


def run_doctor() -> None:
    """Legacy entry point — prints doctor results to stdout."""
    checks, required_python, optional = collect_checks()

    all_ok = True
    for label, _binary, path in checks:
        if path:
            print(f"  ✓ {label}: {path}")
        else:
            print(f"  ✗ {label}: not found")
            all_ok = False

    for label, _pkg, available in required_python:
        if available:
            print(f"  ✓ {label}: installed")
        else:
            print(f"  ✗ {label}: not installed")
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
