"""hpx CLI — Profile LiteRT models on Ambiq Apollo hardware."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ._version import __version__
from .engines import EngineType


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hpx",
        description="Profile LiteRT models on Ambiq Apollo hardware.",
    )
    parser.add_argument("--version", action="version", version=f"hpx {__version__}")
    sub = parser.add_subparsers(dest="command")

    # --- hpx profile ---
    p_profile = sub.add_parser("profile", help="Profile a model on target hardware")
    p_profile.add_argument("model", nargs="?", type=Path, help="Path to .tflite model file")
    p_profile.add_argument("--config", type=Path, help="YAML config file (hpx.yml)")
    p_profile.add_argument(
        "--engine",
        type=str,
        choices=[e.value for e in EngineType],
        help="Inference engine",
    )
    p_profile.add_argument("--engine-config", type=Path, help="Engine-specific YAML config")
    p_profile.add_argument("--board", type=str, help="Target board (default: apollo510_evb)")
    p_profile.add_argument("--toolchain", type=str, help="Toolchain (default: arm-none-eabi-gcc)")
    p_profile.add_argument("--arena-size", type=int, help="Tensor arena size in bytes")
    p_profile.add_argument(
        "--pmu-presets",
        nargs="+",
        help="PMU preset names to capture (default: basic_cpu)",
    )
    p_profile.add_argument("--per-layer", action="store_true", default=None, help="Per-layer breakdown (default)")
    p_profile.add_argument("--no-per-layer", action="store_false", dest="per_layer")
    p_profile.add_argument("--iterations", type=int, help="Inference iterations (default: 100)")
    p_profile.add_argument("--power", action="store_true", help="Enable Joulescope power capture")
    p_profile.add_argument("--power-duration", type=int, help="Power capture seconds (default: 30)")
    p_profile.add_argument("--output-dir", type=Path, help="Results output directory")
    p_profile.add_argument("--output-format", choices=["csv", "json"], help="Output format")
    p_profile.add_argument(
        "--no-model-explorer",
        action="store_true",
        help="Skip Model Explorer overlay generation",
    )
    p_profile.add_argument("--work-dir", type=Path, help="Working directory for generated firmware")
    p_profile.add_argument("--keep-work-dir", action="store_true", help="Keep working directory")
    p_profile.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")

    # --- hpx doctor ---
    sub.add_parser("doctor", help="Check toolchain and dependencies")

    # --- hpx engines ---
    sub.add_parser("engines", help="List available inference engines")

    # --- hpx boards ---
    sub.add_parser("boards", help="List supported boards and SoC capabilities")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "profile":
        _cmd_profile(args)
    elif args.command == "doctor":
        _cmd_doctor()
    elif args.command == "engines":
        _cmd_engines()
    elif args.command == "boards":
        _cmd_boards()


def _cmd_profile(args: argparse.Namespace) -> None:
    """Run the profiling pipeline."""
    from .config import load_config
    from .errors import HpxError

    # Build CLI overrides dict from parsed args
    cli: dict = {}

    if args.model is not None:
        cli.setdefault("model", {})["path"] = str(args.model)
    if args.arena_size is not None:
        cli.setdefault("model", {})["arena_size"] = args.arena_size

    if args.engine is not None:
        cli.setdefault("engine", {})["type"] = args.engine
    if args.engine_config is not None:
        cli.setdefault("engine", {})["config_path"] = str(args.engine_config)

    if args.board is not None:
        cli.setdefault("target", {})["board"] = args.board
    if args.toolchain is not None:
        cli.setdefault("target", {})["toolchain"] = args.toolchain

    if args.pmu_presets is not None:
        cli.setdefault("profiling", {})["pmu_presets"] = args.pmu_presets
    if args.per_layer is not None:
        cli.setdefault("profiling", {})["per_layer"] = args.per_layer
    if args.iterations is not None:
        cli.setdefault("profiling", {})["iterations"] = args.iterations

    if args.power:
        cli.setdefault("power", {})["enabled"] = True
    if args.power_duration is not None:
        cli.setdefault("power", {})["duration_s"] = args.power_duration

    if args.output_dir is not None:
        cli.setdefault("output", {})["dir"] = str(args.output_dir)
    if args.output_format is not None:
        cli.setdefault("output", {})["format"] = args.output_format
    if args.no_model_explorer:
        cli.setdefault("output", {})["model_explorer"] = False

    if args.work_dir is not None:
        cli["work_dir"] = str(args.work_dir)
    if args.keep_work_dir:
        cli["keep_work_dir"] = True
    cli["verbose"] = args.verbose

    config = load_config(args.config, cli)

    from .profiler import run_profile

    try:
        run_profile(config)
    except HpxError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _cmd_doctor() -> None:
    """Check toolchain and dependencies."""
    from .doctor import run_doctor

    run_doctor()


def _cmd_engines() -> None:
    """List available inference engines."""
    for engine in EngineType:
        print(f"  {engine.value}")


def _cmd_boards() -> None:
    """List supported boards and their SoC capabilities."""
    from .platform import get_soc, list_boards

    boards = list_boards()
    print(f"{'Board':<24} {'SoC':<14} {'Core':<14} {'PMU':<6} {'MVE':<5} {'Channel'}")
    print("-" * 80)
    for board in boards:
        soc = get_soc(board.soc)
        pmu = "full" if soc.has_full_pmu else "dwt"
        mve = "yes" if soc.has_mve else "no"
        print(f"{board.name:<24} {soc.name:<14} {soc.core.value:<14} {pmu:<6} {mve:<5} {board.channel}")
