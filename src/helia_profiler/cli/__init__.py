"""hpx CLI — Profile LiteRT models on Ambiq silicon."""

from __future__ import annotations

from .app import app


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``hpx`` console script.

    Invokes the Typer/Click app in standalone mode so usage errors and
    command implementations continue to raise ``SystemExit`` with the same
    exit codes as the previous argparse-based CLI.
    """
    app(args=argv, prog_name="hpx")


__all__ = ["main"]
