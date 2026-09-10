"""Run fail-fast configuration and domain checks without touching hardware."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import ProfileConfig, Transport
from ..engines.preflight import check_profiling_support, check_runtime_split_locations
from ..errors import ConfigError
from ..engines.model_validation import check_model, check_softmax_scaling
from ..pipeline import PipelineContext
from ..platform.preflight import check_pmu_selection, check_usb_support

log = logging.getLogger("hpx")


class PreflightStage:
    @property
    def name(self) -> str:
        return "preflight"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        check_model(cfg.model.path, cfg.engine.type)
        check_softmax_scaling(cfg.model.path, cfg.engine.type)
        _check_arena_size(cfg.model.arena_size)
        _check_rtt_buffer_size(cfg.target.rtt_buffer_size_up)
        check_runtime_split_locations(cfg)
        check_pmu_selection(
            cfg.target.board, cfg.profiling.pmu_counters, registry=cfg.platform_registry
        )
        check_profiling_support(
            cfg.engine.type,
            power_enabled=cfg.power.enabled,
            clean_window_probe=cfg.profiling.clean_window_probe,
        )
        if cfg.target.transport == Transport.USB_CDC:
            check_usb_support(cfg.target.board, registry=cfg.platform_registry)
        _check_output_dir(cfg.output.dir)
        _check_host_tools(cfg)
        log.info("Preflight checks passed.")


def _check_arena_size(arena_size: int | None) -> None:
    if arena_size is None:
        return
    if arena_size <= 0:
        raise ConfigError(
            f"model.arena_size must be positive (got {arena_size}).",
            hint="Leave arena_size unset to let the engine choose, or set a positive byte count.",
        )


def _check_rtt_buffer_size(size: int | None) -> None:
    if size is None:
        return
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ConfigError(
            f"target.rtt_buffer_size_up must be a positive integer (got {size!r}).",
            hint="Set target.rtt_buffer_size_up to a positive byte count, or leave it unset to use the toolchain-aware default.",
        )


def _check_output_dir(out_dir: Path) -> None:
    resolved = out_dir.expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Cannot create output directory: {resolved} ({exc})",
            hint="Check output.dir — the parent must be writable.",
        ) from exc
    # Probe writability even when the directory already exists.
    probe = resolved / ".hpx_write_probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise ConfigError(
            f"Output directory is not writable: {resolved} ({exc})",
            hint="Point output.dir to a writable location.",
        ) from exc


def _check_host_tools(cfg: ProfileConfig) -> None:
    from ..hostenv.doctor import inspect_environment

    result = inspect_environment(
        toolchain=cfg.target.toolchain,
        transport=cfg.target.transport,
        engine=cfg.engine.type,
    )
    if result.ok:
        return
    missing = "\n".join(
        f"  - {check.name}: {check.hint or 'Install this dependency.'}"
        for check in result.missing_required
    )
    raise ConfigError(
        "Required host dependencies are missing.",
        hint=f"Install the following and re-run:\n{missing}\nRun 'hpx doctor' for details.",
    )
