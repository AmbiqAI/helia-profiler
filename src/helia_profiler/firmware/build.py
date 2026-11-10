"""Build invocation — ``nsx configure/build`` on the generated app.

Owns the NSX build-invocation vocabulary: the config→``nsx --toolchain``
mapping, the compile-time RTT up-buffer sizing, the build itself, and the
deterministic target-binary search.  The package re-exports every name so
callers (stages/build_firmware, stages/build_power_firmware,
stages/plan_memory) keep one import surface.  Flashing does not go through
NSX — both firmware deployments run the NSX-generated J-Link recipe
directly (target/probe/flash.flash_binary).

NOTE: ``nsx_cli`` and ``glob`` are imported as modules (never ``from ... import
build`` / ``from glob import glob``) so tests that monkeypatch
``helia_profiler.firmware.nsx_cli.*`` and ``helia_profiler.firmware.glob.glob``
keep patching the same module objects this code reads at call time.
"""

from __future__ import annotations

import glob
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..config import Transport
from ..deps import nsx as nsx_cli
from ..errors import BuildError, ImageTooLargeError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


_DEFAULT_RTT_BUFFER_SIZE_UP = 32768


def nsx_toolchain(toolchain: str) -> str | None:
    """Convert a config toolchain name to the ``nsx --toolchain`` value.

    Returns *None* for the default (GCC) so the flag is omitted.
    """
    from ..hostenv.toolchains import get_toolchain_spec

    return get_toolchain_spec(toolchain).nsx_name


def rtt_buffer_size_up(toolchain: str, transport: Transport, configured_size: int | None) -> int:
    """Return the compile-time SEGGER RTT up-buffer size for generated apps."""
    if configured_size is not None:
        return configured_size
    if transport == Transport.RTT:
        from ..hostenv.toolchains import get_toolchain_spec

        return get_toolchain_spec(toolchain).default_rtt_buffer_size_up
    return _DEFAULT_RTT_BUFFER_SIZE_UP


def build_app(ctx: PipelineContext) -> tuple[Path, Path]:
    """Invoke ``nsx configure`` + ``nsx build`` on the generated app.

    Returns (build_dir, binary_path).
    """
    app_dir = ctx.resolved_firmware_dir
    board = ctx.resolved_board.name
    timeouts = ctx.config.timeouts
    toolchain = ctx.config.target.toolchain
    verbose = ctx.config.verbose

    nsx_tc = nsx_toolchain(toolchain)
    build_dir = app_dir / "build" / board

    from ..deps.dependencies import (
        prepare_locked_dependencies,
        workspace_mutex,
    )
    from ..deps.sync import invalidate_sync_stamp

    with workspace_mutex(ctx.resolved_workspace):
        prepare_locked_dependencies(ctx)
        try:
            from .linker import configure_linker

            configure_linker(ctx, build_dir=build_dir, role="profile", target="hpx_profiler")
            try:
                nsx_cli.build(
                    app_dir, toolchain=nsx_tc, timeout_s=timeouts.build_s, verbose=verbose
                )
            except BuildError as exc:
                translated = region_overflow_error(exc)
                if translated is None:
                    raise
                raise translated from exc

            # Prefer the ELF-form executable: later reporting stages run
            # size tools against it to capture text/data/bss.
            binary_path = find_target_binary(build_dir, "hpx_profiler")
            if binary_path is None:
                raise BuildError(
                    "Build succeeded but binary not found",
                    hint=f"Searched in {build_dir}",
                )
        except BuildError:
            # A failed build may have damaged the materialization, so the next
            # run must verify dependencies instead of trusting its sync stamp.
            invalidate_sync_stamp(app_dir)
            raise

    log.info("Binary: %s", binary_path)

    return build_dir, binary_path


# lld: "section '.text' will not fit in region 'MCU_MRAM': overflowed by 434308 bytes"
# GNU ld: "region `MCU_MRAM' overflowed by 434308 bytes"
_REGION_OVERFLOW = re.compile(
    r"region [`']?(?P<region>[A-Za-z0-9_]+)'?:? overflowed by (?P<bytes>\d+) bytes"
)
# armlink: "L6220E: Load region LR_1 size (1600904 bytes) exceeds limit (1589248 bytes)"
_ARMLINK_OVERFLOW = re.compile(
    r"L6220E: (?P<kind>Load|Execution) region (?P<region>[A-Za-z0-9_]+) "
    r"size \((?P<size>\d+) bytes\) exceeds limit \((?P<limit>\d+) bytes\)"
)


#: Linker regions that hold code and weights: MRAM on Apollo4/5, NOR flash
#: ("ROMEM") on Apollo3.
_NONVOLATILE_REGIONS = ("MRAM", "ROMEM", "FLASH")


def _find_overflow(text: str) -> tuple[str, int, bool] | None:
    """Region, positive overflow bytes and nonvolatile status; prefer RAM failures."""
    nonvolatile_overflow = None
    for pattern in (_REGION_OVERFLOW, _ARMLINK_OVERFLOW):
        for match in pattern.finditer(text):
            region = match["region"]
            is_load = pattern is _ARMLINK_OVERFLOW and match["kind"] == "Load"
            overflow = (
                int(match["bytes"])
                if pattern is _REGION_OVERFLOW
                else int(match["size"]) - int(match["limit"])
            )
            if overflow <= 0:
                continue
            nonvolatile = is_load or any(kind in region.upper() for kind in _NONVOLATILE_REGIONS)
            found = region, overflow, nonvolatile
            if not nonvolatile:
                return found
            if nonvolatile_overflow is None:
                nonvolatile_overflow = found
    return nonvolatile_overflow


def region_overflow_error(exc: BuildError) -> BuildError | None:
    """A linker region overflow as a placement error, or ``None`` for other failures.

    Nonvolatile-only overflow is an :class:`ImageTooLargeError`, the same
    outcome as the post-build flash check. Any reported RAM overflow takes
    precedence so validation does not skip a configuration failure.
    """
    found = _find_overflow(f"{exc.details or ''}\n{exc}")
    if found is None:
        return None
    region, overflow, nonvolatile = found
    cls, hint = (
        (
            ImageTooLargeError,
            f"The model weights plus the runtime and application code exceed the {region} "
            "space the linker can use. Use a smaller model, or place weights in PSRAM "
            "(--weights-location psram) on a board that has PSRAM.",
        )
        if nonvolatile
        else (BuildError, "Reduce the arena size or choose another --arena-location.")
    )
    return cls(
        f"The linked image does not fit in {region}: overflowed by {overflow:,} bytes",
        hint=hint,
        returncode=exc.returncode,
        details=exc.details,
    )


def find_target_binary(build_dir: Path, target_name: str) -> Path | None:
    """Locate a built NSX target's executable/binary under ``build_dir``.

    Mirrors the existing hpx_profiler artifact search so hpx_profiler_power
    (or any future target) resolves the same way across toolchains/layouts.
    """
    artifact_patterns = [
        str(build_dir / target_name),
        str(build_dir / "**" / target_name),
        str(build_dir / "**" / f"{target_name}.axf"),
        str(build_dir / "**" / f"{target_name}.elf"),
        str(build_dir / f"{target_name}.bin"),
        str(build_dir / "**" / f"{target_name}.bin"),
    ]
    for pattern in artifact_patterns:
        # sorted(): glob order is filesystem-dependent, so a build tree
        # with two candidates (stale + fresh subdir) could resolve
        # differently across machines/runs. Deterministic pick — shortest
        # path first, ties lexicographic — so the shallowest match wins
        # reproducibly.
        matches = sorted(
            (m for m in glob.glob(pattern, recursive=True) if Path(m).is_file()),
            key=lambda m: (len(Path(m).parts), m),
        )
        if matches:
            return Path(matches[0])
    return None
