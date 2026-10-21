"""Typer wiring for the run API commands: ``hpx freeze``, ``hpx build`` and ``hpx run``.

``hpx validate build`` lives with ``hpx validate`` in ``validation_app.py``.
Each command is a thin adapter over the matching ``_cmd_*`` function in
``run_cmds.py``, which calls the library's run API.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Optional

import typer

from ..engines import EngineType
from ..lifecycle import Lifecycle
from ..placement import Placement
from .choices import _ArenaLocation, _CoreOverride

G_ENGINE = "engine"
G_TARGET = "target hardware"
G_EXPECT = "output verification"


# A copy of FixtureTimingScope: importing _fixture_build would slow every hpx command.
# tests/test_cli_choices.py keeps the two in step.
class _TimingScope(StrEnum):
    INVOKE_ONLY = "invoke_only"
    RESTORE_AND_INVOKE = "restore_and_invoke"


class _Expect(StrEnum):
    HOST_LITERT = "host-litert"
    NONE = "none"


def _positive(value: float) -> float:
    # typer's public Option has no open lower bound, and the capture needs more than 0.
    if not value > 0:
        raise typer.BadParameter(f"{value} is not more than 0")
    return value


def register(app: typer.Typer) -> None:
    """Attach ``freeze``, ``build`` and ``run`` to *app*."""
    app.command(
        "freeze",
        help="Freeze a resolved run plan to JSON for hpx build",
        epilog=(
            "Takes the config file and the engine, target and profiling options of hpx "
            "profile, plus --backend, and writes the resolved plan with the model's hash "
            "and the toolchain and runtime identities, so any host can build the same "
            "run.\n\n"
            "Example:\n\n"
            # One paragraph: a blank line inside the command would end it at a backslash.
            "  hpx freeze model.tflite --engine helia-rt --backend helia \\\n"
            "      --toolchain atfe --cpu-clock lp --arena-size 131072 \\\n"
            "      --arena-location sram --weights-location mram -o plan.json"
        ),
    )(freeze_command)
    app.command(
        "build",
        help="Build a frozen plan for fixed inputs into a run directory",
        epilog=(
            "Writes OUT/build.json, the model and one input_<index>.bin per model input "
            "under OUT/inputs, and the firmware under OUT/work. OUT must be new or empty, "
            "so a failed build never touches an earlier one. heliaRT and LiteRT link the "
            "runtime that hpx runtimes prepare cached under the HPX cache. Prints the saved "
            "build's path, inputs and identity as JSON.\n\n"
            "Example:\n\n"
            "  hpx build plan.json --out runs/kws --input kws_input.bin"
        ),
    )(build_command)
    app.command(
        "run",
        help="Capture once from a run directory on a board you name",
        epilog=(
            "The operator vouches for the physical board behind the probe, as hpx profile "
            "assumes: --board names the board wired to the probe that --jlink-serial names, "
            "and the operator holds it for the capture. A --board other than the build's "
            "board is refused before any device is touched; HPX cannot check the physical "
            "board itself.\n\n"
            "Each capture writes its evidence to a fresh directory. Prints the outcome, "
            "identity, verification and timing as JSON. Exit status: 0 when the capture "
            "succeeds and its outputs match, 1 on a refusal or a failed capture, 3 when "
            "the outputs do not match.\n\n"
            "Example:\n\n"
            "  hpx run runs/kws --board apollo510_evb --jlink-serial 1160002255"
        ),
    )(run_command)


def freeze_command(
    output: Annotated[Path, typer.Option("-o", "--output", help="Where to write the plan (JSON)")],
    model: Annotated[
        Optional[Path], typer.Argument(help="Path to .tflite or .pte model file")
    ] = None,
    config: Annotated[
        Optional[Path], typer.Option("-c", "--config", help="YAML config file (hpx.yml)")
    ] = None,
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity")
    ] = 0,
    engine: Annotated[
        Optional[EngineType],
        typer.Option(
            "--engine", help="Inference engine (default: helia-rt)", rich_help_panel=G_ENGINE
        ),
    ] = None,
    backend: Annotated[
        Optional[str],
        typer.Option(
            "--backend",
            help=(
                "Kernel backend (engine.backend). Run builds need helia for "
                "helia-rt and cmsis_nn for litert."
            ),
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
        Optional[_ArenaLocation],
        typer.Option(
            "--arena-location",
            help="Tensor arena placement. Omit to let the engine and memory planner choose.",
            rich_help_panel=G_ENGINE,
        ),
    ] = None,
    runtime_weights_location: Annotated[
        Optional[Placement],
        typer.Option(
            "--weights-location",
            help="Model weights placement. Omit to let the engine and memory planner choose.",
            rich_help_panel=G_ENGINE,
        ),
    ] = None,
    core_override: Annotated[
        Optional[_CoreOverride],
        typer.Option(
            "--core-override",
            help="Select the core library in an explicit heliaRT prebuilt distribution.",
            rich_help_panel=G_ENGINE,
        ),
    ] = None,
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
            help="Toolchain (default: arm-none-eabi-gcc; run builds use atfe)",
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    cpu_clock: Annotated[
        Optional[str],
        typer.Option(
            "--cpu-clock",
            metavar="SPEED",
            help="CPU clock speed (board-specific, e.g. 'lp'/'hp'; run builds use lp)",
            rich_help_panel=G_TARGET,
        ),
    ] = None,
    iterations: Annotated[
        Optional[int], typer.Option("--iterations", help="Timed iterations (default: 100)")
    ] = None,
    warmup: Annotated[
        Optional[int], typer.Option("--warmup", help="Warmup iterations (default: 5)")
    ] = None,
) -> None:
    """Freeze a resolved run plan."""
    from .run_cmds import _cmd_freeze

    _cmd_freeze(
        model=model,
        output=output,
        config=config,
        verbose=verbose,
        engine=str(engine) if engine is not None else None,
        backend=backend,
        engine_config=engine_config,
        arena_size=arena_size,
        runtime_arena_location=str(runtime_arena_location) if runtime_arena_location else None,
        runtime_weights_location=(
            str(runtime_weights_location) if runtime_weights_location else None
        ),
        core_override=str(core_override) if core_override is not None else None,
        board=board,
        toolchain=toolchain,
        cpu_clock=cpu_clock,
        iterations=iterations,
        warmup=warmup,
    )


def build_command(
    plan: Annotated[Path, typer.Argument(help="Plan written by hpx freeze")],
    out: Annotated[Path, typer.Option("--out", help="Run directory to write")],
    input_: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--input",
            help=(
                "Raw bytes for one model input; repeat once per input, in model order "
                "(default: zero-filled inputs)"
            ),
        ),
    ] = None,
    lifecycle: Annotated[
        Lifecycle,
        typer.Option(
            "--lifecycle",
            help=(
                "carry keeps the plan's iterations and warmups and carries model state; "
                "single times one invocation per boot"
            ),
        ),
    ] = Lifecycle.CARRY,
    timing_scope: Annotated[
        _TimingScope,
        typer.Option(
            "--timing-scope",
            help=(
                "invoke_only times each invocation; restore_and_invoke times the loop, "
                "input restoration included"
            ),
        ),
    ] = _TimingScope.INVOKE_ONLY,
    model: Annotated[
        Optional[Path],
        typer.Option(
            "--model", help="The plan's model, when it is no longer at the path the plan records"
        ),
    ] = None,
    work_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--work-dir",
            help=(
                "Firmware build directory inside OUT (default: OUT/work); the saved build "
                "records its files relative to OUT"
            ),
        ),
    ] = None,
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity")
    ] = 0,
) -> None:
    """Build a frozen plan into a run directory."""
    from .run_cmds import _cmd_build

    _cmd_build(
        plan=plan,
        out=out,
        inputs=list(input_ or ()),
        lifecycle=str(lifecycle),
        timing_scope=str(timing_scope),
        model=model,
        work_dir=work_dir,
        verbose=verbose,
    )


def run_command(
    directory: Annotated[
        Path, typer.Argument(help="Run directory written by hpx build, or its build.json")
    ],
    board: Annotated[
        str,
        typer.Option(
            "--board",
            help="The board wired to the probe; it must be the build's board",
            rich_help_panel=G_TARGET,
        ),
    ],
    jlink_serial: Annotated[
        str,
        typer.Option("--jlink-serial", help="J-Link probe serial number", rich_help_panel=G_TARGET),
    ],
    expect: Annotated[
        Optional[_Expect],
        typer.Option(
            "--expect",
            help=(
                "Reference outputs: host-litert runs the model on the host (default), "
                "none skips verification"
            ),
            rich_help_panel=G_EXPECT,
        ),
    ] = None,
    tolerance: Annotated[
        float,
        typer.Option(
            "--tolerance",
            min=0.0,
            help="Largest accepted absolute error per element, in stored units",
            rich_help_panel=G_EXPECT,
        ),
    ] = 0.0,
    reference: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--reference",
            help=(
                "Reference bytes for one output; repeat once per output, in model order. "
                "Replaces --expect."
            ),
            rich_help_panel=G_EXPECT,
        ),
    ] = None,
    settle_seconds: Annotated[
        float,
        typer.Option(
            "--settle-seconds",
            max=300.0,
            callback=_positive,
            help=(
                "Bound on each wait for the board to start and to finish, in seconds "
                "(more than 0, at most 300)"
            ),
        ),
    ] = 60.0,
    evidence_dir: Annotated[
        Optional[Path],
        typer.Option(
            "--evidence-dir",
            help="New directory for the capture's evidence (default: DIR/evidence-<N>)",
        ),
    ] = None,
    verbose: Annotated[
        int, typer.Option("-v", "--verbose", count=True, help="Increase verbosity")
    ] = 0,
) -> None:
    """Capture once from a run directory."""
    from .run_cmds import _cmd_run

    _cmd_run(
        directory=directory,
        board=board,
        jlink_serial=jlink_serial,
        expect=str(expect) if expect is not None else None,
        tolerance=tolerance,
        references=list(reference or ()),
        settle_seconds=settle_seconds,
        evidence_dir=evidence_dir,
        verbose=verbose,
    )
