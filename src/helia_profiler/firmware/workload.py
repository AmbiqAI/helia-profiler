"""Workload identity from the rendered source selected for a measured window."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..power.diagnostics import window_inference_count

if TYPE_CHECKING:
    from ..pipeline import PipelineContext

AOT_CLEAN_WORKLOAD = "aot_raw_zero_refill_included_v1"


def measured_clean_workload(ctx: PipelineContext, *, power: bool = False) -> str | None:
    """Read the generated workload declaration; absent sources remain unknown.

    Like the firmware fingerprint, this identifies rendered source, not binary
    attestation. Never infer old artifact semantics from the installed version.
    """
    if ctx.firmware_dir is None or ctx.config.engine.type.value != "helia-aot":
        return None
    filename = "main.cc"
    if power:
        if ctx.power_result is None or ctx.power_run is None or not window_inference_count(ctx):
            return None
        if ctx.power_run.plan.firmware_mode == "dedicated":
            filename = "main_power.cc"
    elif ctx.pmu_result is None or not ctx.pmu_result.meta.clean_infer_count:
        return None
    try:
        source = (ctx.firmware_dir / "src" / filename).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    declaration = f'#define HPX_CLEAN_WORKLOAD "{AOT_CLEAN_WORKLOAD}"'
    return AOT_CLEAN_WORKLOAD if declaration in source.splitlines() else None
