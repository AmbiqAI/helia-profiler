"""Toolchain probes — ``--version`` and binary-section size queries.

Centralises every shell-out for read-only toolchain info so that:

* ``build_firmware`` does not need ``subprocess`` at all;
* timeout handling, error capture, and output parsing live in one place;
* tests can monkeypatch a single module to simulate missing toolchains.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from .results import BinarySections

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# Compiler / cmake --version probes
# ---------------------------------------------------------------------------


def _compiler_command(toolchain: str) -> str:
    """Return the executable name to query for ``--version`` info.

    Maps profile toolchain names ("armclang", "atfe", "arm-none-eabi-gcc")
    onto the actual binary that will respond to ``--version``.
    """
    if toolchain in ("armclang", "atfe"):
        return toolchain
    if "gcc" in toolchain:
        return toolchain if toolchain.endswith("-gcc") else f"{toolchain}-gcc"
    return toolchain


def _run_version(cmd: str, *, timeout_s: int) -> str:
    """Return the first line of ``<cmd> --version`` stdout, or ``""``."""
    try:
        result = subprocess.run(
            [cmd, "--version"], capture_output=True, text=True, timeout=timeout_s
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s --version probe failed: %s", cmd, exc)
        return ""
    if result.returncode != 0:
        log.debug("%s --version returned rc=%d", cmd, result.returncode)
        return ""
    out = (result.stdout or "").strip().splitlines()
    return out[0] if out else ""


def compiler_version(toolchain: str, *, timeout_s: int) -> str:
    """Return the first line of the compiler's ``--version`` banner."""
    return _run_version(_compiler_command(toolchain), timeout_s=timeout_s)


def cmake_version(*, timeout_s: int) -> str:
    """Return the first line of ``cmake --version``."""
    return _run_version("cmake", timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Binary section size probes
# ---------------------------------------------------------------------------


def _sections_via_size(
    binary_path: Path,
    *,
    size_cmd: str,
    timeout_s: int,
) -> BinarySections | None:
    """Parse Berkeley-format ``size`` output for GCC ELF binaries.

    Output shape::

           text    data     bss     dec     hex filename
         123420   27032   92412  242864   3b4b0 hpx_profiler
    """
    try:
        result = subprocess.run(
            [size_cmd, str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s probe failed: %s", size_cmd, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", size_cmd, (result.stderr or "").strip())
        return None
    lines = (result.stdout or "").strip().splitlines()
    if len(lines) < 2:
        return None
    parts = lines[1].split()
    if len(parts) < 4:
        return None
    try:
        text, data, bss, total = (int(parts[i]) for i in range(4))
    except ValueError:
        return None
    log.info("Binary sections: text=%d data=%d bss=%d total=%d", text, data, bss, total)
    return BinarySections(text=text, data=data, bss=bss, total=total)


_FROMELF_TOTALS_RE = re.compile(r"\s*Grand Totals?\s*[:\s]+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")


def _sections_via_fromelf(
    binary_path: Path,
    *,
    timeout_s: int,
) -> BinarySections | None:
    """Parse ``fromelf --text -z`` output for armclang/ATfE binaries.

    The ``Grand Totals`` line reports ``Code RO_Data RW_Data ZI_Data``
    which we collapse to ``(text=Code+RO, data=RW, bss=ZI)`` to match
    the GCC ``size`` shape.
    """
    try:
        result = subprocess.run(
            ["fromelf", "--text", "-z", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("fromelf probe failed: %s", exc)
        return None
    if result.returncode != 0:
        log.debug("fromelf failed: %s", (result.stderr or "").strip())
        return None
    for line in (result.stdout or "").splitlines():
        m = _FROMELF_TOTALS_RE.match(line)
        if m:
            code, ro_data, rw_data, zi_data = (int(m.group(i)) for i in range(1, 5))
            text = code + ro_data
            data = rw_data
            bss = zi_data
            total = text + data + bss
            log.info(
                "Binary sections (fromelf): text=%d data=%d bss=%d total=%d",
                text,
                data,
                bss,
                total,
            )
            return BinarySections(text=text, data=data, bss=bss, total=total)
    log.debug("Could not find Grand Totals in fromelf output")
    return None


def binary_sections(
    binary_path: Path,
    toolchain: str,
    *,
    timeout_s: int,
) -> BinarySections | None:
    """Return section sizes for *binary_path*, dispatching by toolchain.

    Uses ``fromelf`` for armclang / ATfE binaries and ``<prefix>-size``
    for GCC binaries.  Returns ``None`` if the size tool is unavailable
    or its output cannot be parsed.
    """
    if toolchain in ("armclang", "atfe"):
        return _sections_via_fromelf(binary_path, timeout_s=timeout_s)

    prefix = toolchain.rsplit("-gcc", 1)[0] if toolchain.endswith("-gcc") else toolchain
    return _sections_via_size(binary_path, size_cmd=f"{prefix}-size", timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Symbol address probe (for build-time placement verification)
# ---------------------------------------------------------------------------


def _nm_command(toolchain: str) -> str:
    """Return the ``nm`` executable matching *toolchain*.

    armclang / ATfE ship the LLVM binutils (``llvm-nm``); GCC uses the
    cross-prefixed ``<prefix>-nm`` (e.g. ``arm-none-eabi-nm``).
    """
    if toolchain in ("armclang", "atfe"):
        return "llvm-nm"
    prefix = toolchain.rsplit("-gcc", 1)[0] if toolchain.endswith("-gcc") else toolchain
    return f"{prefix}-nm"


def symbol_address(
    binary_path: Path,
    toolchain: str,
    symbol: str,
    *,
    timeout_s: int,
) -> tuple[int, str] | None:
    """Return ``(address, nm_type_letter)`` for *symbol* in *binary_path*.

    Reads the linked address via ``nm``.  The symbol is matched as a suffix so
    a C++-mangled local (``_ZL15g_arena_storage``) and a plain C symbol
    (``g_arena_storage``) both resolve.  Returns ``None`` on any failure
    (missing tool, symbol absent, parse error) so callers stay best-effort.
    """
    nm = _nm_command(toolchain)
    try:
        result = subprocess.run(
            [nm, str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s probe failed: %s", nm, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", nm, (result.stderr or "").strip())
        return None
    pattern = re.compile(
        rf"^([0-9a-fA-F]+)\s+(\S)\s+\S*{re.escape(symbol)}\s*$",
        re.MULTILINE,
    )
    match = pattern.search(result.stdout or "")
    if match is None:
        log.debug("symbol %s not found via %s in %s", symbol, nm, binary_path)
        return None
    return int(match.group(1), 16), match.group(2)


__all__ = [
    "binary_sections",
    "cmake_version",
    "compiler_version",
    "symbol_address",
]
