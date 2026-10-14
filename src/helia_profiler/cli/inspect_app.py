"""Typer wiring for the ``hpx`` inspection/utility command cluster.

Covers ``doctor``, ``engines``, ``boards``, ``probes``, ``ports``, and
``target`` — the same cluster ``cli/inspect_cmds.py`` implements. Kept in
its own module (mirroring that split) so ``cli/app.py`` stays under the
project's per-module line ceiling as this cluster grows (see
``tests/test_package_layout.py``).

Each Typer command function is a thin adapter: it collects typed CLI
parameters and forwards them as keyword arguments to the matching
``_cmd_*`` implementation function in ``inspect_cmds.py``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Optional

import click
import typer

from ..config import Transport
from ..engines import EngineType


class _TargetResetKind(StrEnum):
    DEBUG = "debug"
    SWPOI = "swpoi"


def register(app: typer.Typer) -> None:
    """Attach the doctor/engines/boards/probes/ports/target commands to *app*."""
    app.command("doctor", help="Check toolchain and dependencies")(doctor_command)
    app.command("engines", help="List available inference engines")(engines_command)
    app.command("boards", help="List supported boards and SoC capabilities")(boards_command)
    app.add_typer(probes_app, name="probes")
    app.add_typer(ports_app, name="ports")
    app.add_typer(target_app, name="target")


def doctor_command(
    json_: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
    bundle: Annotated[
        Optional[Path],
        typer.Option(
            "--bundle",
            help=(
                "Write a sanitized support-bundle archive to this file or directory "
                "instead of printing the toolchain table"
            ),
        ),
    ] = None,
    workspace: Annotated[
        Optional[Path],
        typer.Option(
            "--workspace",
            help=(
                "Prepared profiler_app directory (or its nsx.lock/hpx-dependencies.json, "
                "or the parent fingerprint workspace) to include exact dependency lock "
                "provenance in the bundle"
            ),
        ),
    ] = None,
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            help=(
                "YAML config whose target and engine select the checks; "
                "with --bundle, a sanitized snapshot of it is included instead"
            ),
        ),
    ] = None,
    toolchain: Annotated[
        Optional[str],
        typer.Option("--toolchain", help="Toolchain to check (default: arm-none-eabi-gcc)"),
    ] = None,
    transport: Annotated[
        Optional[Transport],
        typer.Option(
            "--transport",
            help="Transport to check (default: rtt)",
        ),
    ] = None,
    engine: Annotated[
        Optional[EngineType],
        typer.Option("--engine", help="Engine to check (default: helia-rt)"),
    ] = None,
    no_probes: Annotated[
        bool,
        typer.Option("--no-probes", help="Skip live J-Link probe enumeration in --bundle"),
    ] = False,
    no_ports: Annotated[
        bool,
        typer.Option("--no-ports", help="Skip live serial port enumeration in --bundle"),
    ] = False,
    raw_probe_ids: Annotated[
        bool,
        typer.Option(
            "--raw-probe-ids",
            help="Include unredacted device serial numbers in --bundle (opt-in; prints a warning)",
        ),
    ] = False,
) -> None:
    """Check toolchain and dependencies."""
    from .inspect_cmds import _cmd_doctor

    _cmd_doctor(
        json_=json_,
        bundle=str(bundle) if bundle is not None else None,
        workspace=str(workspace) if workspace is not None else None,
        config=str(config) if config is not None else None,
        toolchain=toolchain,
        transport=str(transport) if transport is not None else None,
        engine=str(engine) if engine is not None else None,
        no_probes=no_probes,
        no_ports=no_ports,
        raw_probe_ids=raw_probe_ids,
    )


def engines_command() -> None:
    """List available inference engines."""
    from .inspect_cmds import _cmd_engines

    _cmd_engines()


def boards_command() -> None:
    """List supported boards and their SoC capabilities."""
    from .inspect_cmds import _cmd_boards

    _cmd_boards()


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
    board: Annotated[
        Optional[str],
        typer.Option(
            "--board", help="Inspect each probe against this board's J-Link device string"
        ),
    ] = None,
    inspect: Annotated[
        bool, typer.Option("--inspect", help="Inspect target cores. Requires --board.")
    ] = False,
    json_: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    from .inspect_cmds import _cmd_probes_list

    _cmd_probes_list(board=board, inspect=inspect, json_=json_)


@probes_app.command(
    "match", help="Resolve the J-Link serial for a board using HPX's normal selection policy"
)
def probes_match_command(
    board: Annotated[str, typer.Option("--board", help="Target board ID")],
    jlink_serial: Annotated[
        Optional[str],
        typer.Option(
            "--jlink-serial",
            help="Optional requested serial to validate against the selected board",
        ),
    ] = None,
    json_: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    from .inspect_cmds import _cmd_probes_match

    _cmd_probes_match(board=board, jlink_serial=jlink_serial, json_=json_)


ports_app = typer.Typer(help="List host serial ports relevant to HPX transports")


@ports_app.callback(invoke_without_command=True)
def _ports_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


@ports_app.command("list", help="List serial ports with J-Link/CDC hints")
def ports_list_command(
    show_all: Annotated[
        bool,
        typer.Option(
            "--all", help="Show every host serial port, not just HPX-relevant USB/J-Link ports"
        ),
    ] = False,
    json_: Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON")] = False,
) -> None:
    from .inspect_cmds import _cmd_ports_list

    _cmd_ports_list(show_all=show_all, json_=json_)


target_app = typer.Typer(help="Run explicit target-side utility operations")


@target_app.callback(invoke_without_command=True)
def _target_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


@target_app.command("reset", help="Reset a target through HPX's non-interactive J-Link wrapper")
def target_reset_command(
    board: Annotated[str, typer.Option("--board", help="Target board ID")],
    jlink_serial: Annotated[
        Optional[str], typer.Option("--jlink-serial", help="J-Link probe serial number")
    ] = None,
    kind: Annotated[
        _TargetResetKind,
        typer.Option(
            "--kind",
            help="Reset kind: debug r/g reset (default) or SWPOI reset",
        ),
    ] = _TargetResetKind.DEBUG,
) -> None:
    from .inspect_cmds import _cmd_target_reset

    _cmd_target_reset(board=board, jlink_serial=jlink_serial, kind=str(kind))
