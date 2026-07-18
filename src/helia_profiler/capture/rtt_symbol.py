"""Resolve the SEGGER RTT control block address from build artifacts.

The firmware pins its RTT control block (``_SEGGER_RTT``) into a fixed section
chosen per cache family (see ``firmware/__init__.py``): non-cached TCM
(default ``.bss``) on the cache-coherent Cortex-M55 parts, and ``.sram_bss``
(SHARED_SRAM) on the cacheless Cortex-M4 parts.  Either way its link address is
deterministic for a given build.  Recovering that address lets the host capture
path attach directly with ``rtt_start(block_address=...)`` and skip the slow
SWD memory sweep that discovery would otherwise need.

Resolution is best-effort: every helper returns ``None`` on any failure so the
caller transparently falls back to scanning.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from ..toolchains import get_toolchain_spec, resolve_toolchain_executable

log = logging.getLogger("hpx")

_RTT_SYMBOL = "_SEGGER_RTT"

# GNU ld map: a bare symbol line such as
#   "                0x20088010                _SEGGER_RTT"
_MAP_SYMBOL_RE = re.compile(
    rf"^\s*0x([0-9a-fA-F]+)\s+{re.escape(_RTT_SYMBOL)}\s*$",
    re.MULTILINE,
)

# nm output: "20088010 B _SEGGER_RTT" (address, type, name).
_NM_SYMBOL_RE = re.compile(
    rf"^([0-9a-fA-F]+)\s+\S\s+{re.escape(_RTT_SYMBOL)}\s*$",
    re.MULTILINE,
)


def _address_from_map(build_dir: Path, target_name: str) -> int | None:
    """Parse the linker map for the ``_SEGGER_RTT`` symbol address."""
    candidates = sorted(build_dir.glob(f"{target_name}.map")) or sorted(
        build_dir.glob(f"**/{target_name}.map")
    )
    for map_path in candidates:
        try:
            text = map_path.read_text(errors="replace")
        except OSError as exc:
            log.debug("could not read map %s: %s", map_path, exc)
            continue
        match = _MAP_SYMBOL_RE.search(text)
        if match:
            address = int(match.group(1), 16)
            log.debug("resolved %s = 0x%08X from %s", _RTT_SYMBOL, address, map_path)
            return address
    return None


def _nm_command(toolchain: str) -> str | None:
    """Return the ``nm`` executable matching *toolchain*, or ``None``."""
    try:
        spec = get_toolchain_spec(toolchain)
        return resolve_toolchain_executable(toolchain, spec.nm)
    except ValueError:
        return None


def _address_from_nm(
    build_dir: Path,
    toolchain: str,
    *,
    target_name: str,
    timeout_s: int,
) -> int | None:
    """Read the ``_SEGGER_RTT`` address from the ELF via ``nm``."""
    nm = _nm_command(toolchain)
    if nm is None:
        return None
    elf_candidates = (
        sorted(build_dir.glob(f"{target_name}.elf"))
        or sorted(build_dir.glob(f"{target_name}.axf"))
        or sorted(build_dir.glob(target_name))
    )
    for elf in elf_candidates:
        try:
            result = subprocess.run(
                [nm, str(elf)],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            log.debug("%s probe failed: %s", nm, exc)
            return None
        if result.returncode != 0:
            log.debug("%s failed: %s", nm, (result.stderr or "").strip())
            continue
        match = _NM_SYMBOL_RE.search(result.stdout or "")
        if match:
            address = int(match.group(1), 16)
            log.debug("resolved %s = 0x%08X from %s via %s", _RTT_SYMBOL, address, elf, nm)
            return address
    return None


def resolve_rtt_control_block_address(
    build_dir: Path | None,
    toolchain: str,
    *,
    target_name: str = "hpx_profiler",
    timeout_s: int = 10,
) -> int | None:
    """Return the linked address of ``_SEGGER_RTT`` for this build, or ``None``.

    Tries the linker map first (no toolchain dependency), then falls back to
    ``nm`` on the ELF.  Any failure yields ``None`` so the caller falls back to
    host-side RTT control-block scanning.
    """
    if build_dir is None:
        return None
    build_dir = Path(build_dir)
    if not build_dir.exists():
        return None

    address = _address_from_map(build_dir, target_name)
    if address is None:
        address = _address_from_nm(
            build_dir,
            toolchain,
            target_name=target_name,
            timeout_s=timeout_s,
        )
    return address


__all__ = ["resolve_rtt_control_block_address"]
