"""DEPRECATED: target lifecycle moved to :mod:`helia_profiler.target.lifecycle`."""

from __future__ import annotations

from .target.lifecycle import (
    CapturePhase,
    ResetAction,
    ResetStrategy,
    TargetLifecyclePlan,
    prepare_target_for_phase,
)

__all__ = [
    "CapturePhase",
    "ResetAction",
    "ResetStrategy",
    "TargetLifecyclePlan",
    "prepare_target_for_phase",
]
