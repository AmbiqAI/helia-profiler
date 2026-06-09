"""Stage 1 — Resolve platform: validate board/SoC and enrich context."""

from __future__ import annotations

import hashlib
import logging

from ..errors import ConfigError, PlatformError
from ..pipeline import PipelineContext
from ..platform import ClockSpeed, PmuTier, get_board, get_soc_for_board
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
            board = get_board(board_name)
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        try:
            soc = get_soc_for_board(board_name)
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
                hint=(
                    f"Supported cpu speeds for {soc.name}: "
                    f"{', '.join(cpu_domain.speed_names)}."
                ),
            )
        if cpu_speed.perf_tier is None:
            raise PlatformError(
                f"cpu clock '{cpu_name}' on {soc.name} has no NSX perf tier.",
                hint="This is likely a bug in the platform registry.",
            )

        npu_domain = soc.clock_domain("npu")
        npu_speed: ClockSpeed | None = None
        if selection.npu is not None:
            if npu_domain is None:
                raise ConfigError(
                    f"Board '{board_name}' has no NPU clock domain.",
                    hint=f"{soc.name} does not expose a separate NPU clock.",
                )
            npu_speed = npu_domain.speed(selection.npu)
            if npu_speed is None:
                raise ConfigError(
                    f"Board '{board_name}' does not support npu clock "
                    f"'{selection.npu}'.",
                    hint=(
                        f"Supported npu speeds for {soc.name}: "
                        f"{', '.join(npu_domain.speed_names)}."
                    ),
                )
        elif npu_domain is not None:
            npu_speed = npu_domain.default_speed

        log.info(
            "Board: %s  SoC: %s (%s, backends=%s)",
            board.name,
            soc.name,
            soc.core.value,
            ", ".join(soc.profiling_backends),
        )
        log.info(
            "Clock: cpu=%s (%d MHz, %s)%s",
            cpu_speed.name,
            cpu_speed.mhz,
            cpu_speed.perf_tier.value,
            f"  npu={npu_speed.name} ({npu_speed.mhz} MHz)" if npu_speed else "",
        )
        if npu_speed is not None:
            log.info(
                "NPU clock is recorded in metadata but not yet applied by "
                "firmware (no NSX NPU clock API)."
            )

        if soc.pmu_tier is PmuTier.DWT_ONLY:
            log.warning(
                "%s has DWT-only profiling (no Armv8-M PMU). "
                "Per-layer PMU breakdowns will be limited to cycle counts.",
                soc.name,
            )

        if soc.has_npu:
            log.info(
                "%s also exposes accelerator profiling domains: %s",
                soc.name,
                ", ".join(domain for domain in soc.profiling_domains if domain != "cpu"),
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
            npu=soc.npu.value if soc.npu is not None else None,
            cpu_clock_name=cpu_speed.name,
            cpu_clock_mhz=cpu_speed.mhz,
            cpu_perf_tier=cpu_speed.perf_tier.value,
            npu_clock_name=npu_speed.name if npu_speed is not None else None,
            npu_clock_mhz=npu_speed.mhz if npu_speed is not None else None,
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
