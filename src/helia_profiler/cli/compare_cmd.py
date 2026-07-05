"""Implementation of the ``hpx compare`` command."""

from __future__ import annotations

import argparse
import sys


def _cmd_compare(args: argparse.Namespace) -> None:
    """Compare two completed hpx profile output directories."""
    from ..compare import compare_runs, write_compare_artifacts
    from ..console import HpxConsole
    from ..errors import HpxError

    console = HpxConsole()
    try:
        result = compare_runs(args.baseline, args.candidate)
        paths = None
        if args.output_dir is not None:
            paths = write_compare_artifacts(result, args.output_dir)
        console.print_compare(result, top_layers=args.top_layers, output_paths=paths)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)
