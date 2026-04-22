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
    p_profile = sub.add_parser(
        "profile",
        help="Profile a model on target hardware",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Quick start:\n"
            "  hpx profile my_model.tflite\n"
            "  hpx profile --config hpx.yml\n"
            "  hpx profile my_model.tflite --engine helia-rt --power -vv\n"
        ),
    )

    # -- Model (most common — no group needed, top-level positional) --
    p_profile.add_argument("model", nargs="?", type=Path, help="Path to .tflite model file")
    p_profile.add_argument("--config", type=Path, help="YAML config file (hpx.yml)")
    p_profile.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")

    # -- Engine --
    g_engine = p_profile.add_argument_group("engine")
    g_engine.add_argument(
        "--engine",
        type=str,
        choices=[e.value for e in EngineType],
        help="Inference engine (default: tflm)",
    )
    g_engine.add_argument("--engine-config", type=Path, help="Engine-specific YAML config")
    g_engine.add_argument("--arena-size", type=int, help="Tensor arena size in bytes")

    # -- Target hardware --
    g_target = p_profile.add_argument_group("target hardware")
    g_target.add_argument("--board", type=str, help="Target board (default: apollo510_evb)")
    g_target.add_argument(
        "--toolchain", type=str, help="Toolchain (default: arm-none-eabi-gcc)"
    )
    g_target.add_argument(
        "--jlink-serial", type=str,
        help="J-Link probe serial number (default: auto-detect)",
    )

    # -- PMU profiling --
    g_pmu = p_profile.add_argument_group("PMU profiling")
    g_pmu.add_argument(
        "--pmu-presets",
        nargs="+",
        help="Legacy PMU preset names (default: basic_cpu). Prefer --pmu-counters.",
    )
    g_pmu.add_argument(
        "--pmu-counters",
        nargs="+",
        metavar="GROUP:SELECT",
        help=(
            "PMU counter selection per compute unit. "
            "Format: GROUP:SELECT where GROUP is cpu/mve/memory and "
            "SELECT is 'default', 'all', or comma-separated counter names. "
            "Examples: --pmu-counters cpu:default mve:all, "
            "--pmu-counters mve:ARM_PMU_MVE_INST_RETIRED,ARM_PMU_MVE_STALL"
        ),
    )
    g_pmu.add_argument(
        "--per-layer", action="store_true", default=None, help="Per-layer breakdown (default)"
    )
    g_pmu.add_argument("--no-per-layer", action="store_false", dest="per_layer")
    g_pmu.add_argument("--iterations", type=int, help="Inference iterations (default: 100)")

    # -- Power measurement --
    g_power = p_profile.add_argument_group("power measurement")
    g_power.add_argument("--power", action="store_true", help="Enable power capture")
    g_power.add_argument(
        "--power-driver",
        type=str,
        choices=["joulescope", "joulescope-js110", "joulescope-js220", "ondevice"],
        help="Power driver (default: joulescope = auto-detect JS110/JS220)",
    )
    g_power.add_argument(
        "--power-mode",
        type=str,
        choices=["external", "internal"],
        help="Power mode (default: external)",
    )
    g_power.add_argument(
        "--power-duration", type=int, help="Power capture seconds (default: 30)"
    )
    g_power.add_argument(
        "--sync-gpio", type=int, help="GPIO pin for external power sync (default: 10)"
    )

    # -- Output --
    g_output = p_profile.add_argument_group("output")
    g_output.add_argument("--output-dir", type=Path, help="Results output directory")
    g_output.add_argument("--output-format", choices=["csv", "json"], help="Output format")
    g_output.add_argument(
        "--no-model-explorer",
        action="store_true",
        help="Skip Model Explorer overlay generation",
    )
    g_output.add_argument(
        "--detailed",
        action="store_true",
        help="Emit detailed per-preset/group CSVs and memory breakdown",
    )

    # -- Advanced --
    g_adv = p_profile.add_argument_group("advanced")
    g_adv.add_argument(
        "--work-dir", type=Path, help="Working directory for generated firmware"
    )
    g_adv.add_argument("--keep-work-dir", action="store_true", help="Keep working directory")

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
    if args.jlink_serial is not None:
        cli.setdefault("target", {})["jlink_serial"] = args.jlink_serial

    if args.pmu_presets is not None:
        cli.setdefault("profiling", {})["pmu_presets"] = args.pmu_presets
    if args.pmu_counters is not None:
        # Parse GROUP:SELECT pairs into a dict
        pmu_counters: dict[str, str | list[str]] = {}
        for spec in args.pmu_counters:
            if ":" not in spec:
                print(
                    f"Error: --pmu-counters format is GROUP:SELECT "
                    f"(e.g. cpu:default, mve:all). Got: '{spec}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            group, sel = spec.split(":", 1)
            if sel in ("default", "all"):
                pmu_counters[group] = sel
            else:
                pmu_counters[group] = sel.split(",")
        cli.setdefault("profiling", {})["pmu_counters"] = pmu_counters
    if args.per_layer is not None:
        cli.setdefault("profiling", {})["per_layer"] = args.per_layer
    if args.iterations is not None:
        cli.setdefault("profiling", {})["iterations"] = args.iterations

    if args.power:
        cli.setdefault("power", {})["enabled"] = True
    if args.power_driver is not None:
        cli.setdefault("power", {})["driver"] = args.power_driver
    if args.power_mode is not None:
        cli.setdefault("power", {})["mode"] = args.power_mode
    if args.power_duration is not None:
        cli.setdefault("power", {})["duration_s"] = args.power_duration
    if args.sync_gpio is not None:
        cli.setdefault("power", {})["sync_gpio_pin"] = args.sync_gpio

    if args.output_dir is not None:
        cli.setdefault("output", {})["dir"] = str(args.output_dir)
    if args.output_format is not None:
        cli.setdefault("output", {})["format"] = args.output_format
    if args.no_model_explorer:
        cli.setdefault("output", {})["model_explorer"] = False
    if args.detailed:
        cli.setdefault("output", {})["detailed"] = True

    if args.work_dir is not None:
        cli["work_dir"] = str(args.work_dir)
    if args.keep_work_dir:
        cli["keep_work_dir"] = True
    cli["verbose"] = args.verbose

    config = load_config(args.config, cli)

    from .api import profile
    from .console import HpxConsole

    console = HpxConsole(config.verbose)

    try:
        profile(config)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)


def _cmd_doctor() -> None:
    """Check toolchain and dependencies."""
    from .doctor import collect_checks
    from .console import HpxConsole

    checks, optional = collect_checks()
    console = HpxConsole()
    console.print_doctor(checks, optional)


def _cmd_engines() -> None:
    """List available inference engines."""
    from .console import HpxConsole

    console = HpxConsole()
    console.print_engines([e.value for e in EngineType])


def _cmd_boards() -> None:
    """List supported boards and their SoC capabilities."""
    from .platform import get_soc, list_boards
    from .console import HpxConsole

    boards = list_boards()
    rows: list[tuple[str, str, str, str, str, str]] = []
    for board in boards:
        soc = get_soc(board.soc)
        pmu = "full" if soc.has_full_pmu else "dwt"
        mve = "yes" if soc.has_mve else "no"
        rows.append((board.name, soc.name, soc.core.value, pmu, mve, board.channel))

    console = HpxConsole()
    console.print_boards(rows)
