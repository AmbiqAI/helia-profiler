"""hpx CLI — Profile LiteRT models on Ambiq silicon."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ._version import __version__
from .engines import EngineType
from .placement import ModelLocation, Placement

if TYPE_CHECKING:
    from .model_analysis import ModelAnalysis


def main(argv: list[str] | None = None) -> None:
    from .config import AGGREGATION_METHODS, Transport
    from .target_lifecycle import ResetStrategy

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
        choices=[EngineType.HELIA_RT.value, EngineType.HELIA_AOT.value],
        help="Inference engine (default: helia-rt)",
    )
    g_engine.add_argument("--engine-config", type=Path, help="Engine-specific YAML config")
    g_engine.add_argument("--arena-size", type=int, help="Tensor arena size in bytes")
    g_engine.add_argument(
        "--model-location",
        type=str,
        choices=[loc.value for loc in ModelLocation],
        help=(
            "Compatibility placement preset for both arena and weights. "
            "Prefer --arena-location and --weights-location for runtime engines. "
            "'auto' picks fastest fit; 'mram' keeps weights in MRAM and arena in fast RAM."
        ),
    )
    g_engine.add_argument(
        "--arena-location",
        "--runtime-arena-location",
        dest="runtime_arena_location",
        type=str,
        choices=[p.value for p in Placement if p is not Placement.MRAM],
        help=(
            "Tensor arena placement. "
            "helia-rt: places the single runtime tensor arena. "
            "helia-aot: use engine.config.aot_args.memory.tensors instead. "
            "Takes precedence over --model-location for the arena. "
            "Alias: --runtime-arena-location."
        ),
    )
    g_engine.add_argument(
        "--weights-location",
        "--runtime-weights-location",
        dest="runtime_weights_location",
        type=str,
        choices=[p.value for p in Placement],
        help=(
            "Model weights placement. "
            "helia-rt: places the model flatbuffer (psram requires "
            "J-Link upload via the RTT transport). "
            "helia-aot: use engine.config.aot_args.memory.tensors instead. "
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
        choices=[t.value for t in Transport],
        help="Data transport (default: rtt). RTT is recommended for lossless capture.",
    )
    g_target.add_argument(
        "--usb-port",
        type=str,
        help=(
            "Explicit USB CDC device path for --transport usb_cdc "
            "(for example /dev/ttyACM1)."
        ),
    )
    g_target.add_argument(
        "--rtt-buffer-size-up",
        type=int,
        metavar="BYTES",
        help=(
            "SEGGER RTT up-buffer size for generated RTT firmware. "
            "If too small, non-blocking writes during timed inference may be dropped, "
            "while blocking CSV/HPX_END writes may stall long enough to hit host timeouts. "
            "If omitted, hpx uses a toolchain-aware default."
        ),
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
    g_build.add_argument(
        "--compiler-launcher",
        type=str,
        metavar="NAME",
        dest="compiler_launcher",
        help=(
            "CMake compiler launcher to cache compiles (e.g. sccache, ccache). "
            "'auto' (default) uses sccache/ccache if installed; a name or path "
            "requires it to be found. Overrides build.compiler_launcher; the "
            "HPX_COMPILER_LAUNCHER env var overrides both."
        ),
    )
    g_build.add_argument(
        "--no-compiler-launcher",
        action="store_true",
        dest="no_compiler_launcher",
        help="Disable the compiler launcher (equivalent to --compiler-launcher none).",
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
    g_pmu.add_argument(
        "--aggregation",
        choices=list(AGGREGATION_METHODS),
        help=(
            "How per-layer counters are aggregated across iterations "
            "(default: median). 'median' rejects corrupted iterations; "
            "'trimmed' drops extremes then means; 'mean' is the raw average."
        ),
    )

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
        "--power-reset-strategy",
        choices=[strategy.value for strategy in ResetStrategy],
        help=(
            "Reset strategy before power capture (default: auto). "
            "Use explicit values only for board bring-up or controlled experiments."
        ),
    )
    g_power.add_argument(
        "--sync-gpio",
        type=int,
        help=(
            "GPIO pin for external power sync (default: board default; "
            "29 on apollo510_evb / apollo510b_evb, 10 on most other built-in EVBs)"
        ),
    )
    g_power.add_argument(
        "--ensure-power",
        action="store_true",
        help=(
            "Scan for a Joulescope at start-up and enable current passthrough "
            "so the board powers on before flashing. Off by default; only "
            "needed when the board's power genuinely comes from the "
            "Joulescope rail (--power already implies this)."
        ),
    )
    g_power.add_argument(
        "--no-ensure-power",
        action="store_true",
        help=(
            "Explicitly skip the auto power-on step, overriding --ensure-power "
            "or a config file's ensure_board_powered: true."
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
        choices=[EngineType.HELIA_RT.value, EngineType.HELIA_AOT.value],
        default=None,
        help=(
            "Analyze as this engine would execute it. "
            "Default (no flag) uses the raw tflite graph. "
            "'helia-aot' runs AOT compilation and analyzes the transformed graph. "
            "'helia-rt' analyzes the original tflite graph."
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

    # --- hpx probes ---
    p_probes = sub.add_parser(
        "probes",
        help="Inspect connected J-Link probes without opening an interactive JLinkExe session",
    )
    probes_sub = p_probes.add_subparsers(dest="probes_action")
    p_probes_list = probes_sub.add_parser("list", help="List connected J-Link probes")
    p_probes_list.add_argument(
        "--board",
        type=str,
        help="Inspect each probe against this board's J-Link device string",
    )
    p_probes_list.add_argument(
        "--inspect",
        action="store_true",
        help="Inspect target cores. Requires --board.",
    )
    p_probes_list.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_probes_match = probes_sub.add_parser(
        "match",
        help="Resolve the J-Link serial for a board using HPX's normal selection policy",
    )
    p_probes_match.add_argument("--board", required=True, type=str, help="Target board ID")
    p_probes_match.add_argument(
        "--jlink-serial",
        type=str,
        help="Optional requested serial to validate against the selected board",
    )
    p_probes_match.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    # --- hpx ports ---
    p_ports = sub.add_parser("ports", help="List host serial ports relevant to HPX transports")
    ports_sub = p_ports.add_subparsers(dest="ports_action")
    p_ports_list = ports_sub.add_parser("list", help="List serial ports with J-Link/CDC hints")
    p_ports_list.add_argument(
        "--all",
        dest="show_all",
        action="store_true",
        help="Show every host serial port, not just HPX-relevant USB/J-Link ports",
    )
    p_ports_list.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    # --- hpx target ---
    p_target = sub.add_parser("target", help="Run explicit target-side utility operations")
    target_sub = p_target.add_subparsers(dest="target_action")
    p_target_reset = target_sub.add_parser(
        "reset",
        help="Reset a target through HPX's non-interactive J-Link wrapper",
    )
    p_target_reset.add_argument("--board", required=True, type=str, help="Target board ID")
    p_target_reset.add_argument("--jlink-serial", type=str, help="J-Link probe serial number")
    p_target_reset.add_argument(
        "--kind",
        choices=("debug", "swpoi"),
        default="debug",
        help="Reset kind: debug r/g reset (default) or SWPOI reset",
    )

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
            "  hpx validate                         # Apollo510 reliability matrix, power off\n"
            "  hpx validate --list                  # preview what would run\n"
            "  hpx validate --models kws,ic         # subset by model\n"
            "  hpx validate --engines aot           # subset by engine\n"
            "  hpx validate --power off             # skip Joulescope (default)\n"
            "  hpx validate --boards apollo3p_evb --repeat 2 --power off\n"
            "                                       # require two passing iterations per case\n"
            "  hpx validate -k kws-aot              # pytest keyword filter\n"
            "  hpx validate --suite smoke           # quick preset: kws / helia-rt / gcc / rtt / auto\n"
            "  hpx validate --suite models-rt       # 12-case model sweep: 3 boards x 4 models x RT\n"
            "  hpx validate --suite models-aot      # 12-case model sweep: 3 boards x 4 models x AOT\n"
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
        default="off",
        help="Power matrix: off (default) | on (only Joulescope runs) | both.",
    )
    p_validate.add_argument(
        "--boards",
        type=str,
        default="",
        help="Comma-separated board IDs (default: apollo510_evb).",
    )
    p_validate.add_argument(
        "--toolchains",
        type=str,
        default="",
        help="Comma-separated toolchains: gcc,armclang/acfe,atfe (default: board defaults).",
    )
    p_validate.add_argument(
        "--interfaces",
        "--transports",
        dest="transports",
        type=str,
        default="",
        help="Comma-separated interfaces/transports: rtt,uart,swo,usb_cdc (default: board defaults).",
    )
    p_validate.add_argument(
        "--memories",
        type=str,
        default="",
        help="Comma-separated model placement presets: auto,tcm,sram,mram,psram (default: board defaults).",
    )
    p_validate.add_argument(
        "--suite",
        choices=["smoke", "models-rt", "models-aot"],
        default=None,
        help=(
            "Preset suite. 'smoke' defaults unset axes to models=kws, engines=helia-rt, "
            "toolchains=arm-none-eabi-gcc, interfaces=rtt, memories=auto. "
            "'models-rt'/'models-aot' default unset axes to all MLPerf Tiny models, "
            "all boards, gcc, rtt, auto memory, and the selected engine. "
            "Explicit axis flags always win."
        ),
    )
    p_validate.add_argument(
        "--jlink-serials",
        type=str,
        default="",
        help="Comma-separated board=serial entries for multi-board validation.",
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

    # --- hpx compare ---
    p_compare = sub.add_parser(
        "compare",
        help="Compare two hpx result directories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  hpx compare results/rt_gcc results/rt_atfe\n"
            "  hpx compare results/rt results/aot --output-dir results/rt_vs_aot\n"
        ),
    )
    p_compare.add_argument("baseline", type=Path, help="Baseline hpx result directory")
    p_compare.add_argument("candidate", type=Path, help="Candidate hpx result directory")
    p_compare.add_argument(
        "--output-dir",
        type=Path,
        help="Write compare_summary.json and layer_diff.csv to this directory",
    )
    p_compare.add_argument(
        "--top-layers",
        type=int,
        default=10,
        help="Number of layer deltas to show in terminal output (default: 10)",
    )

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
    elif args.command == "probes":
        _cmd_probes(args)
    elif args.command == "ports":
        _cmd_ports(args)
    elif args.command == "target":
        _cmd_target(args)
    elif args.command == "power-on":
        _cmd_power_on(args)
    elif args.command == "validate":
        _cmd_validate(args)
    elif args.command == "compare":
        _cmd_compare(args)
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
        cli.setdefault("model", {})["arena_location"] = args.runtime_arena_location
    if args.runtime_weights_location is not None:
        cli.setdefault("model", {})["weights_location"] = args.runtime_weights_location
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
    if getattr(args, "usb_port", None) is not None:
        cli.setdefault("target", {})["usb_port"] = args.usb_port
    if args.rtt_buffer_size_up is not None:
        cli.setdefault("target", {})["rtt_buffer_size_up"] = args.rtt_buffer_size_up
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
    if getattr(args, "aggregation", None) is not None:
        cli.setdefault("profiling", {})["aggregation"] = args.aggregation

    if args.power:
        cli.setdefault("power", {})["enabled"] = True
    if args.power_driver is not None:
        cli.setdefault("power", {})["driver"] = args.power_driver
    if args.power_mode is not None:
        cli.setdefault("power", {})["mode"] = args.power_mode
    if args.power_duration is not None:
        cli.setdefault("power", {})["duration_s"] = args.power_duration
    if getattr(args, "power_reset_strategy", None) is not None:
        cli.setdefault("power", {})["reset_strategy"] = args.power_reset_strategy
    if args.sync_gpio is not None:
        cli.setdefault("power", {})["sync_gpio_pin"] = args.sync_gpio
    if getattr(args, "ensure_power", False):
        cli.setdefault("target", {})["ensure_board_powered"] = True
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
    if getattr(args, "no_compiler_launcher", False):
        cli.setdefault("build", {})["compiler_launcher"] = "none"
    elif getattr(args, "compiler_launcher", None):
        cli.setdefault("build", {})["compiler_launcher"] = args.compiler_launcher
    nsx_overrides_raw = getattr(args, "nsx_module_overrides", None)
    if nsx_overrides_raw:
        nsx_modules: dict[str, dict[str, str]] = {}
        for spec in nsx_overrides_raw:
            if ":" not in spec:
                print(
                    f"Error: --nsx-module format is NAME:KEY=VALUE "
                    f"(e.g. nsx-ambiq-bsp:path=/my/bsp). Got: '{spec}'",
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

    engine = args.engine  # None, "helia-aot", or "helia-rt"
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
    console.print_engines([EngineType.HELIA_RT.value, EngineType.HELIA_AOT.value])


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


def _cmd_probes(args: argparse.Namespace) -> None:
    action = getattr(args, "probes_action", None)
    if action == "list":
        _cmd_probes_list(args)
    elif action == "match":
        _cmd_probes_match(args)
    else:
        print("Usage: hpx probes {list|match}", file=sys.stderr)
        sys.exit(1)


def _cmd_probes_list(args: argparse.Namespace) -> None:
    from .errors import HpxError
    from .jlink import inspect_probe_target, list_connected_probes

    board_name = getattr(args, "board", None)
    inspect = bool(getattr(args, "inspect", False) or board_name)
    if inspect and not board_name:
        print("Error: hpx probes list --inspect requires --board.", file=sys.stderr)
        sys.exit(2)

    board = soc = None
    if board_name:
        try:
            from .platform import get_board, get_soc_for_board

            board = get_board(board_name)
            soc = get_soc_for_board(board_name)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

    try:
        probes = list_connected_probes()
        rows: list[dict[str, str | bool | None]] = []
        for probe in probes:
            row: dict[str, str | bool | None] = {
                "serial": probe.serial,
                "product": probe.product,
                "connection": probe.connection,
            }
            if inspect and soc is not None:
                match = inspect_probe_target(probe, device=soc.jlink_device)
                row["detected_core"] = match.detected_core.value if match.detected_core else None
                row["matches_board"] = match.detected_core is soc.core
                row["board"] = board.name if board is not None else board_name
                row["jlink_device"] = soc.jlink_device
            rows.append(row)
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if args.json:
        print(json.dumps({"probes": rows}, indent=2))
        return
    if not rows:
        print("No J-Link probes detected.")
        return
    if inspect:
        _print_rows(rows, ("serial", "product", "connection", "detected_core", "matches_board"))
    else:
        _print_rows(rows, ("serial", "product", "connection"))


def _cmd_probes_match(args: argparse.Namespace) -> None:
    from .errors import HpxError
    from .jlink import resolve_probe_serial

    try:
        from .platform import get_soc_for_board

        soc = get_soc_for_board(args.board)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        serial = resolve_probe_serial(
            device=soc.jlink_device,
            expected_core=soc.core,
            requested_serial=args.jlink_serial,
        )
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if args.json:
        print(json.dumps({"board": args.board, "serial": serial}, indent=2))
    else:
        print(f"{args.board}: {serial}")


def _cmd_ports(args: argparse.Namespace) -> None:
    action = getattr(args, "ports_action", None)
    if action == "list":
        _cmd_ports_list(args)
    else:
        print("Usage: hpx ports {list}", file=sys.stderr)
        sys.exit(1)


def _cmd_ports_list(args: argparse.Namespace) -> None:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("Error: pyserial is required for hpx ports list.", file=sys.stderr)
        sys.exit(1)

    rows = [_describe_serial_port(info) for info in list_ports.comports()]
    if not args.show_all:
        rows = [row for row in rows if _is_relevant_serial_port(row)]
    if args.json:
        print(json.dumps({"ports": rows}, indent=2))
        return
    if not rows:
        print("No serial ports detected.")
        return
    _print_rows(rows, ("device", "kind", "serial_number", "description", "product"))


def _cmd_target(args: argparse.Namespace) -> None:
    action = getattr(args, "target_action", None)
    if action == "reset":
        _cmd_target_reset(args)
    else:
        print("Usage: hpx target {reset}", file=sys.stderr)
        sys.exit(1)


def _cmd_target_reset(args: argparse.Namespace) -> None:
    from .errors import HpxError
    from .jlink import reset_target, reset_target_poi

    try:
        from .platform import get_soc_for_board

        soc = get_soc_for_board(args.board)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        if args.kind == "swpoi":
            reset_target_poi(device=soc.jlink_device, jlink_serial=args.jlink_serial)
        else:
            reset_target(device=soc.jlink_device, jlink_serial=args.jlink_serial)
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    serial = args.jlink_serial or "auto"
    print(f"Reset {args.board} via {args.kind} reset (serial={serial}).")


def _print_hpx_error(exc: Exception) -> None:
    print(f"Error: {exc}", file=sys.stderr)


def _print_rows(rows: list[dict[str, object]], columns: tuple[str, ...]) -> None:
    widths = {
        col: max(len(col), *(len(_cell_text(row.get(col))) for row in rows))
        for col in columns
    }
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(_cell_text(row.get(col)).ljust(widths[col]) for col in columns))


def _cell_text(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _describe_serial_port(info: object) -> dict[str, str]:
    fields = {
        "device": str(getattr(info, "device", "") or ""),
        "description": str(getattr(info, "description", "") or ""),
        "manufacturer": str(getattr(info, "manufacturer", "") or ""),
        "product": str(getattr(info, "product", "") or ""),
        "serial_number": str(getattr(info, "serial_number", "") or ""),
        "interface": str(getattr(info, "interface", "") or ""),
        "hwid": str(getattr(info, "hwid", "") or ""),
    }
    text = " ".join(fields.values()).lower()
    is_jlink = "segger" in text or "j-link" in text or "jlink" in text
    is_hpx_cdc = fields["serial_number"].startswith("HPX-")
    if is_jlink:
        kind = "jlink-vcom"
    elif is_hpx_cdc:
        kind = "hpx-usb-cdc"
    else:
        kind = "serial"
    return {**fields, "kind": kind}


def _is_relevant_serial_port(row: dict[str, str]) -> bool:
    if row["kind"] in ("jlink-vcom", "hpx-usb-cdc"):
        return True
    device = row["device"]
    return any(token in device for token in ("ttyACM", "ttyUSB", "tty.usbmodem"))


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

_TOOLCHAIN_ALIASES = {
    "gcc": "arm-none-eabi-gcc",
    "arm-none-eabi-gcc": "arm-none-eabi-gcc",
    "armclang": "armclang",
    "acfe": "armclang",
    "atfe": "atfe",
}

_TRANSPORT_ALIASES = {
    "rtt": "rtt",
    "uart": "uart",
    "swo": "swo",
    "usb": "usb_cdc",
    "usb_cdc": "usb_cdc",
}

_MEMORY_ALIASES = {
    "auto": "auto",
    "tcm": "tcm",
    "sram": "sram",
    "mram": "mram",
    "psram": "psram",
}


def _parse_jlink_serials(raw: str) -> dict[str, str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, serial = item.partition("=")
        if not sep or not board.strip() or not serial.strip():
            print(
                f"Error: invalid --jlink-serials entry {item!r}; expected board=serial.",
                file=sys.stderr,
            )
            sys.exit(2)
        mapping[board.strip()] = serial.strip()
    return mapping


def _normalise_engines(raw: str) -> str:
    """Translate short engine aliases (rt, aot) to canonical names."""
    return _normalise_csv_aliases(
        raw,
        aliases=_ENGINE_ALIASES,
        label="engine",
        known="rt, aot, helia-rt, helia-aot",
    )


def _normalise_toolchains(raw: str) -> str:
    """Translate toolchain aliases (gcc, acfe) to config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TOOLCHAIN_ALIASES,
        label="toolchain",
        known="gcc, arm-none-eabi-gcc, armclang/acfe, atfe",
    )


def _normalise_transports(raw: str) -> str:
    """Translate interface aliases (usb) to transport config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TRANSPORT_ALIASES,
        label="interface",
        known="rtt, uart, swo, usb_cdc",
    )


def _normalise_memories(raw: str) -> str:
    """Translate memory aliases to model placement presets."""
    return _normalise_csv_aliases(
        raw,
        aliases=_MEMORY_ALIASES,
        label="memory",
        known="auto, tcm, sram, mram, psram",
    )


def _normalise_csv_aliases(
    raw: str,
    *,
    aliases: dict[str, str],
    label: str,
    known: str,
) -> str:
    if not raw.strip():
        return ""
    out: list[str] = []
    for token in [t.strip() for t in raw.split(",") if t.strip()]:
        if token not in aliases:
            print(
                f"Error: unknown {label} '{token}'. Known: {known}.",
                file=sys.stderr,
            )
            sys.exit(2)
        out.append(aliases[token])
    return ",".join(out)


def _cmd_validate(args: argparse.Namespace) -> None:
    """Drive the hardware validation suite via pytest."""
    from .validation import MODELS, BOARDS, build_matrix

    # Preset suites fill in defaults for any axis the user did not set.
    suite = getattr(args, "suite", None)
    if suite == "smoke":
        if not args.models.strip():
            args.models = "kws"
        if not args.engines.strip():
            args.engines = "helia-rt"
        if not args.toolchains.strip():
            args.toolchains = "arm-none-eabi-gcc"
        if not args.transports.strip():
            args.transports = "rtt"
        if not args.memories.strip():
            args.memories = "auto"
    elif suite in {"models-rt", "models-aot"}:
        if not args.models.strip():
            args.models = "kws,vww,ic,ad"
        if not args.engines.strip():
            args.engines = "helia-rt" if suite == "models-rt" else "helia-aot"
        if not args.boards.strip():
            args.boards = "apollo3p_evb,apollo4p_blue_kxr_evb,apollo510_evb"
        if not args.toolchains.strip():
            args.toolchains = "arm-none-eabi-gcc"
        if not args.transports.strip():
            args.transports = "rtt"
        if not args.memories.strip():
            args.memories = "auto"

    if not args.boards.strip():
        args.boards = "apollo510_evb"

    engines_csv = _normalise_engines(args.engines)
    toolchains_csv = _normalise_toolchains(args.toolchains)
    transports_csv = _normalise_transports(args.transports)
    memories_csv = _normalise_memories(args.memories)
    jlink_serials = _parse_jlink_serials(args.jlink_serials)

    # --list mode — preview the matrix, don't touch hardware.
    if args.list:
        try:
            cases = build_matrix(
                models=[m.strip() for m in args.models.split(",") if m.strip()] or None,
                engines=[e.strip() for e in engines_csv.split(",") if e.strip()] or None,
                power=args.power,
                boards=[b.strip() for b in args.boards.split(",") if b.strip()] or None,
                toolchains=[t.strip() for t in toolchains_csv.split(",") if t.strip()] or None,
                transports=[t.strip() for t in transports_csv.split(",") if t.strip()] or None,
                memories=[m.strip() for m in memories_csv.split(",") if m.strip()] or None,
                jlink_serials=jlink_serials,
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
            print(
                f"  {c.case_id:<82}  {c.engine:<10}  "
                f"{c.toolchain.value:<18}  {c.transport.value:<7}  {c.memory.value:<5}  {power}"
            )
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
    if toolchains_csv:
        pytest_args += ["--mlperf-toolchains", toolchains_csv]
    if transports_csv:
        pytest_args += ["--mlperf-transports", transports_csv]
    if memories_csv:
        pytest_args += ["--mlperf-memories", memories_csv]
    if args.jlink_serials.strip():
        pytest_args += ["--mlperf-jlink-serials", args.jlink_serials.strip()]
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


def _cmd_compare(args: argparse.Namespace) -> None:
    """Compare two completed hpx profile output directories."""
    from .compare import compare_runs, write_compare_artifacts
    from .console import HpxConsole
    from .errors import HpxError

    console = HpxConsole()
    try:
        result = compare_runs(args.baseline, args.candidate)
        paths = None
        if args.output_dir is not None:
            paths = write_compare_artifacts(result, args.output_dir)
        console.print_compare(result, top_layers=args.top_layers, output_paths=paths)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)


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
