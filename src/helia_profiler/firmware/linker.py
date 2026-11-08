"""Refresh configuration and bind the current executable to its verified linker input."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..deps import nsx as nsx_cli
from ..deps.dependencies import read_dependency_lock_provenance
from ..errors import LockError
from ..placement import admit_memory_plan
from ..platform.linker.model import LinkerVerification
from .linker_source import request_linker_information, verify_configured_linker

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

log = logging.getLogger("hpx")


def configure_linker(
    ctx: PipelineContext, *, build_dir: Path, role: Literal["profile", "power"], target: str
) -> LinkerVerification:
    """Called under the workspace mutex before every normal or incremental link."""
    from .build import nsx_toolchain

    def record(value: LinkerVerification) -> None:
        ctx.run_metadata.linker_verifications = (
            *(v for v in ctx.run_metadata.linker_verifications if v.role != role),
            value,
        )

    record(
        LinkerVerification(role, target, None, "Configuration has not completed for this target")
    )
    request_linker_information(build_dir)
    nsx_cli.configure(
        ctx.resolved_firmware_dir,
        toolchain=nsx_toolchain(ctx.config.target.toolchain),
        frozen=True,
        timeout_s=ctx.config.timeouts.configure_s,
        verbose=ctx.config.verbose,
    )
    platform = ctx.run_metadata.platform_provenance
    try:
        current = read_dependency_lock_provenance(ctx.resolved_firmware_dir)
        result = verify_configured_linker(
            app_dir=ctx.resolved_firmware_dir,
            build_dir=build_dir,
            modules=current.resolved,
            expected=platform.expected_linker if platform is not None else None,
            role=role,
            target=target,
        )
    except (LockError, ValueError, RecursionError) as exc:
        result = LinkerVerification(role, target, None, str(exc))
    record(result)
    if result.layout is None:
        log.warning("%s linker fit/accounting unavailable: %s", target, result.reason)
        return result
    declared = ctx.declared_memory_plan or ctx.memory_plan
    if declared is not None:
        admitted = admit_memory_plan(declared, result.layout)
        if role == "profile":
            ctx.memory_plan = admitted
            ctx.run_metadata.memory_plan = admitted
    return result
