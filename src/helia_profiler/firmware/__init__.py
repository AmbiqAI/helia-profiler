"""Firmware generation — NSX app scaffolding for the profiler.

This module provides the interface between the pipeline stages and the
low-level firmware template rendering + NSX build system.  Each function
receives a ``PipelineContext`` and operates on the fields set by prior stages.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import BuildError, FirmwareError

if TYPE_CHECKING:
    from ..pipeline import PipelineContext


def generate_app(ctx: PipelineContext) -> Path:
    """Render Jinja templates into an NSX-compatible firmware app.

    Returns the path to the generated app directory inside ``ctx.work_dir``.
    """
    assert ctx.soc is not None
    assert ctx.engine_artifacts is not None

    app_dir = ctx.work_dir / "profiler_app"
    app_dir.mkdir(parents=True, exist_ok=True)

    # TODO: Load Jinja templates from package data
    # TODO: Render main.cc, module.yaml, CMakeLists.txt using:
    #   - ctx.config (model path, profiling settings, PMU presets)
    #   - ctx.soc (c_define, memory layout, PMU tier)
    #   - ctx.engine_artifacts.template_vars (engine-specific vars)
    # TODO: Copy model .tflite into app_dir/src/

    raise FirmwareError(
        "Firmware generation not yet implemented.",
        hint="This feature is under development.",
    )


def build_app(ctx: PipelineContext) -> tuple[Path, Path]:
    """Invoke ``nsx configure`` + ``nsx build`` on the generated app.

    Returns (build_dir, binary_path).
    """
    assert ctx.firmware_dir is not None

    # TODO: subprocess.run(["nsx", "configure", ...], check=True)
    # TODO: subprocess.run(["nsx", "build", ...], check=True)
    # TODO: Locate the .bin / .axf output

    raise BuildError(
        "Firmware build not yet implemented.",
        hint="This feature is under development.",
    )


def flash_app(ctx: PipelineContext) -> None:
    """Invoke ``nsx flash`` or JLink to deploy the binary to the target."""
    assert ctx.binary_path is not None

    # TODO: subprocess.run(["nsx", "flash", ...], check=True)
    #   or direct JLink commander invocation

    raise BuildError(
        "Firmware flash not yet implemented.",
        hint="This feature is under development.",
    )
