"""Offline result commands; no operational CLI registration is needed to read evidence."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from ..api import export_result, inspect_result, load_result, observations
from ..readers.native import ResultReadError

results_app = typer.Typer(
    help="Inspect and export verified profile bundles and saved run results.", add_completion=False
)
app = results_app


class _ExportFormat(StrEnum):
    JSON = "json"
    PERFETTO = "perfetto"


@results_app.callback()
def results() -> None:
    """Inspect and export verified profile bundles and saved run results."""


@results_app.command("inspect")
def inspect(location: Annotated[Path, typer.Argument()]) -> None:
    """Emit a structured inspection; a verified failed run is not corrupt evidence."""
    checked = inspect_result(location)
    view = None
    code, message = checked.code, checked.message
    if checked.result is not None:
        try:
            view = observations(checked.result)
        except ResultReadError as exc:
            code, message = exc.code, str(exc)
    status = "verified"
    if code:
        status = (
            "incomplete"
            if code.startswith("incomplete")
            else "unsupported"
            if code.startswith("unsupported")
            else "invalid"
        )
    typer.echo(
        json.dumps(
            {
                "schema_version": "1.0-draft",
                "status": status,
                "issues": [{"code": code, "message": message}] if code else [],
                "result": view,
            },
            allow_nan=False,
        )
    )
    if code:
        raise typer.Exit(1)


@results_app.command("show")
def show(location: Annotated[Path, typer.Argument()]) -> None:
    """Emit the portable JSON view of a verified profile bundle or saved run result."""
    try:
        typer.echo(json.dumps(observations(load_result(location)), indent=2, allow_nan=False))
    except ResultReadError as exc:
        typer.echo(json.dumps({"code": exc.code, "message": str(exc)}), err=True)
        raise typer.Exit(1) from exc


@results_app.command("export")
def export(
    location: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Option("--output")],
    format: Annotated[_ExportFormat, typer.Option("--format")] = _ExportFormat.JSON,
) -> None:
    """Write portable JSON or recorded transfer traces; existing files are never overwritten."""
    try:
        record = load_result(location)
        if format is _ExportFormat.PERFETTO:
            from ..report.perfetto import export_transfer_trace

            export_transfer_trace(record, output)
        else:
            export_result(record, output)
    except (OSError, ResultReadError) as exc:
        typer.echo(json.dumps({"code": "export_failed", "message": str(exc)}), err=True)
        raise typer.Exit(1) from exc
