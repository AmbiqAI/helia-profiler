"""Execute the profile command and apply its presentation and exit policy."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..results import ResultValidity
from .options import _build_cli_overrides


def _cmd_profile(*, config: Path | None = None, verbose: int = 0, **params: Any) -> None:
    from ..config import load_config
    from ..console import HpxConsole
    from ..errors import HpxError

    cli = _build_cli_overrides(verbose=verbose, **params)

    # Use the CLI's own --verbose flag for error reporting during config load,
    # since a ConfigError means we never get a resolved ProfileConfig.verbose.
    console = HpxConsole(verbose)

    try:
        resolved_config = load_config(config, cli)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    console = HpxConsole(resolved_config.verbose)

    from ..api import _execute_profile
    from ..console.lifecycle import profile_presentation

    try:
        with profile_presentation(console):
            result = _execute_profile(resolved_config, observer=console)
    except KeyboardInterrupt:
        console.print_interrupted()
        sys.exit(130)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    # Apply the opt-in exit policy only after reports and presentation complete.
    if (
        result.evaluation is not None
        and result.evaluation.validity is ResultValidity.INVALID
        and resolved_config.output.fail_on_invalid
    ):
        sys.exit(3)
