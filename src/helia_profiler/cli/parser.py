"""Argparse construction for the ``hpx`` CLI.

Each subcommand's parser is built by a dedicated ``_add_X_subparser``
function so no single function grows past a manageable size. ``build_parser``
assembles them all onto one ``argparse.ArgumentParser``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .._version import __version__
from ..engines import EngineType
from ..placement import ModelLocation, Placement


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level ``hpx`` argument parser."""
    from ..config import AGGREGATION_METHODS, Transport
    from ..target.lifecycle import ResetStrategy

    parser = argparse.ArgumentParser(
        prog="hpx",
        description="Profile LiteRT models on Ambiq silicon.",
    )
    parser.add_argument("--version", action="version", version=f"hpx {__version__}")
    sub = parser.add_subparsers(dest="command")

    _add_profile_subparser(
        sub, Transport=Transport, AGGREGATION_METHODS=AGGREGATION_METHODS, ResetStrategy=ResetStrategy
    )
    _add_doctor_subparser(sub)
    _add_analyze_subparser(sub)
    _add_engines_subparser(sub)
    _add_boards_subparser(sub)
    _add_probes_subparser(sub)
    _add_ports_subparser(sub)
    _add_target_subparser(sub)
    _add_power_on_subparser(sub)
    _add_validate_subparser(sub)
    _add_compare_subparser(sub)
    _add_cache_subparser(sub)

    return parser


def _add_profile_subparser(sub, *, Transport, AGGREGATION_METHODS, ResetStrategy):
    """Build the ``hpx profile`` subparser."""
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
    _add_profile_engine_args(p_profile)
    _add_profile_target_args(p_profile, Transport=Transport)
    _add_profile_build_args(p_profile)
    _add_profile_pmu_args(p_profile, AGGREGATION_METHODS=AGGREGATION_METHODS)
    _add_profile_power_args(p_profile, ResetStrategy=ResetStrategy)
    _add_profile_output_args(p_profile)
    _add_profile_advanced_args(p_profile)
    return p_profile


def _add_profile_engine_args(p_profile: argparse.ArgumentParser) -> None:
    """Build the ``hpx profile engine`` subparser."""
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


def _add_profile_target_args(p_profile: argparse.ArgumentParser, *, Transport) -> None:
    """Build the ``hpx profile target`` subparser."""
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


def _add_profile_build_args(p_profile: argparse.ArgumentParser) -> None:
    """Build the ``hpx profile build`` subparser."""
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


def _add_profile_pmu_args(p_profile: argparse.ArgumentParser, *, AGGREGATION_METHODS) -> None:
    """Build the ``hpx profile PMU`` subparser."""
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


def _add_profile_power_args(p_profile: argparse.ArgumentParser, *, ResetStrategy) -> None:
    """Build the ``hpx profile power`` subparser."""
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


def _add_profile_output_args(p_profile: argparse.ArgumentParser) -> None:
    """Build the ``hpx profile output`` subparser."""
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


def _add_profile_advanced_args(p_profile: argparse.ArgumentParser) -> None:
    """Build the ``hpx profile advanced`` subparser."""
    # -- Advanced --
    g_adv = p_profile.add_argument_group("advanced")
    g_adv.add_argument("--work-dir", type=Path, help="Working directory for generated firmware")
    g_adv.add_argument("--keep-work-dir", action="store_true", help="Keep working directory")
    g_adv.add_argument(
        "--clean",
        action="store_true",
        help="Wipe cached build directory before building (forces full rebuild)",
    )


def _add_doctor_subparser(sub) -> None:
    """Build the ``hpx doctor`` subparser."""
    sub.add_parser("doctor", help="Check toolchain and dependencies")


def _add_analyze_subparser(sub) -> None:
    """Build the ``hpx analyze`` subparser."""
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


def _add_engines_subparser(sub) -> None:
    """Build the ``hpx engines`` subparser."""
    sub.add_parser("engines", help="List available inference engines")


def _add_boards_subparser(sub) -> None:
    """Build the ``hpx boards`` subparser."""
    sub.add_parser("boards", help="List supported boards and SoC capabilities")


def _add_probes_subparser(sub) -> None:
    """Build the ``hpx probes`` subparser."""
    p_probes = sub.add_parser(
        "probes",
        help="Inspect connected J-Link probes without opening an interactive SEGGER commander session",
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


def _add_ports_subparser(sub) -> None:
    """Build the ``hpx ports`` subparser."""
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


def _add_target_subparser(sub) -> None:
    """Build the ``hpx target`` subparser."""
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


def _add_power_on_subparser(sub) -> None:
    """Build the ``hpx power-on`` subparser."""
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


def _add_validate_subparser(sub) -> None:
    """Build the ``hpx validate`` subparser."""
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
    _add_validate_axis_args(p_validate)
    _add_validate_run_control_args(p_validate)


def _add_validate_axis_args(p_validate: argparse.ArgumentParser) -> None:
    """Add the ``hpx validate`` case-selection axis flags (models/engines/...)."""
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


def _add_validate_run_control_args(p_validate: argparse.ArgumentParser) -> None:
    """Add the ``hpx validate`` run-control flags (repeat/timeout/output/...)."""
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


def _add_compare_subparser(sub) -> None:
    """Build the ``hpx compare`` subparser."""
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


def _add_cache_subparser(sub) -> None:
    """Build the ``hpx cache`` subparser."""
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
