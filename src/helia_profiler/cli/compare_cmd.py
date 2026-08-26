"""Implementation of the ``hpx compare`` command."""

from __future__ import annotations

import sys
from pathlib import Path


def _cmd_compare(
    *,
    baseline: Path,
    candidate: Path,
    output_dir: Path | None = None,
    profile: Path | None = None,
    validation: bool = False,
    top_layers: int = 10,
) -> None:
    """Compare two completed hpx profile output directories."""
    from ..evaluation import compare_runs, write_compare_artifacts
    from ..console import HpxConsole
    from ..errors import HpxError

    console = HpxConsole()
    try:
        if validation:
            if profile is not None:
                raise HpxError("--profile is not yet supported with --validation")
            if output_dir is None:
                raise HpxError("--output-dir is required with --validation")
            from ..validation.compare import (
                compare_validation_bundles,
                write_validation_compare_artifacts,
            )

            result = compare_validation_bundles(baseline, candidate)
            paths = write_validation_compare_artifacts(result, output_dir)
            print(
                "Validation comparison: "
                f"{result.summary['compared']}/{result.summary['total']} cases compared"
            )
            print(f"JSON report:     {paths[0]}")
            print(f"Markdown report: {paths[1]}")
            return
        comparison_profile = None
        if profile is not None:
            from ..evaluation import ComparisonProfile

            comparison_profile = ComparisonProfile.load(profile)
        result = compare_runs(baseline, candidate, profile=comparison_profile)
        paths = None
        if output_dir is not None:
            paths = write_compare_artifacts(result, output_dir)
        console.print_compare(result, top_layers=top_layers, output_paths=paths)
        if result.verdict is not None and result.verdict.status.value == "fail":
            sys.exit(1)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)
