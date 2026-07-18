"""Typer construction for the ``hpx`` CLI.

Each Typer command function is a thin adapter: it collects typed CLI
parameters, assembles a ``types.SimpleNamespace`` with exactly the attribute
names the existing ``_cmd_*`` implementation functions read (see
``profile_cmd.py``, ``analyze_cmd.py``, ``inspect_cmds.py``, ...), and calls
into that unchanged implementation. This keeps the command implementations,
and the tests that exercise them via ``SimpleNamespace``, stable across the
argparse -> Typer migration.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import click
import typer

from .._version import __version__
from ..config import AGGREGATION_METHODS, POWER_FIRMWARE_MODES, Transport
from ..engines import EngineType
from ..placement import Placement
from ..target.lifecycle import ResetStrategy

app = typer.Typer(
    name="hpx",
    help="Profile LiteRT models on Ambiq silicon.",
    # Click 8.2+'s built-in no_args_is_help raises a UsageError (exit code 2)
    # instead of the historical "print help, exit 0" behavior. Replicate the
    # old argparse `hpx` bare-invocation contract explicitly in the callback
    # below instead of relying on no_args_is_help.
    no_args_is_help=False,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hpx {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _hpx_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        help="show program's version number and exit",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """Profile LiteRT models on Ambiq silicon."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


# ---------------------------------------------------------------------------
# hpx profile
# ---------------------------------------------------------------------------


_ENGINE_CHOICE = click.Choice([engine.value for engine in EngineType])
_ARENA_LOCATION_CHOICE = click.Choice([p.value for p in Placement if p is not Placement.MRAM])
_WEIGHTS_LOCATION_CHOICE = click.Choice([p.value for p in Placement])
_CORE_OVERRIDE_CHOICE = click.Choice(["cm4", "cm55"])
_TRANSPORT_CHOICE = click.Choice([t.value for t in Transport])
_AGGREGATION_CHOICE = click.Choice(list(AGGREGATION_METHODS))
_POWER_DRIVER_CHOICE = click.Choice(["joulescope", "ondevice"])
_POWER_MODE_CHOICE = click.Choice(["external", "internal"])
_POWER_FIRMWARE_CHOICE = click.Choice(list(POWER_FIRMWARE_MODES))
_POWER_RESET_STRATEGY_CHOICE = click.Choice([strategy.value for strategy in ResetStrategy])
_OUTPUT_FORMAT_CHOICE = click.Choice(["csv", "json"])

G_ENGINE = "engine"
G_TARGET = "target hardware"
G_BUILD = "build overrides"
G_PMU = "PMU profiling"
G_POWER = "power measurement"
G_OUTPUT = "output"
G_ADVANCED = "advanced"


@app.command(
    "profile",
    help="Profile a model on target hardware",
    epilog=(
        "Quick start:\n\n"
        "  hpx profile my_model.tflite\n\n"
        "  hpx profile --config hpx.yml\n\n"
        "  hpx profile my_model.tflite --engine helia-rt --power -vv"
    ),
)
def profile_command(
    model: Optional[Path] = typer.Argument(None, help="Path to .tflite model file"),
    config: Optional[Path] = typer.Option(None, "--config", help="YAML config file (hpx.yml)"),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True, help="Increase verbosity"),
    # -- engine --
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        click_type=_ENGINE_CHOICE,
        help="Inference engine (default: helia-rt)",
        rich_help_panel=G_ENGINE,
    ),
    engine_config: Optional[Path] = typer.Option(
        None, "--engine-config", help="Engine-specific YAML config", rich_help_panel=G_ENGINE
    ),
    arena_size: Optional[int] = typer.Option(
        None, "--arena-size", help="Tensor arena size in bytes", rich_help_panel=G_ENGINE
    ),
    runtime_arena_location: Optional[str] = typer.Option(
        None,
        "--arena-location",
        click_type=_ARENA_LOCATION_CHOICE,
        help=(
            "Tensor arena placement. "
            "helia-rt: places the single runtime tensor arena. "
            "helia-aot: use engine.config.aot_args.memory.tensors instead. "
            "Omit to let the engine and memory planner choose."
        ),
        rich_help_panel=G_ENGINE,
    ),
    runtime_weights_location: Optional[str] = typer.Option(
        None,
        "--weights-location",
        click_type=_WEIGHTS_LOCATION_CHOICE,
        help=(
            "Model weights placement. "
            "helia-rt: places the model flatbuffer (psram requires "
            "J-Link upload via the RTT transport). "
            "helia-aot: use engine.config.aot_args.memory.tensors instead. "
            "Omit to let the engine and memory planner choose."
        ),
        rich_help_panel=G_ENGINE,
    ),
    core_override: Optional[str] = typer.Option(
        None,
        "--core-override",
        click_type=_CORE_OVERRIDE_CHOICE,
        help=(
            "Force heliaRT to use a specific core library variant "
            "(e.g. cm4 to disable MVE kernels on an M55 board)."
        ),
        rich_help_panel=G_ENGINE,
    ),
    # -- target hardware --
    board: Optional[str] = typer.Option(
        None, "--board", help="Target board (default: apollo510_evb)", rich_help_panel=G_TARGET
    ),
    toolchain: Optional[str] = typer.Option(
        None,
        "--toolchain",
        help="Toolchain (default: arm-none-eabi-gcc)",
        rich_help_panel=G_TARGET,
    ),
    jlink_serial: Optional[str] = typer.Option(
        None,
        "--jlink-serial",
        help="J-Link probe serial number (default: auto-detect)",
        rich_help_panel=G_TARGET,
    ),
    transport: Optional[str] = typer.Option(
        None,
        "--transport",
        click_type=_TRANSPORT_CHOICE,
        help="Data transport (default: rtt). RTT is recommended for lossless capture.",
        rich_help_panel=G_TARGET,
    ),
    usb_port: Optional[str] = typer.Option(
        None,
        "--usb-port",
        help=(
            "Explicit USB CDC device path for --transport usb_cdc "
            "(for example /dev/ttyACM1)."
        ),
        rich_help_panel=G_TARGET,
    ),
    rtt_buffer_size_up: Optional[int] = typer.Option(
        None,
        "--rtt-buffer-size-up",
        metavar="BYTES",
        help=(
            "SEGGER RTT up-buffer size for generated RTT firmware. "
            "If too small, non-blocking writes during timed inference may be dropped, "
            "while blocking CSV/HPX_END writes may stall long enough to hit host timeouts. "
            "If omitted, hpx uses a toolchain-aware default."
        ),
        rich_help_panel=G_TARGET,
    ),
    cpu_clock: Optional[str] = typer.Option(
        None,
        "--cpu-clock",
        metavar="SPEED",
        help=(
            "CPU clock speed for generated firmware (board-specific, e.g. "
            "'lp'/'hp'). Default: the board's lowest-power tier."
        ),
        rich_help_panel=G_TARGET,
    ),
    frozen: bool = typer.Option(
        False,
        "--frozen",
        help=(
            "Use the existing nsx.lock/modules state without re-running dependency "
            "resolution. Useful for reproducible offline reruns."
        ),
        rich_help_panel=G_TARGET,
    ),
    # -- build / NSX overrides --
    nsx_channel: Optional[str] = typer.Option(
        None,
        "--nsx-channel",
        help="NSX channel for module resolution (default: stable).",
        rich_help_panel=G_BUILD,
    ),
    nsx_module: Optional[list[str]] = typer.Option(
        None,
        "--nsx-module",
        metavar="NAME:KEY=VALUE",
        help=(
            "Override an NSX module's source. Repeatable. "
            "Keys: path (local dir), ref (git ref/tag), version (pin). "
            "Examples: --nsx-module nsx-core:path=/my/nsx-core "
            "--nsx-module nsx-cmsis-core:ref=feat/new-cmsis "
            "--nsx-module nsx-gpio:version=2.0.0"
        ),
        rich_help_panel=G_BUILD,
    ),
    compiler_launcher: Optional[str] = typer.Option(
        None,
        "--compiler-launcher",
        metavar="NAME",
        help=(
            "CMake compiler launcher to cache compiles (e.g. sccache, ccache). "
            "'auto' (default) uses sccache/ccache if installed; a name or path "
            "requires it to be found. Overrides build.compiler_launcher; the "
            "HPX_COMPILER_LAUNCHER env var overrides both."
        ),
        rich_help_panel=G_BUILD,
    ),
    no_compiler_launcher: bool = typer.Option(
        False,
        "--no-compiler-launcher",
        help="Disable the compiler launcher (equivalent to --compiler-launcher none).",
        rich_help_panel=G_BUILD,
    ),
    # -- PMU profiling --
    pmu_counters: Optional[list[str]] = typer.Option(
        None,
        "--pmu-counters",
        metavar="GROUP:SELECT",
        help=(
            "PMU counter selection per compute unit. Repeatable. "
            "Format: GROUP:SELECT where GROUP is a supported group for the target SoC "
            "(for example cpu/mve/memory on Cortex-M55) and "
            "SELECT is 'default', 'all', or comma-separated counter names. "
            "Examples: --pmu-counters cpu:default --pmu-counters mve:all, "
            "--pmu-counters mve:ARM_PMU_MVE_INST_RETIRED,ARM_PMU_MVE_STALL"
        ),
        rich_help_panel=G_PMU,
    ),
    per_layer: Optional[bool] = typer.Option(
        None,
        "--per-layer/--no-per-layer",
        help="Per-layer breakdown (default)",
        rich_help_panel=G_PMU,
    ),
    iterations: Optional[int] = typer.Option(
        None, "--iterations", help="Inference iterations (default: 100)", rich_help_panel=G_PMU
    ),
    warmup: Optional[int] = typer.Option(
        None, "--warmup", help="Warmup iterations (default: 5)", rich_help_panel=G_PMU
    ),
    aggregation: Optional[str] = typer.Option(
        None,
        "--aggregation",
        click_type=_AGGREGATION_CHOICE,
        help=(
            "How per-layer counters are aggregated across iterations "
            "(default: median). 'median' rejects corrupted iterations; "
            "'trimmed' drops extremes then means; 'mean' is the raw average."
        ),
        rich_help_panel=G_PMU,
    ),
    # -- power measurement --
    power: bool = typer.Option(
        False, "--power", help="Enable power capture", rich_help_panel=G_POWER
    ),
    power_driver: Optional[str] = typer.Option(
        None,
        "--power-driver",
        click_type=_POWER_DRIVER_CHOICE,
        help="Power driver (default: joulescope = auto-detect JS110/JS220/JS320)",
        rich_help_panel=G_POWER,
    ),
    power_mode: Optional[str] = typer.Option(
        None,
        "--power-mode",
        click_type=_POWER_MODE_CHOICE,
        help="Power mode (default: external)",
        rich_help_panel=G_POWER,
    ),
    power_duration: Optional[int] = typer.Option(
        None,
        "--power-duration",
        help="Power capture seconds (default: 30)",
        rich_help_panel=G_POWER,
    ),
    power_firmware: Optional[str] = typer.Option(
        None,
        "--power-firmware",
        click_type=_POWER_FIRMWARE_CHOICE,
        help=(
            "Which binary is on target during power capture (default: "
            "dedicated). 'dedicated' flashes the transport-free "
            "hpx_profiler_power image to avoid SWO/UART/RTT/USB current "
            "contamination (measured on AP510 EVBs); 'shared' "
            "reuses the already-flashed transport binary."
        ),
        rich_help_panel=G_POWER,
    ),
    power_reset_strategy: Optional[str] = typer.Option(
        None,
        "--power-reset-strategy",
        click_type=_POWER_RESET_STRATEGY_CHOICE,
        help=(
            "Reset strategy before power capture (default: auto). "
            "Use explicit values only for board bring-up or controlled experiments."
        ),
        rich_help_panel=G_POWER,
    ),
    sync_gpio: Optional[int] = typer.Option(
        None,
        "--sync-gpio",
        help=(
            "GPIO pin for external power sync (default: board default; "
            "29 on apollo510_evb / apollo510b_evb, 10 on most other built-in EVBs)"
        ),
        rich_help_panel=G_POWER,
    ),
    ensure_power: bool = typer.Option(
        False,
        "--ensure-power",
        help=(
            "Scan for a Joulescope at start-up and enable current passthrough "
            "so the board powers on before flashing. Off by default; only "
            "needed when the board's power genuinely comes from the "
            "Joulescope rail (--power already implies this)."
        ),
        rich_help_panel=G_POWER,
    ),
    no_ensure_power: bool = typer.Option(
        False,
        "--no-ensure-power",
        help=(
            "Explicitly skip the auto power-on step, overriding --ensure-power "
            "or a config file's ensure_board_powered: true."
        ),
        rich_help_panel=G_POWER,
    ),
    power_serial: Optional[str] = typer.Option(
        None,
        "--power-serial",
        "--js-serial",
        help=(
            "Power instrument serial number to disambiguate when multiple "
            "devices are connected (e.g. Joulescope serial '004204'). "
            "Alias: --js-serial."
        ),
        rich_help_panel=G_POWER,
    ),
    # -- output --
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Results output directory", rich_help_panel=G_OUTPUT
    ),
    output_format: Optional[str] = typer.Option(
        None,
        "--output-format",
        click_type=_OUTPUT_FORMAT_CHOICE,
        help="Output format",
        rich_help_panel=G_OUTPUT,
    ),
    no_model_explorer: bool = typer.Option(
        False,
        "--no-model-explorer",
        help="Skip Model Explorer overlay generation",
        rich_help_panel=G_OUTPUT,
    ),
    detailed: bool = typer.Option(
        False,
        "--detailed",
        help="Emit detailed per-preset/group CSVs and memory breakdown",
        rich_help_panel=G_OUTPUT,
    ),
    # -- advanced --
    work_dir: Optional[Path] = typer.Option(
        None,
        "--work-dir",
        help="Working directory for generated firmware",
        rich_help_panel=G_ADVANCED,
    ),
    clean: bool = typer.Option(
        False,
        "--clean",
        help="Wipe cached build directory before building (forces full rebuild)",
        rich_help_panel=G_ADVANCED,
    ),
) -> None:
    """Profile a model on target hardware."""
    from .profile_cmd import _cmd_profile

    args = SimpleNamespace(
        model=model,
        config=config,
        verbose=verbose,
        engine=engine,
        engine_config=engine_config,
        arena_size=arena_size,
        runtime_arena_location=runtime_arena_location,
        runtime_weights_location=runtime_weights_location,
        core_override=core_override,
        board=board,
        toolchain=toolchain,
        jlink_serial=jlink_serial,
        transport=transport,
        usb_port=usb_port,
        rtt_buffer_size_up=rtt_buffer_size_up,
        cpu_clock=cpu_clock,
        frozen=frozen,
        nsx_channel=nsx_channel,
        nsx_module_overrides=nsx_module,
        compiler_launcher=compiler_launcher,
        no_compiler_launcher=no_compiler_launcher,
        pmu_counters=pmu_counters,
        per_layer=per_layer,
        iterations=iterations,
        warmup=warmup,
        aggregation=aggregation,
        power=power,
        power_driver=power_driver,
        power_mode=power_mode,
        power_duration=power_duration,
        power_firmware=power_firmware,
        power_reset_strategy=power_reset_strategy,
        sync_gpio=sync_gpio,
        ensure_power=ensure_power,
        no_ensure_power=no_ensure_power,
        power_serial=power_serial,
        output_dir=output_dir,
        output_format=output_format,
        no_model_explorer=no_model_explorer,
        detailed=detailed,
        work_dir=work_dir,
        clean=clean,
    )
    _cmd_profile(args)


# ---------------------------------------------------------------------------
# hpx doctor / engines / boards
# ---------------------------------------------------------------------------


@app.command("doctor", help="Check toolchain and dependencies")
def doctor_command() -> None:
    """Check toolchain and dependencies."""
    from .inspect_cmds import _cmd_doctor

    _cmd_doctor()


@app.command("engines", help="List available inference engines")
def engines_command() -> None:
    """List available inference engines."""
    from .inspect_cmds import _cmd_engines

    _cmd_engines()


@app.command("boards", help="List supported boards and SoC capabilities")
def boards_command() -> None:
    """List supported boards and their SoC capabilities."""
    from .inspect_cmds import _cmd_boards

    _cmd_boards()


# ---------------------------------------------------------------------------
# hpx analyze
# ---------------------------------------------------------------------------


_ANALYZE_FORMAT_CHOICE = click.Choice(["table", "csv", "json"])


@app.command(
    "analyze",
    help="Analyze model compute/parameter breakdown (no hardware needed)",
    epilog=(
        "Analyze a .tflite model without hardware:\n\n"
        "  hpx analyze model.tflite\n\n"
        "  hpx analyze model.tflite --engine helia-aot --board apollo510_evb\n\n"
        "  hpx analyze model.tflite --format csv --output analysis.csv\n\n"
        "  hpx analyze model.tflite --engine helia-aot --compare"
    ),
)
def analyze_command(
    model: Path = typer.Argument(..., help="Path to .tflite model file"),
    engine: Optional[str] = typer.Option(
        None,
        "--engine",
        click_type=_ENGINE_CHOICE,
        help=(
            "Analyze as this engine would execute it. "
            "Default (no flag) uses the raw tflite graph. "
            "'helia-aot' runs AOT compilation and analyzes the transformed graph. "
            "'helia-rt' analyzes the original tflite graph."
        ),
    ),
    compare: bool = typer.Option(
        False, "--compare", help="Show side-by-side comparison of original vs engine-transformed graph"
    ),
    format: str = typer.Option(
        "table",
        "--format",
        click_type=_ANALYZE_FORMAT_CHOICE,
        help="Output format (default: table)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Write output to file"
    ),
    board: str = typer.Option(
        "apollo510_evb",
        "--board",
        help="Target board for AOT compilation (default: apollo510_evb)",
    ),
) -> None:
    """Analyze model compute/parameter breakdown without hardware."""
    from .analyze_cmd import _cmd_analyze

    args = SimpleNamespace(
        model=model,
        engine=engine,
        compare=compare,
        format=format,
        output=output,
        board=board,
    )
    _cmd_analyze(args)


# ---------------------------------------------------------------------------
# hpx probes {list, match}
# ---------------------------------------------------------------------------

probes_app = typer.Typer(
    help="Inspect connected J-Link probes without opening an interactive SEGGER commander session",
)


@probes_app.callback(invoke_without_command=True)
def _probes_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


@probes_app.command("list", help="List connected J-Link probes")
def probes_list_command(
    board: Optional[str] = typer.Option(
        None, "--board", help="Inspect each probe against this board's J-Link device string"
    ),
    inspect: bool = typer.Option(
        False, "--inspect", help="Inspect target cores. Requires --board."
    ),
    json_: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from .inspect_cmds import _cmd_probes_list

    _cmd_probes_list(SimpleNamespace(board=board, inspect=inspect, json=json_))


@probes_app.command(
    "match", help="Resolve the J-Link serial for a board using HPX's normal selection policy"
)
def probes_match_command(
    board: str = typer.Option(..., "--board", help="Target board ID"),
    jlink_serial: Optional[str] = typer.Option(
        None, "--jlink-serial", help="Optional requested serial to validate against the selected board"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from .inspect_cmds import _cmd_probes_match

    _cmd_probes_match(SimpleNamespace(board=board, jlink_serial=jlink_serial, json=json_))


app.add_typer(probes_app, name="probes")


# ---------------------------------------------------------------------------
# hpx ports {list}
# ---------------------------------------------------------------------------

ports_app = typer.Typer(help="List host serial ports relevant to HPX transports")


@ports_app.callback(invoke_without_command=True)
def _ports_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


@ports_app.command("list", help="List serial ports with J-Link/CDC hints")
def ports_list_command(
    show_all: bool = typer.Option(
        False, "--all", help="Show every host serial port, not just HPX-relevant USB/J-Link ports"
    ),
    json_: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> None:
    from .inspect_cmds import _cmd_ports_list

    _cmd_ports_list(SimpleNamespace(show_all=show_all, json=json_))


app.add_typer(ports_app, name="ports")


# ---------------------------------------------------------------------------
# hpx target {reset}
# ---------------------------------------------------------------------------

target_app = typer.Typer(help="Run explicit target-side utility operations")


@target_app.callback(invoke_without_command=True)
def _target_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


_TARGET_RESET_KIND_CHOICE = click.Choice(["debug", "swpoi"])


@target_app.command(
    "reset", help="Reset a target through HPX's non-interactive J-Link wrapper"
)
def target_reset_command(
    board: str = typer.Option(..., "--board", help="Target board ID"),
    jlink_serial: Optional[str] = typer.Option(
        None, "--jlink-serial", help="J-Link probe serial number"
    ),
    kind: str = typer.Option(
        "debug",
        "--kind",
        click_type=_TARGET_RESET_KIND_CHOICE,
        help="Reset kind: debug r/g reset (default) or SWPOI reset",
    ),
) -> None:
    from .inspect_cmds import _cmd_target_reset

    _cmd_target_reset(SimpleNamespace(board=board, jlink_serial=jlink_serial, kind=kind))


app.add_typer(target_app, name="target")


# ---------------------------------------------------------------------------
# hpx power-on
# ---------------------------------------------------------------------------

_POWER_ON_DRIVER_CHOICE = click.Choice(["joulescope"])


@app.command(
    "power-on",
    help="Enable Joulescope current passthrough (keeps board powered)",
    epilog=(
        "Opens the Joulescope and enables current passthrough so the\n\n"
        "target board stays powered.  Holds the connection open until\n\n"
        "Ctrl-C.  Useful when the Joulescope app is not running and the\n\n"
        "board would otherwise be unpowered."
    ),
)
def power_on_command(
    driver: str = typer.Option(
        "joulescope",
        "--driver",
        click_type=_POWER_ON_DRIVER_CHOICE,
        help="Joulescope driver (default: auto-detect)",
    ),
    power_serial: Optional[str] = typer.Option(
        None,
        "--power-serial",
        "--js-serial",
        help="Joulescope serial number to select when multiple are connected",
    ),
) -> None:
    """Enable Joulescope current passthrough and hold open until Ctrl-C."""
    from .power_cmd import _cmd_power_on

    _cmd_power_on(driver, power_serial=power_serial)


# ---------------------------------------------------------------------------
# hpx validate
# ---------------------------------------------------------------------------

_VALIDATE_POWER_CHOICE = click.Choice(["both", "on", "off"])
_VALIDATE_SUITE_CHOICE = click.Choice(["smoke", "models-rt", "models-aot", "complete"])


@app.command(
    "validate",
    help="Run hardware-in-the-loop validation suite (MLPerf Tiny models)",
    epilog=(
        "Hardware validation — runs canonical MLPerf Tiny models end-to-end\n\n"
        "against a real EVB + J-Link (and optional Joulescope).\n\n"
        "Examples:\n\n"
        "  hpx validate                         # Apollo510 reliability matrix, power off\n\n"
        "  hpx validate --list                  # preview what would run\n\n"
        "  hpx validate --models kws,ic         # subset by model\n\n"
        "  hpx validate --engines aot           # subset by engine\n\n"
        "  hpx validate --power off             # skip Joulescope (default)\n\n"
        "  hpx validate --boards apollo3p_evb --repeat 2 --power off\n\n"
        "                                       # require two passing iterations per case\n\n"
        "  hpx validate -k kws-aot              # pytest keyword filter\n\n"
        "  hpx validate --suite smoke           # quick preset: kws / helia-rt / gcc / rtt / auto\n\n"
        "  hpx validate --suite models-rt       # 16-case RT sweep: 2 boards x 4 models x 2 toolchains\n\n"
        "  hpx validate --suite models-aot      # 16-case AOT sweep: 2 boards x 4 models x 2 toolchains\n\n"
        "  hpx validate --suite complete        # 32-case RT + AOT sweep"
    ),
)
def validate_command(
    models: str = typer.Option(
        "", "--models", help="Comma-separated model IDs (default: all). See `hpx validate --list`."
    ),
    engines: str = typer.Option(
        "",
        "--engines",
        help="Comma-separated engines: rt,aot,helia-rt,helia-aot (default: both).",
    ),
    power: str = typer.Option(
        "off",
        "--power",
        click_type=_VALIDATE_POWER_CHOICE,
        help="Power matrix: off (default) | on (only Joulescope runs) | both.",
    ),
    boards: str = typer.Option(
        "", "--boards", help="Comma-separated board IDs (default: apollo510_evb)."
    ),
    toolchains: str = typer.Option(
        "",
        "--toolchains",
        help="Comma-separated toolchains: gcc,armclang/acfe,atfe (default: board defaults).",
    ),
    transports: str = typer.Option(
        "",
        "--interfaces",
        "--transports",
        help="Comma-separated interfaces/transports: rtt,uart,swo,usb_cdc (default: board defaults).",
    ),
    memories: str = typer.Option(
        "",
        "--memories",
        help="Comma-separated model placement presets: auto,tcm,sram,mram,psram (default: board defaults).",
    ),
    suite: Optional[str] = typer.Option(
        None,
        "--suite",
        click_type=_VALIDATE_SUITE_CHOICE,
        help=(
            "Preset suite. 'smoke' defaults unset axes to models=kws, engines=helia-rt, "
            "toolchains=arm-none-eabi-gcc, interfaces=rtt, memories=auto. "
            "'models-rt' and 'models-aot' default unset axes to all MLPerf Tiny models, "
            "Apollo510 + Apollo330mP, gcc + atfe, rtt, auto memory, and the selected engine. "
            "'complete' runs the same axes for both helia-rt and helia-aot. "
            "Explicit axis flags always win."
        ),
    ),
    jlink_serials: str = typer.Option(
        "", "--jlink-serials", help="Comma-separated board=serial entries for multi-board validation."
    ),
    repeat: int = typer.Option(
        1, "--repeat", help="Repeat each selected case N times for stress testing (default: 1)."
    ),
    output_dir: Path = typer.Option(
        Path("results/validation"),
        "--output-dir",
        help="Where to write per-case artifacts + summary report (default: ./results/validation).",
    ),
    timeout: float = typer.Option(
        900.0, "--timeout", help="Per-case timeout in seconds (default: 900)."
    ),
    keyword: str = typer.Option(
        "",
        "-k",
        help="Pytest keyword expression — filter cases by substring match (e.g. 'kws-aot').",
    ),
    junit_xml: Optional[Path] = typer.Option(
        None, "--junit-xml", help="Emit JUnit-XML report at this path (for CI consumption)."
    ),
    list_: bool = typer.Option(
        False, "--list", help="List matching cases and exit without running."
    ),
    verbose: int = typer.Option(0, "-v", "--verbose", count=True),
) -> None:
    """Drive the hardware validation suite via pytest."""
    from .validate_cmd import _cmd_validate

    args = SimpleNamespace(
        models=models,
        engines=engines,
        power=power,
        boards=boards,
        toolchains=toolchains,
        transports=transports,
        memories=memories,
        suite=suite,
        jlink_serials=jlink_serials,
        repeat=repeat,
        output_dir=output_dir,
        timeout=timeout,
        keyword=keyword,
        junit_xml=junit_xml,
        list=list_,
        verbose=verbose,
    )
    _cmd_validate(args)


# ---------------------------------------------------------------------------
# hpx compare
# ---------------------------------------------------------------------------


@app.command(
    "compare",
    help="Compare two hpx result directories",
    epilog=(
        "Examples:\n\n"
        "  hpx compare results/rt_gcc results/rt_atfe\n\n"
        "  hpx compare results/rt results/aot --output-dir results/rt_vs_aot\n\n"
        "  hpx compare results/baseline-validation results/candidate-validation "
        "--validation --output-dir results/validation-compare"
    ),
)
def compare_command(
    baseline: Path = typer.Argument(..., help="Baseline hpx result directory"),
    candidate: Path = typer.Argument(..., help="Candidate hpx result directory"),
    output_dir: Optional[Path] = typer.Option(
        None, "--output-dir", help="Write comparison artifacts to this directory"
    ),
    profile: Optional[Path] = typer.Option(
        None,
        "--profile",
        help="Versioned JSON comparison profile for regression verdicts",
    ),
    validation: bool = typer.Option(
        False, "--validation", help="Compare portable validation bundles instead of profile runs"
    ),
    top_layers: int = typer.Option(
        10, "--top-layers", help="Number of layer deltas to show in terminal output (default: 10)"
    ),
) -> None:
    """Compare two completed profile runs or validation bundles."""
    from .compare_cmd import _cmd_compare

    args = SimpleNamespace(
        baseline=baseline,
        candidate=candidate,
        output_dir=output_dir,
        profile=profile,
        top_layers=top_layers,
        validation=validation,
    )
    _cmd_compare(args)


# ---------------------------------------------------------------------------
# hpx cache {purge, info}
# ---------------------------------------------------------------------------

cache_app = typer.Typer(
    help="Manage hpx/nsx caches",
    epilog=(
        "Manage local caches used by hpx and its nsx dependency:\n\n"
        "  hpx cache purge      Remove all cached data (module clones,\n\n"
        "                       resolved refs, generated workspaces).\n\n"
        "                       Forces fresh network\n\n"
        "                       fetches on next run.\n\n"
        "  hpx cache info       Show cache location and size."
    ),
)


@cache_app.callback(invoke_without_command=True)
def _cache_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


@cache_app.command("purge", help="Remove all cached data, including workspaces")
def cache_purge_command() -> None:
    from .cache_cmd import _cmd_cache_purge

    _cmd_cache_purge()


@cache_app.command("info", help="Show cache location and disk usage")
def cache_info_command() -> None:
    from .cache_cmd import _cmd_cache_info

    _cmd_cache_info()


app.add_typer(cache_app, name="cache")


__all__ = ["app"]
