"""Implementation of the ``hpx calibrate`` command."""

from __future__ import annotations

import sys
from pathlib import Path


def _cmd_calibrate(
    *,
    bundles: list[Path],
    output: Path,
    merge: bool = False,
    tolerance: float | None = None,
) -> None:
    from ..calibration import DEFAULT_TOLERANCE, calibrate
    from ..console import HpxConsole
    from ..errors import HpxError

    console = HpxConsole()
    try:
        table = calibrate(
            bundles,
            output=output,
            merge=merge,
            tolerance=DEFAULT_TOLERANCE if tolerance is None else tolerance,
        )
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)
    except OSError as exc:
        console.print_error(HpxError(f"Could not write {output}: {exc}"))
        sys.exit(1)
    print(
        f"Wrote calibration: {output} "
        f"({table.operator_count} operators, {len(table.samples)} samples)"
    )
