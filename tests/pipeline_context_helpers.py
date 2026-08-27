"""Test helpers for populating typed pipeline records."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.pipeline import PipelineContext
from helia_profiler.power.base import PowerResult
from helia_profiler.power.metadata import PowerIntegrity, classify_observation
from helia_profiler.results import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerObservation,
    PowerRun,
    PowerRunPlan,
    ProfileRun,
    PmuResult,
)

_MISSING = object()


def set_profile_firmware(
    ctx: PipelineContext,
    *,
    artifact: FirmwareArtifact | None = None,
    build_dir: Path | None = None,
    binary_path: Path | None | object = _MISSING,
) -> None:
    if binary_path is None:
        ctx.profile_run = None
        return

    existing = ctx.profile_run
    if artifact is None:
        existing_fw = existing.firmware if existing is not None else None
        resolved_build_dir = build_dir or (
            existing_fw.build_dir if existing_fw is not None else ctx.work_dir / "build"
        )
        resolved_binary_path = (
            binary_path
            if binary_path is not _MISSING
            else (
                existing_fw.binary_path
                if existing_fw is not None
                else resolved_build_dir / "hpx_profiler"
            )
        )
        artifact = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=ctx.firmware_dir or ctx.work_dir,
            build_dir=resolved_build_dir,
            binary_path=resolved_binary_path,  # type: ignore[arg-type]
            binary_sections=ctx.binary_sections,
        )

    deployment = (
        existing.deployment
        if existing is not None
        and existing.deployment is not None
        and existing.deployment.firmware == artifact
        else None
    )
    result = existing.result if existing is not None else None
    ctx.profile_run = ProfileRun(firmware=artifact, deployment=deployment, result=result)


def set_profile_result(ctx: PipelineContext, result: PmuResult | None) -> None:
    if result is None:
        if ctx.profile_run is not None:
            ctx.profile_run = replace(ctx.profile_run, result=None)
        return

    if ctx.profile_run is None:
        set_profile_firmware(ctx)
    assert ctx.profile_run is not None
    ctx.profile_run = replace(ctx.profile_run, result=result)


def clear_profile_run(ctx: PipelineContext) -> None:
    ctx.profile_run = None


def set_power_plan(ctx: PipelineContext, plan: PowerRunPlan) -> None:
    ctx.power_run = PowerRun(plan=plan)


def set_power_firmware(
    ctx: PipelineContext,
    *,
    artifact: FirmwareArtifact | None = None,
    binary_path: Path | None | object = _MISSING,
) -> None:
    if binary_path is None:
        if ctx.power_run is not None:
            ctx.power_run = replace(ctx.power_run, firmware=None, deployment=None, observation=None)
        return

    existing = ctx.power_run
    plan = existing.plan if existing is not None else PowerRunPlan(firmware_mode="dedicated")
    if artifact is None:
        resolved_binary_path = (
            binary_path
            if binary_path is not _MISSING
            else ctx.work_dir / "build" / "hpx_profiler_power"
        )
        artifact = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=ctx.firmware_dir or ctx.work_dir,
            build_dir=ctx.build_dir or ctx.work_dir / "build",
            binary_path=resolved_binary_path,  # type: ignore[arg-type]
        )

    deployment = (
        existing.deployment
        if existing is not None
        and existing.deployment is not None
        and existing.deployment.firmware == artifact
        else None
    )
    observation = existing.observation if existing is not None else None
    ctx.power_run = PowerRun(
        plan=plan,
        firmware=artifact,
        deployment=deployment,
        observation=observation,
        terminal=existing.terminal if existing is not None else None,
        on_device_summary=existing.on_device_summary if existing is not None else None,
    )


def set_power_deployment(ctx: PipelineContext, deployment: DeploymentRecord | None = None) -> None:
    if ctx.power_run is None or ctx.power_run.firmware is None:
        set_power_firmware(ctx)
    assert ctx.power_run is not None and ctx.power_run.firmware is not None
    deployment = deployment or DeploymentRecord(
        firmware=ctx.power_run.firmware,
        target_id=ctx.config.target.board,
        deployed_at="2026-01-01T00:00:00+00:00",
    )
    ctx.power_run = replace(ctx.power_run, deployment=deployment)


def set_power_result(ctx: PipelineContext, result: PowerResult | None) -> None:
    if result is None:
        if ctx.power_run is not None:
            ctx.power_run = replace(ctx.power_run, observation=None)
        return

    if ctx.power_run is None:
        ctx.power_run = PowerRun(plan=PowerRunPlan(firmware_mode="shared"))

    mode, integrity, rise, fall, deadline = classify_observation(result.metadata)
    observation = PowerObservation(
        mode=result.metadata.observation_mode or mode,
        result=result,
        gate_rise_observed=(
            result.metadata.gate_rise_observed
            if result.metadata.gate_rise_observed is not None
            else rise
        ),
        gate_fall_observed=(
            result.metadata.gate_fall_observed
            if result.metadata.gate_fall_observed is not None
            else fall
        ),
        deadline_s=float(
            result.metadata.observation_deadline_s
            if result.metadata.observation_deadline_s is not None
            else deadline
            if deadline is not None
            else result.summary.duration_s
        ),
        integrity=PowerIntegrity(result.metadata.integrity or integrity),
    )
    ctx.power_run = replace(ctx.power_run, observation=observation)


def clear_power_run(ctx: PipelineContext) -> None:
    ctx.power_run = None
