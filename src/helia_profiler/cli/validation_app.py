"""Hardware-facing CLI commands: ``hpx power-on`` and ``hpx validate``.

Split from ``app.py`` at its size ceiling, following the ``inspect_app``
pattern: plain command functions here, attached to the main Typer app via
:func:`register` so the module needs no import of ``app`` itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

# typer vendors click for ``click_type``; see the note in ``app.py``.
from typer._types import TyperChoice


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
    app.command(
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
            "  hpx validate --suite complete        # RT + AOT + TFLM + ExecuTorch sweep"
        ),
    )(validate_command)

# ---------------------------------------------------------------------------
# hpx power-on
# ---------------------------------------------------------------------------

_POWER_ON_DRIVER_CHOICE = TyperChoice(["joulescope"])


def power_on_command(
    driver: Annotated[
        str,
        typer.Option(
            "--driver",
            click_type=_POWER_ON_DRIVER_CHOICE,
            help="Joulescope driver (default: auto-detect)",
        ),
    ] = "joulescope",
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

    _cmd_power_on(driver, power_serial=power_serial)


# ---------------------------------------------------------------------------
# hpx validate
# ---------------------------------------------------------------------------

_VALIDATE_POWER_CHOICE = TyperChoice(["both", "on", "off"])
_VALIDATE_SUITE_CHOICE = TyperChoice(["smoke", "models-rt", "models-aot", "complete"])


def validate_command(
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
                "Comma-separated engines: rt,aot,tflm,et,executorch,helia-rt,helia-aot "
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
                "TFLM always uses ARM CMSIS-NN; heliaRT and heliaAOT always use ns-cmsis-nn."
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
        str,
        typer.Option(
            "--power",
            click_type=_VALIDATE_POWER_CHOICE,
            help="Power matrix: off (default) | on (only Joulescope runs) | both.",
        ),
    ] = "off",
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
        Optional[str],
        typer.Option(
            "--suite",
            click_type=_VALIDATE_SUITE_CHOICE,
            help=(
                "Preset suite. 'smoke' defaults unset axes to models=kws, engines=helia-rt, "
                "toolchains=arm-none-eabi-gcc, interfaces=rtt, memories=auto. "
                "'models-rt' and 'models-aot' default unset axes to all MLPerf Tiny models, "
                "Apollo510 + Apollo330mP, gcc + atfe, rtt, auto memory, and the selected engine. "
                "'complete' runs the same axes for helia-rt, helia-aot, TFLM/CMSIS-NN, "
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
        power=power,
        power_boards=power_boards,
        boards=boards,
        toolchains=toolchains,
        transports=transports,
        memories=memories,
        suite=suite,
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
