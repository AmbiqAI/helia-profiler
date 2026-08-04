"""Implementation of the hpx inspect/utility commands.

Covers ``doctor``, ``engines``, ``boards``, ``probes``, ``ports``, and
``target`` — read-only or single-shot utility commands that don't run the
full profiling pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys

from ..config import Toolchain, Transport
from ..engines import EngineType
from .common import _print_hpx_error

_DOCTOR_DEFAULT_TOOLCHAIN = Toolchain.ARM_NONE_EABI_GCC
_DOCTOR_DEFAULT_TRANSPORT = Transport.RTT
_DOCTOR_DEFAULT_ENGINE = EngineType.HELIA_RT


def _resolve_doctor_env(args: argparse.Namespace | None) -> tuple[Toolchain, Transport, EngineType]:
    """Resolve the CLI's plain --toolchain/--transport/--engine strings to enums."""
    toolchain_raw = getattr(args, "toolchain", None)
    transport_raw = getattr(args, "transport", None)
    engine_raw = getattr(args, "engine", None)
    try:
        toolchain = Toolchain(toolchain_raw) if toolchain_raw else _DOCTOR_DEFAULT_TOOLCHAIN
        transport = Transport(transport_raw) if transport_raw else _DOCTOR_DEFAULT_TRANSPORT
        engine = EngineType(engine_raw) if engine_raw else _DOCTOR_DEFAULT_ENGINE
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    return toolchain, transport, engine


def _cmd_doctor(args: argparse.Namespace | None = None) -> None:
    """Check toolchain and dependencies; optionally emit JSON or a support bundle."""
    from ..doctor import inspect_environment
    from ..console import HpxConsole

    json_ = bool(getattr(args, "json", False))
    bundle = getattr(args, "bundle", None)

    if bundle is not None:
        _cmd_doctor_bundle(args)
        return

    toolchain, transport, engine = _resolve_doctor_env(args)
    result = inspect_environment(
        toolchain=toolchain,
        transport=transport,
        engine=engine,
        include_versions=json_,
    )
    if json_:
        print(json.dumps(result.to_dict(), indent=2))
        return
    console = HpxConsole()
    console.print_doctor(result)


def _cmd_doctor_bundle(args: argparse.Namespace) -> None:
    """Collect and write an ``hpx doctor --bundle`` support archive."""
    from pathlib import Path

    from ..errors import HpxError
    from ..support_bundle import (
        SupportBundleOptions,
        collect_support_bundle,
        write_support_bundle,
    )

    raw_probe_ids = bool(getattr(args, "raw_probe_ids", False))
    if raw_probe_ids:
        print(
            "Warning: --raw-probe-ids includes unredacted device serial numbers "
            "in the support bundle.",
            file=sys.stderr,
        )

    toolchain, transport, engine = _resolve_doctor_env(args)
    workspace = getattr(args, "workspace", None)
    config_path = getattr(args, "config", None)
    options = SupportBundleOptions(
        workspace=Path(workspace).expanduser() if workspace else None,
        config_path=Path(config_path).expanduser() if config_path else None,
        toolchain=toolchain,
        transport=transport,
        engine=engine,
        include_probes=not bool(getattr(args, "no_probes", False)),
        include_ports=not bool(getattr(args, "no_ports", False)),
        raw_probe_ids=raw_probe_ids,
    )

    try:
        collection = collect_support_bundle(options)
        path = write_support_bundle(collection, Path(args.bundle).expanduser())
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if bool(getattr(args, "json", False)):
        print(
            json.dumps(
                {"path": str(path), "manifest": collection.manifest.to_dict()}, indent=2
            )
        )
    else:
        print(f"Support bundle written to {path}")
        unavailable = [section.name for section in collection.manifest.sections if not section.available]
        if unavailable:
            print(f"Skipped sections: {', '.join(unavailable)}")


def _cmd_engines() -> None:
    """List available inference engines."""
    from ..console import HpxConsole

    console = HpxConsole()
    console.print_engines([engine.value for engine in EngineType])


def _cmd_boards() -> None:
    """List supported boards and their SoC capabilities."""
    from ..platform import get_soc, list_boards
    from ..console import HpxConsole

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
    from ..errors import HpxError
    from ..target.probe.jlink import inspect_probe_target, list_connected_probes

    board_name = getattr(args, "board", None)
    inspect = bool(getattr(args, "inspect", False) or board_name)
    if inspect and not board_name:
        print("Error: hpx probes list --inspect requires --board.", file=sys.stderr)
        sys.exit(2)

    board = soc = None
    if board_name:
        try:
            from ..platform import get_board, get_soc_for_board

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
    from ..errors import HpxError
    from ..target.probe.jlink import resolve_probe_serial

    try:
        from ..platform import get_soc_for_board

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
        from ..transport.ports import list_serial_ports
    except ImportError:
        print("Error: pyserial is required for hpx ports list.", file=sys.stderr)
        sys.exit(1)

    ports = list_serial_ports(include_all=args.show_all)
    rows = [
        {
            "device": port.device,
            "kind": port.kind,
            "description": port.description,
            "manufacturer": port.manufacturer,
            "product": port.product,
            "serial_number": port.serial_number,
            "interface": port.interface,
            "hwid": port.hwid,
        }
        for port in ports
    ]
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
    from ..errors import HpxError
    from ..target.probe.jlink import reset_target, reset_target_poi

    try:
        from ..platform import get_soc_for_board

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


def _print_rows(rows: list[dict[str, object]], columns: tuple[str, ...]) -> None:
    widths = {
        col: max(len(col), *(len(_cell_text(row.get(col))) for row in rows)) for col in columns
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
