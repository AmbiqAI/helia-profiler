"""Typer construction for the ``hpx`` CLI.

Each Typer command function is a thin adapter: it collects typed CLI
parameters and passes them as keyword arguments to the ``_cmd_*``
implementation functions (see ``profile_cmd.py``, ``analyze_cmd.py``,
``inspect_cmds.py``, ...). The commands own argument parsing; the
implementations own behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import click
import typer

# typer 0.26+ vendors click, so ``click_type`` values must subclass the
# vendored ParamType: an external ``click.Choice`` falls through
# ``convert_type`` to FuncParamType (metavar "FUNCTION", no choice list in
# --help). TyperChoice is the class typer itself builds for enum parameters.
from typer._types import TyperChoice

from .._version import __version__
from ..config import Aggregation, PowerFirmware, Transport
from ..engines import EngineType
from ..placement import Placement
from ..target.lifecycle import ResetStrategy
from . import inspect_app as _inspect_app
from . import validation_app as _validation_app

app = typer.Typer(
    name="hpx",
    help="Profile LiteRT and ExecuTorch models on Ambiq silicon.",
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
    version: Annotated[
        Optional[bool],
        typer.Option(
            "--version",
            help="show program's version number and exit",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Profile LiteRT and ExecuTorch models on Ambiq silicon."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


# ---------------------------------------------------------------------------
# hpx profile
# ---------------------------------------------------------------------------


_ENGINE_CHOICE = TyperChoice([engine.value for engine in EngineType])
_ARENA_LOCATION_CHOICE = TyperChoice([p.value for p in Placement if p is not Placement.MRAM])
_WEIGHTS_LOCATION_CHOICE = TyperChoice([p.value for p in Placement])
_CORE_OVERRIDE_CHOICE = TyperChoice(["cm4", "cm55"])
_TRANSPORT_CHOICE = TyperChoice([t.value for t in Transport])
_AGGREGATION_CHOICE = TyperChoice([a.value for a in Aggregation])
_POWER_DRIVER_CHOICE = TyperChoice(["joulescope", "ondevice"])
_POWER_MODE_CHOICE = TyperChoice(["external", "internal"])
_POWER_FIRMWARE_CHOICE = TyperChoice([f.value for f in PowerFirmware])
_POWER_RESET_STRATEGY_CHOICE = TyperChoice([strategy.value for strategy in ResetStrategy])
_OUTPUT_FORMAT_CHOICE = TyperChoice(["csv", "json"])

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
    model: Annotated[
        Optional[Path], typer.Argument(help="Path to .tflite or .pte model file")
    ] = None,
    config: Annotated[
        Optional[Path], typer.Option("--config", help="YAML config file (hpx.yml)")
    ] = None,
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity")
    ] = 0,
    # -- engine --
    engine: Annotated[
        Optional[str],
        typer.Option(
            "--engine",
            click_type=_ENGINE_CHOICE,
            help="Inference engine (default: helia-rt)",
            rich_help_panel=G_ENGINE,
        ),
    ] = None,
    engine_config: Annotated[
        Optional[Path],
        typer.Option(
            "--engine-config", help="Engine-specific YAML config", rich_help_panel=G_ENGINE
        ),
    ] = None,
    arena_size: Annotated[
        Optional[int],
        typer.Option("--arena-size", help="Tensor arena size in bytes", rich_help_panel=G_ENGINE),
    ] = None,
    runtime_arena_location: Annotated[
        Optional[str],
        typer.Option(
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
    ] = None,
    runtime_weights_location: Annotated[
        Optional[str],
        typer.Option(
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
    ] = None,
    core_override: Annotated[
        Optional[str],
        typer.Option(
            "--core-override",
            click_type=_CORE_OVERRIDE_CHOICE,
            help=(
                "Force heliaRT to use a specific core library variant "
                "(e.g. cm4 to disable MVE kernels on an M55 board)."
            ),
            rich_help_panel=G_ENGINE,
        ),
    ] = None,
    # -- target hardware --
    board: Annotated[
        Optional[str],
        typer.Option(
            "--board", help="Target board (default: apollo510_evb)", rich_help_panel=G_TARGET
        ),
    ] = None,
    toolchain: Annotated[
        Optional[str],
        typer.Option(
            "--toolchain",
            help="Toolchain (default: arm-none-eabi-gcc)",
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    jlink_serial: Annotated[
        Optional[str],
        typer.Option(
            "--jlink-serial",
            help="J-Link probe serial number (default: auto-detect)",
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    transport: Annotated[
        Optional[str],
        typer.Option(
            "--transport",
            click_type=_TRANSPORT_CHOICE,
            help="Data transport (default: rtt). RTT is recommended for lossless capture.",
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    usb_port: Annotated[
        Optional[str],
        typer.Option(
            "--usb-port",
            help=(
                "Explicit USB CDC device path for --transport usb_cdc (for example /dev/ttyACM1)."
            ),
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    rtt_buffer_size_up: Annotated[
        Optional[int],
        typer.Option(
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
    ] = None,
    cpu_clock: Annotated[
        Optional[str],
        typer.Option(
            "--cpu-clock",
            metavar="SPEED",
            help=(
                "CPU clock speed for generated firmware (board-specific, e.g. "
                "'lp'/'hp'). Default: the board's lowest-power tier."
            ),
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    frozen: Annotated[
        bool,
        typer.Option(
            "--frozen",
            help=(
                "Deprecated alias for --offline: require the compatible lock and "
                "materialized module state without dependency resolution."
            ),
            rich_help_panel=G_TARGET,
        ),
    ] = False,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline",
            help="Require exact compatible lock/module reuse without network resolution.",
            rich_help_panel=G_BUILD,
        ),
    ] = False,
    update_dependencies: Annotated[
        bool,
        typer.Option(
            "--update-dependencies",
            help="Explicitly re-resolve dependency refs and rewrite nsx.lock.",
            rich_help_panel=G_BUILD,
        ),
    ] = False,
    # -- build / NSX overrides --
    nsx_channel: Annotated[
        Optional[str],
        typer.Option(
            "--nsx-channel",
            help="NSX channel for module resolution (default: stable).",
            rich_help_panel=G_BUILD,
        ),
    ] = None,
    nsx_module: Annotated[
        Optional[list[str]],
        typer.Option(
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
    ] = None,
    compiler_launcher: Annotated[
        Optional[str],
        typer.Option(
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
    ] = None,
    no_compiler_launcher: Annotated[
        bool,
        typer.Option(
            "--no-compiler-launcher",
            help="Disable the compiler launcher (equivalent to --compiler-launcher none).",
            rich_help_panel=G_BUILD,
        ),
    ] = False,
    # -- PMU profiling --
    pmu_counters: Annotated[
        Optional[list[str]],
        typer.Option(
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
    ] = None,
    per_layer: Annotated[
        Optional[bool],
        typer.Option(
            "--per-layer/--no-per-layer",
            help="Per-layer breakdown (default)",
            rich_help_panel=G_PMU,
        ),
    ] = None,
    iterations: Annotated[
        Optional[int],
        typer.Option(
            "--iterations", help="Inference iterations (default: 100)", rich_help_panel=G_PMU
        ),
    ] = None,
    warmup: Annotated[
        Optional[int],
        typer.Option("--warmup", help="Warmup iterations (default: 5)", rich_help_panel=G_PMU),
    ] = None,
    aggregation: Annotated[
        Optional[str],
        typer.Option(
            "--aggregation",
            click_type=_AGGREGATION_CHOICE,
            help=(
                "How per-layer counters are aggregated across iterations "
                "(default: median). 'median' rejects corrupted iterations; "
                "'trimmed' drops extremes then means; 'mean' is the raw average."
            ),
            rich_help_panel=G_PMU,
        ),
    ] = None,
    # -- power measurement --
    power: Annotated[
        bool, typer.Option("--power", help="Enable power capture", rich_help_panel=G_POWER)
    ] = False,
    power_driver: Annotated[
        Optional[str],
        typer.Option(
            "--power-driver",
            click_type=_POWER_DRIVER_CHOICE,
            help="Power driver (default: joulescope = auto-detect JS110/JS220/JS320)",
            rich_help_panel=G_POWER,
        ),
    ] = None,
    power_mode: Annotated[
        Optional[str],
        typer.Option(
            "--power-mode",
            click_type=_POWER_MODE_CHOICE,
            help="Power mode (default: external)",
            rich_help_panel=G_POWER,
        ),
    ] = None,
    power_duration: Annotated[
        Optional[int],
        typer.Option(
            "--power-duration",
            help="Power capture seconds (default: 30)",
            rich_help_panel=G_POWER,
        ),
    ] = None,
    power_firmware: Annotated[
        Optional[str],
        typer.Option(
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
    ] = None,
    power_reset_strategy: Annotated[
        Optional[str],
        typer.Option(
            "--power-reset-strategy",
            click_type=_POWER_RESET_STRATEGY_CHOICE,
            help=(
                "Reset strategy before power capture (default: auto). "
                "Use explicit values only for board bring-up or controlled experiments."
            ),
            rich_help_panel=G_POWER,
        ),
    ] = None,
    sync_gpio: Annotated[
        Optional[int],
        typer.Option(
            "--sync-gpio",
            help=(
                "GPIO pin for external power sync (default: per-board; "
                "10 only for boards without a registered override)"
            ),
            rich_help_panel=G_POWER,
        ),
    ] = None,
    ensure_power: Annotated[
        bool,
        typer.Option(
            "--ensure-power",
            help=(
                "Scan for a Joulescope at start-up and enable current passthrough "
                "so the board powers on before flashing. Off by default; only "
                "needed when the board's power genuinely comes from the "
                "Joulescope rail (--power already implies this)."
            ),
            rich_help_panel=G_POWER,
        ),
    ] = False,
    no_ensure_power: Annotated[
        bool,
        typer.Option(
            "--no-ensure-power",
            help=(
                "Explicitly skip the auto power-on step, overriding --ensure-power "
                "or a config file's ensure_board_powered: true."
            ),
            rich_help_panel=G_POWER,
        ),
    ] = False,
    power_serial: Annotated[
        Optional[str],
        typer.Option(
            "--power-serial",
            "--js-serial",
            help=(
                "Power instrument serial number to disambiguate when multiple "
                "devices are connected (e.g. Joulescope serial '004204'). "
                "Alias: --js-serial."
            ),
            rich_help_panel=G_POWER,
        ),
    ] = None,
    # -- output --
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", help="Results output directory", rich_help_panel=G_OUTPUT),
    ] = None,
    output_format: Annotated[
        Optional[str],
        typer.Option(
            "--output-format",
            click_type=_OUTPUT_FORMAT_CHOICE,
            help="Output format",
            rich_help_panel=G_OUTPUT,
        ),
    ] = None,
    no_model_explorer: Annotated[
        bool,
        typer.Option(
            "--no-model-explorer",
            help="Skip Model Explorer overlay generation",
            rich_help_panel=G_OUTPUT,
        ),
    ] = False,
    detailed: Annotated[
        bool,
        typer.Option(
            "--detailed",
            help="Emit detailed per-preset/group CSVs and memory breakdown",
            rich_help_panel=G_OUTPUT,
        ),
    ] = False,
    fail_on_invalid: Annotated[
        bool,
        typer.Option(
            "--fail-on-invalid",
            help="Exit 3 when the run evaluates INVALID (artifacts still written)",
            rich_help_panel=G_OUTPUT,
        ),
    ] = False,
    # -- advanced --
    work_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--work-dir",
            help="Working directory for generated firmware",
            rich_help_panel=G_ADVANCED,
        ),
    ] = None,
    clean: Annotated[
        bool,
        typer.Option(
            "--clean",
            help="Wipe cached build directory before building (forces full rebuild)",
            rich_help_panel=G_ADVANCED,
        ),
    ] = False,
) -> None:
    """Profile a model on target hardware."""
    # Snapshot the parameter values before any other local bindings exist;
    # every ``hpx profile`` flag forwards to the implementation by name, so
    # the Typer signature above stays the single declaration of the surface.
    params = dict(locals())
    from .profile_cmd import _cmd_profile

    _cmd_profile(**params)


# ---------------------------------------------------------------------------
# hpx analyze
# ---------------------------------------------------------------------------


_ANALYZE_FORMAT_CHOICE = TyperChoice(["table", "csv", "json"])


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
    model: Annotated[Path, typer.Argument(help="Path to .tflite model file")],
    engine: Annotated[
        Optional[str],
        typer.Option(
            "--engine",
            click_type=_ENGINE_CHOICE,
            help=(
                "Analyze as this engine would execute it. "
                "Default (no flag) uses the raw tflite graph. "
                "'helia-aot' runs AOT compilation and analyzes the transformed graph. "
                "'helia-rt' analyzes the original tflite graph."
            ),
        ),
    ] = None,
    compare: Annotated[
        bool,
        typer.Option(
            "--compare",
            help="Show side-by-side comparison of original vs engine-transformed graph",
        ),
    ] = False,
    format: Annotated[
        str,
        typer.Option(
            "--format",
            click_type=_ANALYZE_FORMAT_CHOICE,
            help="Output format (default: table)",
        ),
    ] = "table",
    output: Annotated[
        Optional[Path], typer.Option("--output", "-o", help="Write output to file")
    ] = None,
    board: Annotated[
        str,
        typer.Option(
            "--board",
            help="Target board for AOT compilation (default: apollo510_evb)",
        ),
    ] = "apollo510_evb",
) -> None:
    """Analyze model compute/parameter breakdown without hardware."""
    from .analyze_cmd import _cmd_analyze

    _cmd_analyze(
        model=model,
        engine=engine,
        compare=compare,
        format=format,
        output=output,
        board=board,
    )


# ---------------------------------------------------------------------------
# hpx doctor / engines / boards / probes / ports / target
#
# Wired from a separate module (cli/inspect_app.py) so this file stays under
# the project's per-module line ceiling — see tests/test_package_layout.py.
# ---------------------------------------------------------------------------

_inspect_app.register(app)


_validation_app.register(app)


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
    baseline: Annotated[Path, typer.Argument(help="Baseline hpx result directory")],
    candidate: Annotated[Path, typer.Argument(help="Candidate hpx result directory")],
    output_dir: Annotated[
        Optional[Path],
        typer.Option("--output-dir", help="Write comparison artifacts to this directory"),
    ] = None,
    profile: Annotated[
        Optional[Path],
        typer.Option(
            "--profile",
            help="Versioned JSON comparison profile for regression verdicts",
        ),
    ] = None,
    validation: Annotated[
        bool,
        typer.Option(
            "--validation",
            help="Compare portable validation bundles instead of profile runs",
        ),
    ] = False,
    top_layers: Annotated[
        int,
        typer.Option(
            "--top-layers",
            help="Number of layer deltas to show in terminal output (default: 10)",
        ),
    ] = 10,
) -> None:
    """Compare two completed profile runs or validation bundles."""
    from .compare_cmd import _cmd_compare

    _cmd_compare(
        baseline=baseline,
        candidate=candidate,
        output_dir=output_dir,
        profile=profile,
        top_layers=top_layers,
        validation=validation,
    )


# ---------------------------------------------------------------------------
# hpx cache {purge, info}
# ---------------------------------------------------------------------------

cache_app = typer.Typer(
    help="Manage hpx/nsx caches",
    epilog=(
        "Manage local caches used by hpx and its nsx dependency:\n\n"
        "  hpx cache purge      Remove all cached data (module artifacts,\n\n"
        "                       git-artifact hashes, resolved refs,\n\n"
        "                       generated workspaces).\n\n"
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


@cache_app.command("purge", help="Remove all NSX caches and HPX workspaces")
def cache_purge_command() -> None:
    from .cache_cmd import _cmd_cache_purge

    _cmd_cache_purge()


@cache_app.command("info", help="Show cache location and disk usage")
def cache_info_command() -> None:
    from .cache_cmd import _cmd_cache_info

    _cmd_cache_info()


app.add_typer(cache_app, name="cache")


__all__ = ["app"]
