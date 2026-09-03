"""Implementation of the hpx inspect/utility commands.

Covers ``doctor``, ``engines``, ``boards``, ``probes``, ``ports``, and
``target`` — read-only or single-shot utility commands that don't run the
full profiling pipeline.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence

from ..config import Toolchain, Transport
from ..engines import EngineType
from .common import _print_hpx_error

_DOCTOR_DEFAULT_TOOLCHAIN = Toolchain.ARM_NONE_EABI_GCC
_DOCTOR_DEFAULT_TRANSPORT = Transport.RTT
_DOCTOR_DEFAULT_ENGINE = EngineType.HELIA_RT


def _resolve_doctor_env(
    toolchain_raw: str | None, transport_raw: str | None, engine_raw: str | None
) -> tuple[Toolchain, Transport, EngineType]:
    """Resolve the CLI's plain --toolchain/--transport/--engine strings to enums."""
    try:
        toolchain = Toolchain(toolchain_raw) if toolchain_raw else _DOCTOR_DEFAULT_TOOLCHAIN
        transport = Transport(transport_raw) if transport_raw else _DOCTOR_DEFAULT_TRANSPORT
        engine = EngineType(engine_raw) if engine_raw else _DOCTOR_DEFAULT_ENGINE
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    return toolchain, transport, engine


def _cmd_doctor(
    *,
    json_: bool = False,
    bundle: str | None = None,
    workspace: str | None = None,
    config: str | None = None,
    toolchain: str | None = None,
    transport: str | None = None,
    engine: str | None = None,
    no_probes: bool = False,
    no_ports: bool = False,
    raw_probe_ids: bool = False,
) -> None:
    """Check toolchain and dependencies; optionally emit JSON or a support bundle."""
    from ..hostenv.doctor import inspect_environment
    from ..console import HpxConsole

    if bundle is not None:
        _cmd_doctor_bundle(
            bundle=bundle,
            json_=json_,
            workspace=workspace,
            config=config,
            toolchain=toolchain,
            transport=transport,
            engine=engine,
            no_probes=no_probes,
            no_ports=no_ports,
            raw_probe_ids=raw_probe_ids,
        )
        return

    resolved_toolchain, resolved_transport, resolved_engine = _resolve_doctor_env(
        toolchain, transport, engine
    )
    result = inspect_environment(
        toolchain=resolved_toolchain,
        transport=resolved_transport,
        engine=resolved_engine,
        include_versions=json_,
    )
    if json_:
        print(json.dumps(result.to_dict(), indent=2))
        return
    console = HpxConsole()
    console.print_doctor(result)


def _cmd_doctor_bundle(
    *,
    bundle: str,
    json_: bool,
    workspace: str | None,
    config: str | None,
    toolchain: str | None,
    transport: str | None,
    engine: str | None,
    no_probes: bool,
    no_ports: bool,
    raw_probe_ids: bool,
) -> None:
    """Collect and write an ``hpx doctor --bundle`` support archive."""
    from pathlib import Path

    from ..errors import HpxError
    from ..diagnostics.support_bundle import (
        SupportBundleOptions,
        collect_support_bundle,
        write_support_bundle,
    )

    if raw_probe_ids:
        print(
            "Warning: --raw-probe-ids includes unredacted device serial numbers "
            "in the support bundle.",
            file=sys.stderr,
        )

    resolved_toolchain, resolved_transport, resolved_engine = _resolve_doctor_env(
        toolchain, transport, engine
    )
    options = SupportBundleOptions(
        workspace=Path(workspace).expanduser() if workspace else None,
        config_path=Path(config).expanduser() if config else None,
        toolchain=resolved_toolchain,
        transport=resolved_transport,
        engine=resolved_engine,
        include_probes=not no_probes,
        include_ports=not no_ports,
        raw_probe_ids=raw_probe_ids,
    )

    try:
        collection = collect_support_bundle(options)
        path = write_support_bundle(collection, Path(bundle).expanduser())
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if json_:
        print(json.dumps({"path": str(path), "manifest": collection.manifest.to_dict()}, indent=2))
    else:
        print(f"Support bundle written to {path}")
        unavailable = [
            section.name for section in collection.manifest.sections if not section.available
        ]
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


def _cmd_probes_list(
    *, board: str | None = None, inspect: bool = False, json_: bool = False
) -> None:
    from ..errors import HpxError
    from ..target.probe.jlink import inspect_probe_target, list_connected_probes

    board_name = board
    inspect = bool(inspect or board_name)
    if inspect and not board_name:
        print("Error: hpx probes list --inspect requires --board.", file=sys.stderr)
        sys.exit(2)

    board_def = soc = None
    if board_name:
        try:
            from ..platform import get_board, get_soc_for_board

            board_def = get_board(board_name)
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
                row["board"] = board_def.name if board_def is not None else board_name
                row["jlink_device"] = soc.jlink_device
            rows.append(row)
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if json_:
        print(json.dumps({"probes": rows}, indent=2))
        return
    if not rows:
        print("No J-Link probes detected.")
        return
    if inspect:
        _print_rows(rows, ("serial", "product", "connection", "detected_core", "matches_board"))
    else:
        _print_rows(rows, ("serial", "product", "connection"))


def _cmd_probes_match(*, board: str, jlink_serial: str | None = None, json_: bool = False) -> None:
    from ..errors import HpxError
    from ..target.probe.jlink import resolve_probe_serial

    try:
        from ..platform import get_soc_for_board

        soc = get_soc_for_board(board)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        serial = resolve_probe_serial(
            device=soc.jlink_device,
            expected_core=soc.core,
            requested_serial=jlink_serial,
        )
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    if json_:
        print(json.dumps({"board": board, "serial": serial}, indent=2))
    else:
        print(f"{board}: {serial}")


def _cmd_ports_list(*, show_all: bool = False, json_: bool = False) -> None:
    try:
        from ..transport.ports import list_serial_ports
    except ImportError:
        print("Error: pyserial is required for hpx ports list.", file=sys.stderr)
        sys.exit(1)

    ports = list_serial_ports(include_all=show_all)
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
    if json_:
        print(json.dumps({"ports": rows}, indent=2))
        return
    if not rows:
        print("No serial ports detected.")
        return
    _print_rows(rows, ("device", "kind", "serial_number", "description", "product"))


def _cmd_target_reset(*, board: str, jlink_serial: str | None = None, kind: str = "debug") -> None:
    from ..errors import HpxError
    from ..target.probe.jlink import reset_target, reset_target_poi

    try:
        from ..platform import get_soc_for_board

        soc = get_soc_for_board(board)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    try:
        if kind == "swpoi":
            reset_target_poi(device=soc.jlink_device, jlink_serial=jlink_serial)
        else:
            reset_target(device=soc.jlink_device, jlink_serial=jlink_serial)
    except HpxError as exc:
        _print_hpx_error(exc)
        sys.exit(1)

    serial = jlink_serial or "auto"
    print(f"Reset {board} via {kind} reset (serial={serial}).")


def _print_rows(rows: Sequence[Mapping[str, object]], columns: tuple[str, ...]) -> None:
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
