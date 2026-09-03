"""SEGGER RTT source vendoring for generated apps (RTT transport).

Owns locating a SEGGER RTT source checkout (config path, ``SEGGER_RTT_PATH``,
or the pinned sources bundled with heliaPROFILER), validating its layout, and
copying it into the generated app — including the per-SoC buffer-placement
snippet appended to ``SEGGER_RTT_Conf.h``.  Extracted from
``firmware/__init__`` at the module size ceiling (see the elf_inventory
precedent in toolchain_probe); the package re-exports every name so callers
(including doctor) keep one import surface.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..errors import FirmwareError
from .render import _write_text

log = logging.getLogger("hpx")


def find_segger_rtt_dir(configured_path: Path | None = None) -> Path:
    """Locate the SEGGER RTT source directory.

    An explicit config path takes precedence, followed by ``SEGGER_RTT_PATH``
    and the pinned sources bundled with heliaPROFILER. Override paths must point
    to the root directory of a SEGGER RTT source checkout (the folder containing
    ``RTT/`` and ``Config/`` subdirectories).

    Returns the validated path.
    """
    if configured_path is not None:
        path = Path(configured_path).expanduser().resolve()
        if _is_segger_rtt_root(path):
            return path
        raise FirmwareError(
            f"target.segger_rtt_path={configured_path} does not contain RTT/SEGGER_RTT.c",
            hint="Point target.segger_rtt_path to the root containing RTT/ and Config/.",
        )

    env_path = os.environ.get("SEGGER_RTT_PATH")
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if _is_segger_rtt_root(p):
            return p
        raise FirmwareError(
            f"SEGGER_RTT_PATH={env_path} does not contain RTT/SEGGER_RTT.c",
            hint="Set SEGGER_RTT_PATH to the root dir containing RTT/ and Config/ subdirs.",
        )

    bundled = _bundled_segger_rtt_dir()
    if _is_segger_rtt_root(bundled):
        log.info("Using bundled SEGGER RTT target sources at %s", bundled)
        return bundled

    raise FirmwareError(
        "Bundled SEGGER RTT target sources are missing or incomplete.",
        hint="Reinstall helia-profiler or provide target.segger_rtt_path explicitly.",
    )


def _bundled_segger_rtt_dir() -> Path:
    """Return the pinned RTT target-source root shipped with heliaPROFILER."""
    return Path(__file__).resolve().parent.parent / "vendor" / "segger_rtt"


def _is_segger_rtt_root(path: Path) -> bool:
    root = path.expanduser()
    return (
        (root / "RTT" / "SEGGER_RTT.c").is_file()
        and (root / "RTT" / "SEGGER_RTT.h").is_file()
        and (root / "Config" / "SEGGER_RTT_Conf.h").is_file()
    )


def _copy_segger_rtt(dest_dir: Path, configured_path: Path | None = None) -> None:
    """Copy SEGGER RTT source files into *dest_dir*/rtt/."""
    rtt_root = find_segger_rtt_dir(configured_path)
    rtt_dest = dest_dir / "rtt"
    rtt_dest.mkdir(parents=True, exist_ok=True)

    # RTT source + headers. SEGGER_RTT.h includes SEGGER_RTT_ConfDefaults.h,
    # which in turn includes Config/SEGGER_RTT_Conf.h.
    for name in ("SEGGER_RTT.c", "SEGGER_RTT.h", "SEGGER_RTT_ConfDefaults.h"):
        src = rtt_root / "RTT" / name
        if src.exists():
            shutil.copy2(src, rtt_dest / name)

    # RTT buffer placement is cache-coherency sensitive on the Cortex-M55 parts.
    #
    # SEGGER RTT supports relocation via its SEGGER_RTT_SECTION hook: when
    # SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 (the default on Apollo parts) the
    # control block and buffers are declared through SEGGER_RTT_PUT_CB_SECTION /
    # SEGGER_RTT_PUT_BUFFER_SECTION, which emit
    # ``__attribute__((section(SEGGER_RTT_SECTION)))`` for GCC/clang.
    #
    # On the cacheless Cortex-M4 parts (Apollo3/4) there is no coherency hazard,
    # so we point that section at the NSX ``.sram_bss`` input section (collected
    # into SHARED_SRAM by the linker scripts) to keep SEGGER's large staging
    # buffers out of scarce MCU_TCM .bss.
    #
    # On the cache-coherent Cortex-M55 parts (Apollo5 / Apollo510 family) shared
    # SRAM is *cached*, and SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 tells RTT there is
    # no cache to work around. That combination is incoherent with J-Link's
    # asynchronous SWD reads/writes of the ring: the host can observe a stale
    # ring (old bytes published before the new payload reaches SRAM) or have its
    # up-buffer RdOff clobbered by the CPU's whole-cache clean, corrupting the
    # stream. We therefore keep the buffers in *non-cached* TCM (the default .bss
    # region) on these parts so SWD reads stay coherent with zero cache
    # maintenance — the configuration SEGGER RTT actually assumes.
    #
    # NOTE: do *not* try to rewrite the ``#if SEGGER_RTT_CPU_CACHE_LINE_SIZE``
    # aligned declarations — that branch is dead code here (the macro is 0), so
    # patching it has no effect on the compiled object.
    rtt_c = rtt_dest / "SEGGER_RTT.c"
    if rtt_c.exists():
        text = rtt_c.read_text(encoding="utf-8")
        if "SEGGER_RTT_PUT_CB_SECTION(" not in text or "SEGGER_RTT_PUT_BUFFER_SECTION(" not in text:
            raise FirmwareError(
                "Failed to patch SEGGER_RTT.c for SRAM placement",
                hint=(
                    "SEGGER_RTT.c does not use SEGGER_RTT_PUT_CB_SECTION / "
                    "SEGGER_RTT_PUT_BUFFER_SECTION; cannot place the RTT control "
                    "block and buffers in shared SRAM. Update the RTT patch "
                    "logic for this SEGGER RTT release."
                ),
            )

    # Config header — nested in Config/ subdir. Append the SEGGER_RTT_SECTION
    # definition so the buffers land in shared SRAM on parts that have a
    # dedicated .sram_bss region (NSX_MEM__HAS_SRAM_BSS); on simpler parts the
    # macro stays undefined and SEGGER falls back to the default .bss region.
    config_dest = rtt_dest / "Config"
    config_dest.mkdir(parents=True, exist_ok=True)
    conf_dest = config_dest / "SEGGER_RTT_Conf.h"
    conf_src = rtt_root / "Config" / "SEGGER_RTT_Conf.h"
    if conf_src.exists():
        conf_text = conf_src.read_text(encoding="utf-8")
    elif conf_dest.exists():
        # No vendor conf to copy from — keep whatever a previous generation
        # left in place rather than reducing it to the placement block alone.
        conf_text = conf_dest.read_text(encoding="utf-8")
    else:
        conf_text = ""

    sram_placement = (
        "\n"
        "/* heliaPROFILER: RTT control block + channel buffer placement.\n"
        " *\n"
        " * Cache-coherent Cortex-M55 parts (Apollo5 / Apollo510 family): keep the\n"
        " * buffers in NON-CACHED TCM (default .bss). Their shared SRAM is cached,\n"
        " * and SEGGER_RTT_CPU_CACHE_LINE_SIZE == 0 assumes no cache, so .sram_bss\n"
        " * placement is incoherent with J-Link's async SWD ring access (stale\n"
        " * reads / clobbered RdOff). TCM is not cached, so SWD stays coherent with\n"
        " * zero cache maintenance.\n"
        " *\n"
        " * Cacheless Cortex-M4 parts (Apollo3/4): no coherency hazard, so move the\n"
        " * large staging buffers into shared SRAM (.sram_bss) to spare MCU_TCM. */\n"
        '#include "nsx_mem.h"\n'
        "#if defined(AM_PART_APOLLO510) || defined(AM_PART_APOLLO510B) || \\\n"
        "    defined(AM_PART_APOLLO5A)  || defined(AM_PART_APOLLO5B)  || \\\n"
        "    defined(AM_PART_APOLLO510L) || defined(AM_PART_APOLLO330P)\n"
        "  /* Non-cached TCM: leave SEGGER_RTT_SECTION undefined (default .bss). */\n"
        "#elif NSX_MEM__HAS_SRAM_BSS\n"
        "  #ifndef SEGGER_RTT_SECTION\n"
        "    #define SEGGER_RTT_SECTION NSX_MEM__SEC_SRAM_BSS\n"
        "  #endif\n"
        "#endif\n"
    )
    if "SEGGER_RTT_SECTION" not in conf_text:
        conf_text += sram_placement
    # Content-compare before writing (see render._write_text): regeneration
    # into a cached workspace must not bump this header's mtime, or every
    # translation unit that includes it recompiles on every run.
    _write_text(conf_dest, conf_text)

    log.info("Copied SEGGER RTT source from %s", rtt_root)
