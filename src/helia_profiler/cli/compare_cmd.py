"""Implementation of the ``hpx compare`` command."""

from __future__ import annotations

import argparse
import sys


def _cmd_compare(args: argparse.Namespace) -> None:
    """Compare two completed hpx profile output directories."""
    from ..evaluation import compare_runs, write_compare_artifacts
    from ..console import HpxConsole
    from ..errors import HpxError

    console = HpxConsole()
    try:
        if getattr(args, "validation", False):
            if getattr(args, "profile", None) is not None:
                raise HpxError("--profile is not yet supported with --validation")
            if args.output_dir is None:
                raise HpxError("--output-dir is required with --validation")
            from ..validation.compare import (
                compare_validation_bundles,
                write_validation_compare_artifacts,
            )

            result = compare_validation_bundles(args.baseline, args.candidate)
            paths = write_validation_compare_artifacts(result, args.output_dir)
            print(
                "Validation comparison: "
                f"{result.summary['compared']}/{result.summary['total']} cases compared"
            )
            print(f"JSON report:     {paths[0]}")
            print(f"Markdown report: {paths[1]}")
            return
        profile = None
        if getattr(args, "profile", None) is not None:
            from ..evaluation import ComparisonProfile

            profile = ComparisonProfile.load(args.profile)
        result = compare_runs(args.baseline, args.candidate, profile=profile)
        paths = None
        if args.output_dir is not None:
            paths = write_compare_artifacts(result, args.output_dir)
        console.print_compare(result, top_layers=args.top_layers, output_paths=paths)
        if result.verdict is not None and result.verdict.status.value == "fail":
            sys.exit(1)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)
