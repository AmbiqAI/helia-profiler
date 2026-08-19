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
from .toolchains import get_toolchain_spec, resolve_toolchain_executable

log = logging.getLogger("hpx")


# ---------------------------------------------------------------------------
# Compiler / cmake --version probes
# ---------------------------------------------------------------------------


def _compiler_command(toolchain: str) -> str:
    """Return the executable name to query for ``--version`` info.

    Maps profile toolchain names ("armclang", "atfe", "arm-none-eabi-gcc")
    onto the actual binary that will respond to ``--version``.
    """
    spec = get_toolchain_spec(toolchain)
    return resolve_toolchain_executable(toolchain, spec.compiler)


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
    readelf_cmd: str | None,
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
    reserved = (
        _reserved_via_readelf(
            binary_path, readelf_cmd=readelf_cmd, timeout_s=timeout_s
        )
        if readelf_cmd is not None
        else None
    )
    if reserved is None or reserved > bss:
        # Either the section list could not be read, or the reserved regions
        # are not all inside what Berkeley called bss -- `size -A` reports no
        # section TYPE, so a `.heap` carrying contents (PROGBITS) would land
        # in Berkeley's data instead, and subtracting it from bss would be
        # wrong in a way a clamp would hide. Report exactly what the tool
        # said rather than guess; the numbers stay explainable either way.
        if reserved is not None and reserved > bss:
            log.debug(
                "reserved sections (%d B) exceed bss (%d B); not adjusting",
                reserved,
                bss,
            )
        return BinarySections(text=text, data=data, bss=bss, total=total)
    return BinarySections(
        text=text,
        data=data,
        bss=bss - reserved,
        total=total,
        reserved=reserved,
    )


_FROMELF_TOTALS_RE = re.compile(r"\s*Grand Totals?\s*[:\s]+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")



#: Section-name stems treated as linker reservations rather than program
#: state. Matched only among sections already known to be **NOBITS and
#: allocated** -- the name alone is not evidence.
#:
#: Only ``.heap``. Review corrected an earlier version that also claimed
#: ``.stack``: on every NSX SoC the ``.stack`` region IS the live stack --
#: ``startup_gcc.c`` loads the initial MSP from its top and sets MSPLIM/PSPLIM
#: from its base -- so it is memory the firmware genuinely needs, and it
#: belongs in the reported footprint.
#:
#: ``.heap`` is different in kind on these parts, and that difference is the
#: whole point of issue #24: NSX linker scripts do not size it to a
#: requirement, they run it to the end of the region
#: (``. = ORIGIN(MCU_TCM) + LENGTH(MCU_TCM);``) purely so ``_sbrk`` has a
#: bounded area. Its size states what was left over, not what is needed, so
#: counting it as footprint tells the reader nothing useful about the build.
_RESERVED_NOBITS_STEMS = ("heap",)

#: ``readelf -S -W`` row: ``[Nr] Name Type Addr Off Size ES Flg ...`` with the
#: numeric columns in hex. The name is anchored to a leading dot so the empty
#: name of section 0 cannot shift the field positions.
_READELF_SECTION_RE = re.compile(
    r"^\s*\[\s*\d+\]\s+(\.\S+)\s+(\S+)\s+"
    r"[0-9a-fA-F]+\s+[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[0-9a-fA-F]+\s+(\S*)"
)


def _reserved_via_readelf(
    binary_path: Path,
    *,
    readelf_cmd: str,
    timeout_s: int,
) -> int | None:
    """Bytes of linker-reserved NOBITS regions, from the section headers.

    An earlier version of this used ``size -A``, which reports name, size and
    address but **no TYPE** -- so a ``.heap`` carrying contents (PROGBITS,
    which ``size`` correctly counts in *data*) was indistinguishable from the
    NOLOAD reservation, and subtracting it from bss understated real
    zero-initialized state while double-counting those bytes. Adversarial
    review caught that on a purpose-built ELF; issue #24 had named readelf
    for this reason from the start.

    So the type is checked: only ``NOBITS`` sections carrying the ``A``
    (alloc) flag are candidates, which is exactly the set ``size`` folds into
    its bss column. The name then selects the reserved ones among them.

    Returns ``None`` when the tool is unavailable or the output cannot be
    parsed, so the caller keeps the unadjusted numbers rather than inventing
    an adjustment.
    """
    try:
        result = subprocess.run(
            [readelf_cmd, "-S", "-W", str(binary_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        log.debug("%s section probe failed: %s", readelf_cmd, exc)
        return None
    if result.returncode != 0:
        log.debug("%s failed: %s", readelf_cmd, (result.stderr or "").strip())
        return None

    reserved = 0
    seen_section = False
    for line in (result.stdout or "").splitlines():
        match = _READELF_SECTION_RE.match(line)
        if match is None:
            continue
        name, sec_type, size_hex, flags = match.groups()
        seen_section = True
        if sec_type != "NOBITS" or "A" not in flags:
            continue
        stem = name.lstrip(".").split(".")[0].split("_")[0]
        if stem in _RESERVED_NOBITS_STEMS:
            reserved += int(size_hex, 16)
    if not seen_section:
        return None
    return reserved


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

    Uses ``fromelf`` for armclang binaries and the configured ``size`` tool
    for GCC and ATFE binaries. Returns ``None`` if the size tool is
    unavailable or its output cannot be parsed.
    """
    spec = get_toolchain_spec(toolchain)
    if spec.section_probe == "fromelf":
        return _sections_via_fromelf(binary_path, timeout_s=timeout_s)
    assert spec.size is not None
    return _sections_via_size(
        binary_path,
        size_cmd=resolve_toolchain_executable(toolchain, spec.size),
        readelf_cmd=(
            resolve_toolchain_executable(toolchain, spec.readelf)
            if spec.readelf is not None
            else None
        ),
        timeout_s=timeout_s,
    )


# ---------------------------------------------------------------------------
# Symbol address probe (for build-time placement verification)
# ---------------------------------------------------------------------------


def _nm_command(toolchain: str) -> str:
    """Return the ``nm`` executable matching *toolchain*.

    armclang / ATfE ship the LLVM binutils (``llvm-nm``); GCC uses the
    cross-prefixed ``<prefix>-nm`` (e.g. ``arm-none-eabi-nm``).
    """
    spec = get_toolchain_spec(toolchain)
    return resolve_toolchain_executable(toolchain, spec.nm)


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
