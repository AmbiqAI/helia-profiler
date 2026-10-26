"""hpx CLI — Profile LiteRT and ExecuTorch models on Ambiq silicon."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> None:
    """Entry point for the ``hpx`` console script.

    Invokes the Typer/Click app in standalone mode; usage errors and
    command exit policies are reported through ``SystemExit``.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["results"]:
        from .results_cmd import app as results_app

        results_app(args=arguments[1:], prog_name="hpx results")
        return
    from .app import app

    globals()["app"] = app
    app(args=arguments, prog_name="hpx")


def __getattr__(name: str):
    if name == "app":
        from .app import app

        globals()["app"] = app
        return app
    raise AttributeError(name)


__all__ = ["main"]
