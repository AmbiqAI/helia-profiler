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
    timed = {operator for operator, _ in table.operator_cycles}
    print(
        f"Wrote calibration: {output} "
        f"({len(table.operators)} operators, {table.price_count} prices; "
        f"{len(timed)} operators timed, {len(table.operator_cycles)} timings)"
    )
