"""Stage 1 — Resolve platform: validate board/SoC and enrich context."""

from __future__ import annotations

import hashlib
import logging

from ..errors import ConfigError, PlatformError
from ..pipeline import PipelineContext
from ..platform import PmuTier, get_board, get_soc_for_board
from ..results import ModelInfo, PlatformInfo

log = logging.getLogger("hpx")


class ResolvePlatformStage:
    @property
    def name(self) -> str:
        return "resolve_platform"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        board_name = ctx.config.target.board
        if not board_name:
            raise ConfigError(
                "No target board specified.",
                hint="Set 'target.board' in hpx.yml or pass --board on the CLI.",
            )

        try:
            board = get_board(board_name, registry=ctx.config.platform_registry)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        try:
            soc = get_soc_for_board(board_name, registry=ctx.config.platform_registry)
        except ValueError as exc:
            raise PlatformError(
                f"Board '{board_name}' references unknown SoC '{board.soc}'.",
                hint="This is likely a bug in the platform registry.",
            ) from exc

        ctx.board = board
        ctx.soc = soc

        # --- Resolve per-domain clock selection ---------------------------
        selection = ctx.config.target.clock

        cpu_domain = soc.cpu_clock
        cpu_name = selection.cpu or cpu_domain.default
        cpu_speed = cpu_domain.speed(cpu_name)
        if cpu_speed is None:
            raise ConfigError(
                f"Board '{board_name}' does not support cpu clock '{cpu_name}'.",
                hint=(f"Supported cpu speeds for {soc.name}: {', '.join(cpu_domain.speed_names)}."),
            )
        if cpu_speed.perf_tier is None:
            raise PlatformError(
                f"cpu clock '{cpu_name}' on {soc.name} has no NSX perf tier.",
                hint="This is likely a bug in the platform registry.",
            )

        log.info(
            "Board: %s  SoC: %s (%s, backends=%s)",
            board.name,
            soc.name,
            soc.core.value,
            ", ".join(soc.profiling_backends),
        )
        log.info(
            "Clock: cpu=%s (%d MHz, %s)",
            cpu_speed.name,
            cpu_speed.mhz,
            cpu_speed.perf_tier.value,
        )

        if soc.pmu_tier is PmuTier.DWT_ONLY:
            log.warning(
                "%s has DWT-only profiling (no Armv8-M PMU). "
                "Per-layer PMU breakdowns will be limited to cycle counts.",
                soc.name,
            )

        # Populate platform metadata
        ctx.run_metadata.platform = PlatformInfo(
            board=board.name,
            soc=soc.name,
            core=soc.core.value,
            pmu_tier=soc.pmu_tier.value,
            has_mve=soc.has_mve,
            profiling_backends=list(soc.profiling_backends),
            profiling_domains=list(soc.profiling_domains),
            cpu_clock_name=cpu_speed.name,
            cpu_clock_mhz=cpu_speed.mhz,
            cpu_perf_tier=cpu_speed.perf_tier.value,
        )

        # Validate model path exists early
        model_path = ctx.config.model.path
        if not model_path.exists():
            raise ConfigError(
                f"Model file not found: {model_path}",
                hint="Check the 'model.path' in your config or positional argument.",
            )

        # Record model file metadata
        model_bytes = model_path.read_bytes()
        ctx.run_metadata.model = ModelInfo(
            name=model_path.name,
            size_bytes=len(model_bytes),
            sha256=hashlib.sha256(model_bytes).hexdigest(),
        )
        ctx.report_progress(
            f"{board.name} at {cpu_speed.mhz} MHz · {model_path.name}",
            kind="checkpoint",
            min_verbosity=1,
        )
