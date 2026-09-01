"""Build/flash invocation — ``nsx configure/build/flash`` on the generated app.

Owns the NSX build-invocation vocabulary: the config→``nsx --toolchain``
mapping, the compile-time RTT up-buffer sizing, the build itself, the
deterministic target-binary search, and flashing.  Extracted from
``firmware/__init__`` at the module size ceiling (see the elf_inventory
precedent in toolchain_probe); the package re-exports every name so callers
(stages/build_firmware, stages/build_power_firmware, stages/plan_memory)
keep one import surface.  Flashing is NOT invoked through here by the
pipeline anymore — both firmware deployments run the NSX-generated J-Link
recipe directly (target/probe/flash.flash_binary); ``flash_app`` remains as
the ``nsx flash`` convenience wrapper for callers outside the pipeline.

NOTE: ``nsx_cli`` and ``glob`` are imported as modules (never ``from ... import
build`` / ``from glob import glob``) so tests that monkeypatch
``helia_profiler.firmware.nsx_cli.*`` and ``helia_profiler.firmware.glob.glob``
keep patching the same module objects this code reads at call time.
"""

from __future__ import annotations

import glob
import logging
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

from ..deps import nsx as nsx_cli
from ..config import Transport
from ..errors import BuildError

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

    # Map config toolchain names to nsx CLI values
    nsx_tc = nsx_toolchain(toolchain)
    build_dir = app_dir / "build" / board
    ninja_already_configured = (build_dir / "build.ninja").exists()

    from ..deps.dependencies import (
        invalidate_sync_stamp,
        prepare_locked_dependencies,
        workspace_mutex,
    )

    with workspace_mutex(ctx.resolved_workspace):
        dependency_state = prepare_locked_dependencies(ctx)
        try:
            if (
                not ninja_already_configured
                or dependency_state.lock.mode.value != "reused"
            ):
                nsx_cli.configure(
                    app_dir,
                    toolchain=nsx_tc,
                    frozen=True,
                    timeout_s=timeouts.configure_s,
                    verbose=verbose,
                )
            else:
                # CMake's regeneration rule handles deterministic source/template
                # changes; dependency verification already ran via sync --frozen.
                log.info("Reusing configured deterministic workspace: %s", build_dir)
            nsx_cli.build(app_dir, toolchain=nsx_tc, timeout_s=timeouts.build_s, verbose=verbose)

            # Locate build output. Prefer the ELF-form executable because
            # later reporting stages run size tools against it to capture
            # text/data/bss.
            binary_path = find_target_binary(build_dir, "hpx_profiler")
            if binary_path is None:
                raise BuildError(
                    "Build succeeded but binary not found",
                    hint=f"Searched in {build_dir}",
                )
        except BuildError:
            # Any build-stage failure — configure, compile, or the artifact
            # lookup — may mean the workspace is corrupted in a way the
            # stamped skip above no longer checks for; drop the stamp so the
            # next run pays full frozen verification (and repair) again
            # instead of skipping straight back into the same failure.
            invalidate_sync_stamp(app_dir)
            raise

    log.info("Binary: %s", binary_path)

    return build_dir, binary_path


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


def flash_app(ctx: PipelineContext) -> None:
    """Invoke ``nsx flash`` to deploy the binary to the target."""
    firmware_dir = ctx.resolved_firmware_dir
    toolchain = ctx.config.target.toolchain
    nsx_tc = nsx_toolchain(toolchain)
    from ..deps.dependencies import workspace_mutex

    lock = (
        workspace_mutex(ctx.dependency_workspace)
        if ctx.dependency_workspace is not None
        else nullcontext()
    )
    with lock:
        nsx_cli.flash(
            firmware_dir,
            toolchain=nsx_tc,
            jlink_serial=ctx.resolved_jlink_serial or ctx.config.target.jlink_serial,
            frozen=True,
            timeout_s=ctx.config.timeouts.flash_s,
            verbose=ctx.config.verbose,
        )
