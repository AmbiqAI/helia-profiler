"""hpx CLI — Profile LiteRT models on Ambiq silicon."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ._version import __version__
from .engines import EngineType

if TYPE_CHECKING:
    from .model_analysis import ModelAnalysis


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="hpx",
        description="Profile LiteRT models on Ambiq silicon.",
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
    g_engine.add_argument(
        "--model-location",
        type=str,
        choices=["auto", "tcm", "sram", "mram", "psram"],
        help=(
            "Where to place model weights and arena. "
            "'auto' (default) picks fastest fit (TCM > SRAM > MRAM), "
            "arena prioritized when regions compete. "
            "'tcm'/'sram'/'mram' force both into that region. "
            "'psram' uploads weights at runtime via J-Link "
            "(requires PSRAM-equipped board)."
        ),
    )
    g_engine.add_argument(
        "--arena-location",
        "--runtime-arena-location",
        dest="runtime_arena_location",
        type=str,
        choices=["tcm", "sram", "psram"],
        help=(
            "Tensor arena placement. "
            "tflm/helia-rt: places the single TFLM arena. "
            "helia-aot: places the AOT scratch arenas (persistent and "
            "constant arenas remain where the AOT planner placed them). "
            "Takes precedence over --model-location for the arena. "
            "Alias: --runtime-arena-location."
        ),
    )
    g_engine.add_argument(
        "--weights-location",
        "--runtime-weights-location",
        dest="runtime_weights_location",
        type=str,
        choices=["tcm", "sram", "mram", "psram"],
        help=(
            "Model weights placement. "
            "tflm/helia-rt: places the model flatbuffer (psram requires "
            "J-Link upload via the RTT transport). "
            "helia-aot: not supported (use heliaAOT planner controls via "
            "engine.config.aot_args; XIP/copy-to-RAM controls land in a "
            "later phase). "
            "Takes precedence over --model-location for weights. "
            "Alias: --runtime-weights-location."
        ),
    )
    g_engine.add_argument(
        "--core-override",
        type=str,
        choices=["cm4", "cm55"],
        help=(
            "Force heliaRT to use a specific core library variant "
            "(e.g. cm4 to disable MVE kernels on an M55 board)."
        ),
    )

    # -- Target hardware --
    g_target = p_profile.add_argument_group("target hardware")
    g_target.add_argument("--board", type=str, help="Target board (default: apollo510_evb)")
    g_target.add_argument("--toolchain", type=str, help="Toolchain (default: arm-none-eabi-gcc)")
    g_target.add_argument(
        "--jlink-serial",
        type=str,
        help="J-Link probe serial number (default: auto-detect)",
    )
    g_target.add_argument(
        "--transport",
        type=str,
        choices=["rtt", "usb_cdc", "swo"],
        help="Data transport (default: rtt). RTT is recommended for lossless capture.",
    )
    g_target.add_argument(
        "--cpu-clock",
        type=str,
        metavar="SPEED",
        help=(
            "CPU clock speed for generated firmware (board-specific, e.g. "
            "'lp'/'hp'). Default: the board's lowest-power tier."
        ),
    )
    g_target.add_argument(
        "--frozen",
        action="store_true",
        help=(
            "Use the existing nsx.lock/modules state without re-running dependency "
            "resolution. Useful for reproducible offline reruns."
        ),
    )

    # -- Build / NSX overrides --
    g_build = p_profile.add_argument_group("build overrides")
    g_build.add_argument(
        "--nsx-channel",
        type=str,
        help="NSX channel for module resolution (default: stable).",
    )
    g_build.add_argument(
        "--nsx-module",
        action="append",
        metavar="NAME:KEY=VALUE",
        dest="nsx_module_overrides",
        help=(
            "Override an NSX module's source. Repeatable. "
            "Keys: path (local dir), ref (git ref/tag), version (pin). "
            "Examples: --nsx-module nsx-core:path=/my/nsx-core "
            "--nsx-module nsx-cmsis-core:ref=feat/new-cmsis "
            "--nsx-module nsx-gpio:version=2.0.0"
        ),
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
            "Format: GROUP:SELECT where GROUP is a supported group for the target SoC "
            "(for example cpu/mve/memory on Cortex-M55) and "
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
    g_pmu.add_argument("--warmup", type=int, help="Warmup iterations (default: 5)")

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
    g_power.add_argument("--power-duration", type=int, help="Power capture seconds (default: 30)")
    g_power.add_argument(
        "--sync-gpio",
        type=int,
        help=(
            "GPIO pin for external power sync (default: board default; "
            "29 on apollo510_evb / apollo510b_evb, 10 on most other built-in EVBs)"
        ),
    )
    g_power.add_argument(
        "--no-ensure-power",
        action="store_true",
        help=(
            "Skip the auto power-on step at start-up. Use when the board "
            "is on a bench supply or you want to manage the rail yourself."
        ),
    )
    g_power.add_argument(
        "--power-serial",
        "--js-serial",
        dest="power_serial",
        type=str,
        default=None,
        help=(
            "Power instrument serial number to disambiguate when multiple "
            "devices are connected (e.g. Joulescope serial '004204'). "
            "Alias: --js-serial."
        ),
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
    g_adv.add_argument("--work-dir", type=Path, help="Working directory for generated firmware")
    g_adv.add_argument("--keep-work-dir", action="store_true", help="Keep working directory")
    g_adv.add_argument(
        "--clean",
        action="store_true",
        help="Wipe cached build directory before building (forces full rebuild)",
    )

    # --- hpx doctor ---
    sub.add_parser("doctor", help="Check toolchain and dependencies")

    # --- hpx analyze ---
    p_analyze = sub.add_parser(
        "analyze",
        help="Analyze model compute/parameter breakdown (no hardware needed)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Analyze a .tflite model without hardware:\n"
            "  hpx analyze model.tflite\n"
            "  hpx analyze model.tflite --engine helia-aot --board apollo510_evb\n"
            "  hpx analyze model.tflite --format csv --output analysis.csv\n"
            "  hpx analyze model.tflite --engine helia-aot --compare\n"
        ),
    )
    p_analyze.add_argument("model", type=Path, help="Path to .tflite model file")
    p_analyze.add_argument(
        "--engine",
        type=str,
        choices=[e.value for e in EngineType],
        default=None,
        help=(
            "Analyze as this engine would execute it. "
            "Default (no flag) uses the raw tflite graph. "
            "'helia-aot' runs AOT compilation and analyzes the transformed graph. "
            "'helia-rt' / 'tflm' analyze the original tflite (same graph)."
        ),
    )
    p_analyze.add_argument(
        "--compare",
        action="store_true",
        help="Show side-by-side comparison of original vs engine-transformed graph",
    )
    p_analyze.add_argument(
        "--format",
        choices=["table", "csv", "json"],
        default="table",
        help="Output format (default: table)",
    )
    p_analyze.add_argument("--output", "-o", type=Path, help="Write output to file")
    p_analyze.add_argument(
        "--board",
        type=str,
        default="apollo510_evb",
        help="Target board for AOT compilation (default: apollo510_evb)",
    )

    # --- hpx engines ---
    sub.add_parser("engines", help="List available inference engines")

    # --- hpx boards ---
    sub.add_parser("boards", help="List supported boards and SoC capabilities")

    # --- hpx power-on ---
    p_power = sub.add_parser(
        "power-on",
        help="Enable Joulescope current passthrough (keeps board powered)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Opens the Joulescope and enables current passthrough so the\n"
            "target board stays powered.  Holds the connection open until\n"
            "Ctrl-C.  Useful when the Joulescope app is not running and the\n"
            "board would otherwise be unpowered.\n"
        ),
    )
    p_power.add_argument(
        "--driver",
        type=str,
        choices=["joulescope", "joulescope-js110", "joulescope-js220"],
        default="joulescope",
        help="Joulescope driver (default: auto-detect)",
    )

    # --- hpx validate ---
    p_validate = sub.add_parser(
        "validate",
        help="Run hardware-in-the-loop validation suite (MLPerf Tiny models)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Hardware validation — runs canonical MLPerf Tiny models end-to-end\n"
            "against a real EVB + J-Link (and optional Joulescope).\n\n"
            "Examples:\n"
            "  hpx validate                         # full matrix (4 models × 2 engines × 2 power)\n"
            "  hpx validate --list                  # preview what would run\n"
            "  hpx validate --models kws,ic         # subset by model\n"
            "  hpx validate --engines aot           # subset by engine\n"
            "  hpx validate --power off             # skip Joulescope\n"
            "  hpx validate --repeat 10 --models kws --engines rt --power off\n"
            "                                       # stress-run the same case 10 times\n"
            "  hpx validate -k kws-aot              # pytest keyword filter\n"
        ),
    )
    p_validate.add_argument(
        "--models",
        type=str,
        default="",
        help="Comma-separated model IDs (default: all). See `hpx validate --list`.",
    )
    p_validate.add_argument(
        "--engines",
        type=str,
        default="",
        help="Comma-separated engines: rt,aot,helia-rt,helia-aot (default: both).",
    )
    p_validate.add_argument(
        "--power",
        choices=("both", "on", "off"),
        default="both",
        help="Power matrix: both (default) | on (only Joulescope runs) | off.",
    )
    p_validate.add_argument(
        "--boards",
        type=str,
        default="apollo510_evb",
        help="Comma-separated board IDs (default: apollo510_evb).",
    )
    p_validate.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Repeat each selected case N times for stress testing (default: 1).",
    )
    p_validate.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results/validation"),
        help="Where to write per-case artifacts + summary report (default: ./results/validation).",
    )
    p_validate.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Per-case timeout in seconds (default: 900).",
    )
    p_validate.add_argument(
        "-k",
        dest="keyword",
        type=str,
        default="",
        help="Pytest keyword expression — filter cases by substring match (e.g. 'kws-aot').",
    )
    p_validate.add_argument(
        "--junit-xml",
        type=Path,
        help="Emit JUnit-XML report at this path (for CI consumption).",
    )
    p_validate.add_argument(
        "--list",
        action="store_true",
        help="List matching cases and exit without running.",
    )
    p_validate.add_argument("-v", "--verbose", action="count", default=0)

    # --- hpx cache ---
    p_cache = sub.add_parser(
        "cache",
        help="Manage hpx/nsx caches",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Manage local caches used by hpx and its nsx dependency:\n\n"
            "  hpx cache purge      Remove all cached data (module clones,\n"
            "                       resolved refs, generated workspaces).\n"
            "                       Forces fresh network\n"
            "                       fetches on next run.\n"
            "  hpx cache info       Show cache location and size.\n"
        ),
    )
    cache_sub = p_cache.add_subparsers(dest="cache_action")
    cache_sub.add_parser("purge", help="Remove all cached data, including workspaces")
    cache_sub.add_parser("info", help="Show cache location and disk usage")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "profile":
        _cmd_profile(args)
    elif args.command == "analyze":
        _cmd_analyze(args)
    elif args.command == "doctor":
        _cmd_doctor()
    elif args.command == "engines":
        _cmd_engines()
    elif args.command == "boards":
        _cmd_boards()
    elif args.command == "power-on":
        _cmd_power_on(args)
    elif args.command == "validate":
        _cmd_validate(args)
    elif args.command == "cache":
        _cmd_cache(args)


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
    if args.model_location is not None:
        cli.setdefault("model", {})["model_location"] = args.model_location
    if args.runtime_arena_location is not None:
        cli.setdefault("engine", {}).setdefault("config", {})["runtime_arena_location"] = (
            args.runtime_arena_location
        )
    if args.runtime_weights_location is not None:
        cli.setdefault("engine", {}).setdefault("config", {})["runtime_weights_location"] = (
            args.runtime_weights_location
        )
    if args.core_override is not None:
        cli.setdefault("engine", {}).setdefault("config", {})["core_override"] = args.core_override

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
    if args.transport is not None:
        cli.setdefault("target", {})["transport"] = args.transport
    clock_sel: dict[str, str] = {}
    if args.cpu_clock is not None:
        clock_sel["cpu"] = args.cpu_clock
    if clock_sel:
        cli.setdefault("target", {})["clock"] = clock_sel
    if args.frozen:
        cli["frozen"] = True

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
    if args.warmup is not None:
        cli.setdefault("profiling", {})["warmup"] = args.warmup

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
    if getattr(args, "no_ensure_power", False):
        cli.setdefault("target", {})["ensure_board_powered"] = False
    if getattr(args, "power_serial", None):
        cli.setdefault("power", {})["serial"] = args.power_serial

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
    if args.clean:
        cli["clean"] = True
    cli["verbose"] = args.verbose

    # -- Build / NSX overrides --
    if getattr(args, "nsx_channel", None):
        cli.setdefault("build", {})["channel"] = args.nsx_channel
    nsx_overrides_raw = getattr(args, "nsx_module_overrides", None)
    if nsx_overrides_raw:
        nsx_modules: dict[str, dict[str, str]] = {}
        for spec in nsx_overrides_raw:
            if ":" not in spec:
                print(
                    f"Error: --nsx-module format is NAME:KEY=VALUE "
                    f"(e.g. nsx-ambiq-bsp-r5:path=/my/bsp). Got: '{spec}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            name, kv = spec.split(":", 1)
            if "=" not in kv:
                print(
                    f"Error: --nsx-module value must be KEY=VALUE "
                    f"(e.g. path=/my/bsp, ref=feat/new-soc, version=2.0.0). "
                    f"Got: '{kv}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            key, val = kv.split("=", 1)
            if key not in ("path", "ref", "version"):
                print(
                    f"Error: --nsx-module key must be 'path', 'ref', or 'version'. Got: '{key}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            nsx_modules.setdefault(name, {})[key] = val
        cli.setdefault("build", {})["nsx_modules"] = nsx_modules

    config = load_config(args.config, cli)

    from .api import profile
    from .console import HpxConsole

    console = HpxConsole(config.verbose)

    try:
        profile(config)
    except KeyboardInterrupt:
        console.print_interrupted()
        sys.exit(130)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)


def _cmd_analyze(args: argparse.Namespace) -> None:
    """Analyze model compute/parameter breakdown without hardware."""
    from .model_analysis import (
        ModelAnalysis,
        analyze_air_model,
        analyze_model,
        is_aot_available,
        is_available,
    )
    from .console import HpxConsole

    console = HpxConsole(verbosity=1)  # always show output

    if not args.model.exists():
        print(f"Error: model file not found: {args.model}", file=sys.stderr)
        sys.exit(1)

    if not is_available():
        print(
            "Error: ai-edge-litert is not installed.\n"
            "  Install with: pip install 'helia-profiler[analysis]'",
            file=sys.stderr,
        )
        sys.exit(1)

    engine = args.engine  # None, "helia-aot", "helia-rt", "tflm"
    is_aot = engine == EngineType.HELIA_AOT.value

    # --- Original tflite analysis (always needed as baseline) ---
    original = analyze_model(str(args.model))
    if original is None:
        print("Error: failed to analyze model.", file=sys.stderr)
        sys.exit(1)

    # --- Engine-specific analysis ---
    engine_result: ModelAnalysis | None = None
    if is_aot:
        if not is_aot_available():
            print(
                "Error: helia-aot is not installed.\n"
                "  Install with: pip install 'helia-profiler[aot]'",
                file=sys.stderr,
            )
            sys.exit(1)
        engine_result = _run_aot_analysis(args.model, args.board)

    # Determine which analysis is "primary" (what the engine actually runs)
    # and whether to show comparison
    if engine_result is not None:
        primary = engine_result
        reference = original if args.compare else None
    else:
        primary = original
        reference = None

    # --- Output ---
    if args.format == "table":
        console.print_analysis(primary, args.model.name, reference)
    elif args.format in ("csv", "json"):
        _write_analysis_file(primary, args.format, args.output, reference)
    else:
        console.print_analysis(primary, args.model.name, reference)


def _run_aot_analysis(model_path: Path, board: str) -> "ModelAnalysis | None":
    """Run heliaAOT compilation and return analysis of the transformed graph."""
    import tempfile

    from .model_analysis import analyze_air_model

    try:
        from helia_aot.converter import AotConverter  # type: ignore[import-untyped]
        from helia_aot.cli.defines import ConvertArgs  # type: ignore[import-untyped]
        from helia_aot.defines import ModuleType  # type: ignore[import-untyped]
    except ImportError:
        print("Error: helia-aot import failed.", file=sys.stderr)
        return None

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        convert_args = ConvertArgs(
            model={"path": str(model_path)},
            module={"path": tmp, "type": ModuleType.nsx.value},
            platform={"name": board},
        )
        try:
            ctx = AotConverter(config=convert_args).convert()
        except Exception as exc:
            print(f"Warning: AOT compilation failed: {exc}", file=sys.stderr)
            return None

        return analyze_air_model(ctx.model)


def _write_analysis_file(
    analysis: "ModelAnalysis",
    fmt: str,
    output: Path | None,
    aot: "ModelAnalysis | None" = None,
) -> None:
    """Write analysis results to CSV or JSON."""
    import csv
    import json

    if fmt == "csv":
        rows = []
        for la in analysis.layers:
            row = {
                "id": la.id,
                "op": la.op,
                "macs": la.macs,
                "ops": la.ops,
                "input_shapes": str(la.input_shapes),
                "output_shapes": str(la.output_shapes),
            }
            row.update(la.params)
            rows.append(row)

        if output:
            dest = output
        else:
            dest = Path("model_analysis.csv")

        fieldnames = list(rows[0].keys()) if rows else []
        with open(dest, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        print(f"Wrote {dest}")

    elif fmt == "json":
        data: dict = {
            "original": {
                "total_macs": analysis.total_macs,
                "total_ops": analysis.total_ops,
                "num_parameters": analysis.num_parameters,
                "layers": [
                    {
                        "id": la.id,
                        "op": la.op,
                        "macs": la.macs,
                        "ops": la.ops,
                        "input_shapes": la.input_shapes,
                        "output_shapes": la.output_shapes,
                        "params": la.params,
                    }
                    for la in analysis.layers
                ],
            }
        }
        if aot is not None:
            data["aot_transformed"] = {
                "total_macs": aot.total_macs,
                "total_ops": aot.total_ops,
                "num_parameters": aot.num_parameters,
                "layers": [
                    {
                        "id": la.id,
                        "op": la.op,
                        "macs": la.macs,
                        "ops": la.ops,
                        "input_shapes": la.input_shapes,
                        "output_shapes": la.output_shapes,
                        "params": la.params,
                    }
                    for la in aot.layers
                ],
            }

        dest = output or Path("model_analysis.json")
        dest.write_text(json.dumps(data, indent=2, default=str))
        print(f"Wrote {dest}")


def _cmd_doctor() -> None:
    """Check toolchain and dependencies."""
    from .doctor import collect_checks
    from .console import HpxConsole

    checks, required_python, optional = collect_checks()
    console = HpxConsole()
    console.print_doctor(checks, required_python, optional)


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
        rows.append(
            (
                board.name,
                soc.name,
                soc.core.value,
                ", ".join(soc.profiling_backends),
                ", ".join(soc.profiling_domains),
                board.channel,
            )
        )

    console = HpxConsole()
    console.print_boards(rows)


def _cmd_power_on(args: argparse.Namespace) -> None:
    """Enable Joulescope current passthrough and hold open until Ctrl-C."""
    from .power import get_driver
    from .errors import PowerError

    driver_name = args.driver

    try:
        driver = get_driver(driver_name)
    except PowerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  Hint: {exc.hint}", file=sys.stderr)
        sys.exit(1)

    print(f"Enabling current passthrough via {driver.name}...")

    try:
        driver.enable_passthrough()
    except PowerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  Hint: {exc.hint}", file=sys.stderr)
        sys.exit(1)

    print("Board powered — press Ctrl-C to release.")
    try:
        import signal

        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            driver.disable_passthrough()
        except Exception:
            pass
        print("\nJoulescope released.")


# ---------------------------------------------------------------------------
# hpx validate — hardware-in-the-loop validation suite
# ---------------------------------------------------------------------------


_ENGINE_ALIASES = {
    "rt": "helia-rt",
    "aot": "helia-aot",
    "helia-rt": "helia-rt",
    "helia-aot": "helia-aot",
}


def _normalise_engines(raw: str) -> str:
    """Translate short engine aliases (rt, aot) to canonical names."""
    if not raw.strip():
        return ""
    out: list[str] = []
    for token in [t.strip() for t in raw.split(",") if t.strip()]:
        if token not in _ENGINE_ALIASES:
            print(
                f"Error: unknown engine '{token}'. Known: rt, aot, helia-rt, helia-aot.",
                file=sys.stderr,
            )
            sys.exit(2)
        out.append(_ENGINE_ALIASES[token])
    return ",".join(out)


def _cmd_validate(args: argparse.Namespace) -> None:
    """Drive the hardware validation suite via pytest."""
    from .validation import MODELS, BOARDS, build_matrix

    engines_csv = _normalise_engines(args.engines)

    # --list mode — preview the matrix, don't touch hardware.
    if args.list:
        try:
            cases = build_matrix(
                models=[m.strip() for m in args.models.split(",") if m.strip()] or None,
                engines=[e.strip() for e in engines_csv.split(",") if e.strip()] or None,
                power=args.power,
                boards=[b.strip() for b in args.boards.split(",") if b.strip()] or None,
                repeat=args.repeat,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

        print(f"Registered models: {', '.join(sorted(MODELS))}")
        print(f"Registered boards: {', '.join(sorted(BOARDS))}")
        print(f"\n{len(cases)} case(s) would run:\n")
        for c in cases:
            power = "power" if c.power else "     "
            print(f"  {c.case_id:<48}  {c.engine:<10}  {power}")
        return

    # Locate the validation test directory inside the installed package /
    # repo checkout.  We support both the editable/repo layout
    # (``helia-profiler/tests/validation``) and any future packaged layout.
    repo_root = _find_repo_root()
    tests_dir = repo_root / "tests" / "validation"
    if not tests_dir.exists():
        print(
            f"Error: validation tests not found at {tests_dir}.\n"
            "  `hpx validate` must be run from a heliaPROFILER checkout.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import pytest  # noqa: F401  (imported to fail fast with a clear msg)
    except ImportError:
        print(
            "Error: pytest is required for `hpx validate`. Install it with `pip install pytest`.",
            file=sys.stderr,
        )
        sys.exit(2)

    pytest_args: list[str] = [
        str(tests_dir),
        "-m",
        "hardware",
        "--mlperf-power",
        args.power,
        "--mlperf-output",
        str(args.output_dir.resolve()),
        "--mlperf-timeout",
        str(args.timeout),
    ]
    if args.models.strip():
        pytest_args += ["--mlperf-models", args.models.strip()]
    if engines_csv:
        pytest_args += ["--mlperf-engines", engines_csv]
    if args.boards.strip():
        pytest_args += ["--mlperf-boards", args.boards.strip()]
    pytest_args += ["--mlperf-repeat", str(args.repeat)]
    if args.keyword:
        pytest_args += ["-k", args.keyword]
    if args.junit_xml:
        pytest_args += [f"--junitxml={args.junit_xml.resolve()}"]
    if args.verbose:
        pytest_args.append("-" + "v" * args.verbose)
    else:
        pytest_args.append("-v")

    import pytest

    print(f"Running: pytest {' '.join(pytest_args)}\n")
    rc = pytest.main(pytest_args)

    report_md = args.output_dir.resolve() / "validation_report.md"
    report_json = args.output_dir.resolve() / "validation_report.json"
    if report_md.exists():
        print(f"\nMarkdown report: {report_md}")
    if report_json.exists():
        print(f"JSON report:     {report_json}")
    sys.exit(int(rc))


def _find_repo_root() -> Path:
    """Locate the helia-profiler checkout root.

    Walks up from this file until a directory containing ``pyproject.toml``
    is found.  Falls back to the current working directory.
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "pyproject.toml").is_file() and (parent / "tests").is_dir():
            return parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# hpx cache — manage nsx/hpx caches
# ---------------------------------------------------------------------------


def _cmd_cache(args: argparse.Namespace) -> None:
    action = getattr(args, "cache_action", None)
    if action == "purge":
        _cmd_cache_purge()
    elif action == "info":
        _cmd_cache_info()
    else:
        print("Usage: hpx cache {purge|info}", file=sys.stderr)
        sys.exit(1)


def _workspace_cache_root() -> Path:
    return Path.home() / ".cache" / "helia-profiler" / "workspaces"


def _cmd_cache_purge() -> None:
    """Purge hpx/nsx caches (module cache + resolve-ref cache + workspaces)."""
    from neuralspotx import _resolve_cache, module_cache

    # 1. Clear the module content-addressed cache
    n_modules = module_cache.clear()
    if n_modules:
        print(f"  Purged {n_modules} cached module(s).")
    else:
        print("  Module cache already empty.")

    # 2. Invalidate the resolve-ref TTL cache
    _resolve_cache.invalidate_all()
    print("  Purged resolve-ref cache.")

    # 3. Remove persistent per-board workspaces (generated apps + nsx.lock)
    workspaces_root = _workspace_cache_root()
    if workspaces_root.is_dir():
        n_workspaces = sum(1 for child in workspaces_root.iterdir() if child.is_dir())
        shutil.rmtree(workspaces_root, ignore_errors=True)
        print(f"  Purged {n_workspaces} cached workspace(s).")
    else:
        print("  Workspace cache already empty.")

    print("Done — next profile/build will recreate workspaces and refresh module state.")


def _cmd_cache_info() -> None:
    """Show cache location and approximate disk usage."""
    from neuralspotx import module_cache
    from neuralspotx._resolve_cache import _cache_path

    mod_root = module_cache.module_cache_root()
    resolve_path = _cache_path()
    workspaces_root = _workspace_cache_root()

    print(f"Module cache:      {mod_root}")
    if mod_root.is_dir():
        entries = module_cache.iter_entries()
        total_bytes = sum(f.stat().st_size for e in entries for f in e.rglob("*") if f.is_file())
        print(f"  Entries: {len(entries)}, Size: {total_bytes / 1024 / 1024:.1f} MB")
    else:
        print("  (empty)")

    print(f"Resolve-ref cache: {resolve_path}")
    if resolve_path.exists():
        size = resolve_path.stat().st_size
        print(f"  Size: {size / 1024:.1f} KB")
    else:
        print("  (empty)")

    print(f"Workspace cache:   {workspaces_root}")
    if workspaces_root.is_dir():
        entries = [entry for entry in workspaces_root.iterdir() if entry.is_dir()]
        total_bytes = sum(
            f.stat().st_size for entry in entries for f in entry.rglob("*") if f.is_file()
        )
        print(f"  Entries: {len(entries)}, Size: {total_bytes / 1024 / 1024:.1f} MB")
    else:
        print("  (empty)")
