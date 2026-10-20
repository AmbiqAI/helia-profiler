"""Hardware-facing CLI commands: ``hpx power-on``, ``hpx validate`` and ``hpx validate build``.

Follows the ``inspect_app`` pattern: plain command functions here, attached
to the main Typer app via :func:`register` so the module needs no import of
``app`` itself. ``hpx validate`` is a group whose callback runs the MLPerf
suite when no subcommand is given, so ``hpx validate [OPTIONS]`` keeps working
beside ``hpx validate build DIR``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Optional

import typer


def register(app: typer.Typer) -> None:
    """Attach the hardware-facing commands (power-on, validate) to *app*."""
    app.command(
        "power-on",
        help="Enable Joulescope current passthrough (keeps board powered)",
        epilog=(
            "Opens the Joulescope and enables current passthrough so the\n\n"
            "target board stays powered.  Holds the connection open until\n\n"
            "Ctrl-C.  Useful when the Joulescope app is not running and the\n\n"
            "board would otherwise be unpowered."
        ),
    )(power_on_command)
    app.add_typer(validate_app, name="validate")


validate_app = typer.Typer(
    help=(
        "Run hardware-in-the-loop validation suite (MLPerf Tiny models), "
        "or verify a run directory with `hpx validate build`"
    ),
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
        "  hpx validate --suite complete        # RT + AOT + LiteRT + ExecuTorch sweep\n\n"
        "  hpx validate build runs/kws          # verify a run directory, no hardware"
    ),
)


class _PowerOnDriver(StrEnum):
    JOULESCOPE = "joulescope"


def power_on_command(
    driver: Annotated[
        _PowerOnDriver,
        typer.Option(
            "--driver",
            help="Power driver that holds the target rail on (JS110/JS220/JS320 auto-detected)",
        ),
    ] = _PowerOnDriver.JOULESCOPE,
    power_serial: Annotated[
        Optional[str],
        typer.Option(
            "--power-serial",
            "--js-serial",
            help="Joulescope serial number to select when multiple are connected",
        ),
    ] = None,
) -> None:
    """Enable Joulescope current passthrough and hold open until Ctrl-C."""
    from .power_cmd import _cmd_power_on

    _cmd_power_on(str(driver), power_serial=power_serial)


class _ValidatePower(StrEnum):
    BOTH = "both"
    ON = "on"
    OFF = "off"


class _ValidateSuite(StrEnum):
    SMOKE = "smoke"
    MODELS_RT = "models-rt"
    MODELS_AOT = "models-aot"
    COMPLETE = "complete"


@validate_app.callback(invoke_without_command=True)
def validate_command(
    ctx: typer.Context,
    models: Annotated[
        str,
        typer.Option(
            "--models",
            help="Comma-separated model IDs (default: all). See `hpx validate --list`.",
        ),
    ] = "",
    models_file: Annotated[
        Optional[Path],
        typer.Option(
            "--models-file",
            help="YAML registry of custom validation models and comparison groups.",
        ),
    ] = None,
    model_paths: Annotated[
        str,
        typer.Option(
            "--model-paths",
            help="Comma-separated .tflite paths for an ad hoc comparison.",
        ),
    ] = "",
    comparison_group: Annotated[
        str,
        typer.Option(
            "--comparison-group",
            help="Shared decision group for models supplied through --model-paths.",
        ),
    ] = "custom",
    model_arena_size: Annotated[
        int,
        typer.Option(
            "--model-arena-size",
            help="Arena size in bytes for models supplied through --model-paths.",
        ),
    ] = 524288,
    engines: Annotated[
        str,
        typer.Option(
            "--engines",
            help=(
                "Comma-separated engines: rt,aot,litert,et,executorch,helia-rt,helia-aot "
                "(default: all)."
            ),
        ),
    ] = "",
    executorch_backends: Annotated[
        str,
        typer.Option(
            "--executorch-backends",
            help=(
                "ExecuTorch CMSIS-NN providers: arm, ns, or both (default: both). "
                "LiteRT always uses ARM CMSIS-NN; heliaRT and heliaAOT always use ns-cmsis-nn."
            ),
        ),
    ] = "both",
    ns_cmsis_nn_ref: Annotated[
        str,
        typer.Option(
            "--ns-cmsis-nn-ref",
            help="Exact ns-cmsis-nn commit/ref used by heliaRT, heliaAOT, and ExecuTorch/ns.",
        ),
    ] = "",
    power: Annotated[
        _ValidatePower,
        typer.Option(
            "--power",
            help="Power matrix: off (default) | on (only Joulescope runs) | both.",
        ),
    ] = _ValidatePower.OFF,
    power_boards: Annotated[
        str,
        typer.Option(
            "--power-boards",
            help=(
                "Comma-separated boards allowed to use power capture "
                "(default: all selected boards)."
            ),
        ),
    ] = "",
    boards: Annotated[
        str,
        typer.Option("--boards", help="Comma-separated board IDs (default: apollo510_evb)."),
    ] = "",
    toolchains: Annotated[
        str,
        typer.Option(
            "--toolchains",
            help="Comma-separated toolchains: gcc,armclang/acfe,atfe (default: board defaults).",
        ),
    ] = "",
    transports: Annotated[
        str,
        typer.Option(
            "--interfaces",
            "--transports",
            help=(
                "Comma-separated interfaces/transports: rtt,uart,swo,usb_cdc "
                "(default: board defaults)."
            ),
        ),
    ] = "",
    memories: Annotated[
        str,
        typer.Option(
            "--memories",
            help=(
                "Comma-separated model placement presets: auto,tcm,sram,mram,psram "
                "(default: board defaults)."
            ),
        ),
    ] = "",
    suite: Annotated[
        Optional[_ValidateSuite],
        typer.Option(
            "--suite",
            help=(
                "Preset suite. 'smoke' defaults unset axes to models=kws, engines=helia-rt, "
                "toolchains=arm-none-eabi-gcc, interfaces=rtt, memories=auto. "
                "'models-rt' and 'models-aot' default unset axes to all MLPerf Tiny models, "
                "Apollo510 + Apollo330mP, gcc + atfe, rtt, auto memory, and the selected engine. "
                "'complete' runs the same axes for helia-rt, helia-aot, LiteRT/CMSIS-NN, "
                "and both ExecuTorch CMSIS-NN providers. "
                "Explicit axis flags always win."
            ),
        ),
    ] = None,
    jlink_serials: Annotated[
        str,
        typer.Option(
            "--jlink-serials",
            help="Comma-separated board=serial entries for multi-board validation.",
        ),
    ] = "",
    power_serials: Annotated[
        str,
        typer.Option(
            "--power-serials",
            help=(
                "Comma-separated board=Joulescope-serial entries for powered "
                "multi-board validation."
            ),
        ),
    ] = "",
    power_gpios: Annotated[
        str,
        typer.Option(
            "--power-gpios",
            help=(
                "Comma-separated board=gate:state:go entries for powered boards "
                "without default sync wiring."
            ),
        ),
    ] = "",
    repeat: Annotated[
        int,
        typer.Option(
            "--repeat",
            help="Repeat each selected case N times for stress testing (default: 1).",
        ),
    ] = 1,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help=(
                "Where to write per-case artifacts + summary report "
                "(default: ./results/validation)."
            ),
        ),
    ] = Path("results/validation"),
    timeout: Annotated[
        float, typer.Option("--timeout", help="Per-case timeout in seconds (default: 900).")
    ] = 900.0,
    keyword: Annotated[
        str,
        typer.Option(
            "-k",
            help="Pytest keyword expression — filter cases by substring match (e.g. 'kws-aot').",
        ),
    ] = "",
    junit_xml: Annotated[
        Optional[Path],
        typer.Option(
            "--junit-xml", help="Emit JUnit-XML report at this path (for CI consumption)."
        ),
    ] = None,
    list_: Annotated[
        bool, typer.Option("--list", help="List matching cases and exit without running.")
    ] = False,
    verbose: Annotated[int, typer.Option("-v", "--verbose", count=True)] = 0,
) -> None:
    """Drive the hardware validation suite via pytest."""
    if ctx.invoked_subcommand is not None:
        given = sorted(
            f"--{name.rstrip('_').replace('_', '-')}"
            for name in ctx.params
            # Typer vendors its own click, so the source enum is matched by name.
            if getattr(ctx.get_parameter_source(name), "name", "DEFAULT") != "DEFAULT"
        )
        if given:
            raise typer.BadParameter(
                f"validation suite options do not apply to "
                f"`hpx validate {ctx.invoked_subcommand}`: {', '.join(given)}"
            )
        return
    from .validate_cmd import _cmd_validate

    _cmd_validate(
        models=models,
        models_file=models_file,
        model_paths=model_paths,
        comparison_group=comparison_group,
        model_arena_size=model_arena_size,
        engines=engines,
        executorch_backends=executorch_backends,
        ns_cmsis_nn_ref=ns_cmsis_nn_ref,
        power=str(power),
        power_boards=power_boards,
        boards=boards,
        toolchains=toolchains,
        transports=transports,
        memories=memories,
        suite=str(suite) if suite is not None else None,
        jlink_serials=jlink_serials,
        power_serials=power_serials,
        power_gpios=power_gpios,
        repeat=repeat,
        output_dir=output_dir,
        timeout=timeout,
        keyword=keyword,
        junit_xml=junit_xml,
        list_=list_,
        verbose=verbose,
    )


@validate_app.command("build", help="Verify a run directory written by hpx build (no hardware)")
def validate_build_command(
    directory: Annotated[
        Path, typer.Argument(help="Run directory written by hpx build, or its build.json")
    ],
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity")
    ] = 0,
) -> None:
    """Load and verify a run directory's build; print its identity as JSON."""
    from .run_cmds import _cmd_validate_build

    _cmd_validate_build(directory=directory, verbose=verbose)
