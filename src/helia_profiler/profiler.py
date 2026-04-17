"""Top-level profiling orchestrator."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .config import ProfileConfig
from .engines import EngineType
from .engines.base import EngineAdapter
from .platform import PmuTier, get_soc_for_board


def _get_adapter(engine_type: EngineType) -> EngineAdapter:
    """Return the engine adapter for the given type."""
    if engine_type is EngineType.TFLM:
        from .engines.tflm import TFLMAdapter

        return TFLMAdapter()
    elif engine_type is EngineType.HELIA_RT:
        from .engines.helia_rt import HeliaRTAdapter

        return HeliaRTAdapter()
    elif engine_type is EngineType.HELIA_AOT:
        from .engines.helia_aot import HeliaAOTAdapter

        return HeliaAOTAdapter()
    else:
        raise ValueError(f"Unknown engine type: {engine_type}")


def run_profile(config: ProfileConfig) -> None:
    """Execute the full profiling pipeline.

    Steps:
    1. Resolve working directory.
    2. Run engine adapter to prepare engine-specific artifacts.
    3. Generate profiler firmware as an NSX app.
    4. Build and flash firmware via NSX pipeline.
    5. Capture PMU data from target.
    6. Optionally capture power data via Joulescope.
    7. Generate report.
    8. Clean up working directory (unless --keep-work-dir).
    """
    work_dir: Path
    should_cleanup = False

    if config.work_dir is not None:
        work_dir = config.work_dir.resolve()
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        work_dir = Path(tempfile.mkdtemp(prefix="hpx_"))
        should_cleanup = not config.keep_work_dir

    try:
        _log(config, f"Working directory: {work_dir}")

        # 0. Resolve platform
        soc = get_soc_for_board(config.target.board)
        _log(config, f"Board: {config.target.board}  SoC: {soc.name} ({soc.core.value})")

        if soc.pmu_tier is PmuTier.DWT_ONLY:
            _log(
                config,
                f"Warning: {soc.name} has DWT-only profiling (no Armv8-M PMU). "
                "Per-layer PMU breakdowns will be limited to cycle counts.",
            )

        # 1. Engine preparation
        adapter = _get_adapter(config.engine.type)
        _log(config, f"Engine: {adapter.name}")
        artifacts = adapter.prepare(config, work_dir)  # noqa: F841 — used once firmware gen is wired

        # 2. Generate firmware
        _log(config, "Generating profiler firmware...")
        # TODO: firmware.app_gen.generate(config, artifacts, work_dir)

        # 3. Build and flash
        _log(config, "Building firmware...")
        # TODO: call nsx configure + build + flash

        # 4. Capture PMU data
        _log(config, "Capturing PMU data...")
        # TODO: capture.serial + capture.pmu

        # 5. Power capture (optional)
        if config.power.enabled:
            _log(config, "Capturing power data...")
            # TODO: capture.power

        # 6. Generate report
        _log(config, "Generating report...")
        # TODO: report generation

        _log(config, "Done.")

    finally:
        if should_cleanup:
            shutil.rmtree(work_dir, ignore_errors=True)


def _log(config: ProfileConfig, msg: str) -> None:
    """Print a message if verbosity allows."""
    if config.verbose >= 0:
        print(f"[hpx] {msg}")
