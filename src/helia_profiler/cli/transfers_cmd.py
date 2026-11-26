"""Command for transfer-cost calibration."""

from pathlib import Path
from typing import Annotated

import typer


def transfers_command(
    config: Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o")],
    provider_plan: Annotated[
        Path | None,
        typer.Option(
            "--provider-plan",
            exists=True,
            dir_okay=False,
            help="Emit an independent DMA provider from this plan, retaining the calibration model settings.",
        ),
    ] = None,
) -> None:
    """Measure PSRAM-to-DTCM copy costs on the selected board."""
    from ..config import load_config
    from ..errors import HpxError
    from ..transfers.capture import measure_transfers

    try:
        bundle = measure_transfers(load_config(config, {}), output, provider_plan=provider_plan)
    except (HpxError, OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {output}; capture evidence: {bundle}")


def register(app: typer.Typer) -> None:
    app.command("transfers")(transfers_command)
