"""Offline result commands; no operational CLI registration is needed to read evidence."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from ..api import export_result, inspect_result, load_result, observations
from ..records import ResultReadError

app = typer.Typer(
    help="Inspect and export verified ordinary profile bundles.", add_completion=False
)


@app.command("inspect")
def inspect(location: Path) -> None:
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


@app.command("show")
def show(location: Path) -> None:
    """Emit the portable JSON view of a verified native bundle."""
    try:
        typer.echo(json.dumps(observations(load_result(location)), indent=2, allow_nan=False))
    except ResultReadError as exc:
        typer.echo(json.dumps({"code": exc.code, "message": str(exc)}), err=True)
        raise typer.Exit(1) from exc


@app.command("export")
def export(location: Path, output: Path = typer.Option(..., "--output")) -> None:
    """Write a new portable JSON file; existing evidence is never overwritten."""
    try:
        export_result(load_result(location), output)
    except (OSError, ResultReadError) as exc:
        typer.echo(json.dumps({"code": "export_failed", "message": str(exc)}), err=True)
        raise typer.Exit(1) from exc
