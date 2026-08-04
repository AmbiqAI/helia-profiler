"""Typer wiring for the ``hpx`` inspection/utility command cluster.

Covers ``doctor``, ``engines``, ``boards``, ``probes``, ``ports``, and
``target`` — the same cluster ``cli/inspect_cmds.py`` implements. Kept in
its own module (mirroring that split) so ``cli/app.py`` stays under the
project's per-module line ceiling as this cluster grows (see
``tests/test_package_layout.py``).

Each Typer command function is a thin adapter, identical in spirit to
``app.py``: it collects typed CLI parameters, assembles a
``types.SimpleNamespace`` with exactly the attribute names the existing
``_cmd_*`` implementation functions in ``inspect_cmds.py`` read, and calls
into that unchanged implementation.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import click
import typer

from ..config import Transport
from ..engines import EngineType

_ENGINE_CHOICE = click.Choice([engine.value for engine in EngineType])
_TRANSPORT_CHOICE = click.Choice([t.value for t in Transport])
_TARGET_RESET_KIND_CHOICE = click.Choice(["debug", "swpoi"])


def register(app: typer.Typer) -> None:
    """Attach the doctor/engines/boards/probes/ports/target commands to *app*."""
    app.command("doctor", help="Check toolchain and dependencies")(doctor_command)
    app.command("engines", help="List available inference engines")(engines_command)
    app.command("boards", help="List supported boards and SoC capabilities")(boards_command)
    app.add_typer(probes_app, name="probes")
    app.add_typer(ports_app, name="ports")
    app.add_typer(target_app, name="target")


# ---------------------------------------------------------------------------
# hpx doctor / engines / boards
# ---------------------------------------------------------------------------


def doctor_command(
    json_: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    bundle: Optional[Path] = typer.Option(
        None,
        "--bundle",
        help=(
            "Write a sanitized support-bundle archive to this file or directory "
            "instead of printing the toolchain table"
        ),
    ),
    workspace: Optional[Path] = typer.Option(
        None,
        "--workspace",
        help=(
            "Prepared profiler_app directory (or its nsx.lock/hpx-dependencies.json, "
            "or the parent fingerprint workspace) to include exact dependency lock "
            "provenance in the bundle"
        ),
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Resolve this YAML config and include a sanitized snapshot in the bundle",
    ),
    toolchain: Optional[str] = typer.Option(
        None, "--toolchain", help="Toolchain to check (default: arm-none-eabi-gcc)"
    ),
    transport: Optional[str] = typer.Option(
        None, "--transport", click_type=_TRANSPORT_CHOICE, help="Transport to check (default: rtt)"
    ),
    engine: Optional[str] = typer.Option(
        None, "--engine", click_type=_ENGINE_CHOICE, help="Engine to check (default: helia-rt)"
    ),
    no_probes: bool = typer.Option(
        False, "--no-probes", help="Skip live J-Link probe enumeration in --bundle"
    ),
    no_ports: bool = typer.Option(
        False, "--no-ports", help="Skip live serial port enumeration in --bundle"
    ),
    raw_probe_ids: bool = typer.Option(
        False,
        "--raw-probe-ids",
        help="Include unredacted device serial numbers in --bundle (opt-in; prints a warning)",
    ),
) -> None:
    """Check toolchain and dependencies."""
    from .inspect_cmds import _cmd_doctor

    _cmd_doctor(
        SimpleNamespace(
            json=json_,
            bundle=str(bundle) if bundle is not None else None,
            workspace=str(workspace) if workspace is not None else None,
            config=str(config) if config is not None else None,
            toolchain=toolchain,
            transport=transport,
            engine=engine,
            no_probes=no_probes,
            no_ports=no_ports,
            raw_probe_ids=raw_probe_ids,
        )
    )


def engines_command() -> None:
    """List available inference engines."""
    from .inspect_cmds import _cmd_engines

    _cmd_engines()


def boards_command() -> None:
    """List supported boards and their SoC capabilities."""
    from .inspect_cmds import _cmd_boards

    _cmd_boards()


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


# ---------------------------------------------------------------------------
# hpx target {reset}
# ---------------------------------------------------------------------------

target_app = typer.Typer(help="Run explicit target-side utility operations")


@target_app.callback(invoke_without_command=True)
def _target_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        raise typer.Exit(0)


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
