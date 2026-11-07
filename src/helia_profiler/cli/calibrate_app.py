"""Typer wiring for ``hpx calibrate``.

Kept in its own module, like the inspection cluster, so ``cli/app.py`` stays
under the project's per-module line ceiling. The command function is a thin
adapter: it collects typed CLI parameters and forwards them to
``calibrate_cmd._cmd_calibrate``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ..calibration import DEFAULT_TOLERANCE


def register(app: typer.Typer) -> None:
    """Attach the calibrate command to *app*."""
    app.command(
        "calibrate",
        help="Write a heliaOptimus calibration file from helia-aot result bundles",
        epilog=(
            "Examples:\n\n"
            "  hpx calibrate results/kws-auto results/kws-mve --output kws-calibration.json\n\n"
            "  hpx calibrate results/kws-int16 --output kws-calibration.json --merge\n\n"
            "Each bundle contributes the cycles of every operator its plan lists with "
            "knobs, as operators[<id>][<knob>][<value>]; an operator without knobs has "
            "no slot in the file and is left out. Every bundle must profile the same "
            "model under the same recorded conditions and capture setup (the blocking "
            "comparison dimensions and the kernel build), and carry cycles the "
            "profiler did not flag: an invalid run counts only when the issue registry "
            "confines every error to power metrics, and a run whose cycle counter was "
            "flagged is refused whatever its verdict. Two sources pricing one value "
            "under the same other knobs must agree within --tolerance; anything else "
            "is an error. The plan's fixed choices and kernel entry are recorded with "
            "each price, never compared. --merge keeps every price the file holds. "
            "heliaOptimus matches the file when the planner passes the same "
            "conditions, including application_load=none and, with PSRAM, "
            "external_memory_clock."
        ),
    )(calibrate_command)


def calibrate_command(
    bundles: Annotated[
        list[Path],
        typer.Argument(
            help="helia-aot result directories of one model under one set of conditions"
        ),
    ],
    output: Annotated[
        Path,
        typer.Option("--output", "-o", help="Calibration file to write (schema version 1)"),
    ],
    merge: Annotated[
        bool,
        typer.Option(
            "--merge",
            help="Read the existing --output file first and hold its prices to the same rules",
        ),
    ] = False,
    tolerance: Annotated[
        float,
        typer.Option(
            "--tolerance",
            help=(
                "Largest relative disagreement two prices of one value may have, as a "
                "fraction (default: 0.02, two percent)"
            ),
        ),
    ] = DEFAULT_TOLERANCE,
) -> None:
    """Write a heliaOptimus calibration file from helia-aot result bundles."""
    from .calibrate_cmd import _cmd_calibrate

    _cmd_calibrate(bundles=bundles, output=output, merge=merge, tolerance=tolerance)
