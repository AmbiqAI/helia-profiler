"""Shared fixtures/helpers for the WP0 architectural contract tests.

Everything here mocks external tools at the same boundaries the existing
suite uses (see ``tests/test_rtt_reader.py`` and ``tests/test_power.py``):

* J-Link reset ownership is observed by monkeypatching ``reset_target`` /
  ``reset_target_poi`` and ``attached_reset_session`` at their import sites.
* Reader dispatch is observed by monkeypatching the per-transport
  ``capture_*_output`` functions and inspecting their kwargs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage

# A minimal, fully parseable firmware capture: one preset, one iteration,
# one CONV_2D layer, framed by the protocol sentinels.  Mirrors the canned
# stream used in ``tests/test_rtt_reader.py``.
CANNED_PMU_LINES: list[str] = [
    "--- HPX_START ---",
    "--- HPX_PRESET basic_cpu ---",
    "--- HPX_ITER 0 ---",
    "Layer,Op,ARM_PMU_CPU_CYCLES",
    "0,CONV_2D,1",
    "--- HPX_END ---",
]

# Representative board per SoC family used across the contracts.
BOARD_FOR_FAMILY: dict[str, str] = {
    "ap3": "apollo3p_evb",
    "ap4": "apollo4p_evb",
    "ap5": "apollo510_evb",
}


def make_pmu_ctx(
    tmp_path: Path,
    *,
    board: str,
    transport: str = "rtt",
    engine: str = "helia-rt",
    power_enabled: bool = False,
    reset_strategy: str = "auto",
    lockstep: bool = False,
    extra: dict | None = None,
) -> PipelineContext:
    """Build a resolved :class:`PipelineContext` ready for a capture stage."""
    model = tmp_path / "model.tflite"
    if not model.exists():
        model.write_bytes(b"\x00")
    overrides: dict = {
        "model": {"path": str(model)},
        "engine": {"type": engine},
        "target": {"board": board, "transport": transport},
        "power": {
            "enabled": power_enabled,
            "reset_strategy": reset_strategy,
            "lockstep": lockstep,
        },
    }
    if lockstep:
        # lockstep validation requires both state and go pins > 0
        overrides["power"].update({"state_gpio_pin": 23, "go_gpio_pin": 24})
    if extra:
        for key, value in extra.items():
            overrides.setdefault(key, {}).update(value)
    config = load_config(None, overrides)
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ResolvePlatformStage().run(ctx)
    ctx.build_dir = tmp_path / "build"
    ctx.build_dir.mkdir(exist_ok=True)
    ctx.resolved_jlink_serial = "1160002204"
    ctx.weights_region = "mram"
    return ctx


@pytest.fixture()
def pmu_ctx_factory(tmp_path: Path):
    """Return a factory building resolved capture contexts under ``tmp_path``."""

    def _factory(**kwargs) -> PipelineContext:
        return make_pmu_ctx(tmp_path, **kwargs)

    return _factory
