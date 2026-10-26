"""Scoped terminal presentation and logging for CLI profiling."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import HpxConsole


@contextmanager
def profile_presentation(console: HpxConsole) -> Iterator[None]:
    """Own logging and presentation cleanup for one CLI profile invocation."""
    with _cli_logging(console.verbosity):
        failed = False
        try:
            console.print_banner()
            yield
        except BaseException:
            failed = True
            raise
        finally:
            try:
                console._stop_spinner()
            except BaseException:
                # Cleanup must not replace the failure that ended the run.
                if not failed:
                    raise


@contextmanager
def _cli_logging(verbosity: int) -> Iterator[None]:
    """Temporarily configure the ``hpx`` logger for one CLI-owned run."""
    from rich.logging import RichHandler

    from . import status_console

    level = logging.WARNING
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO

    logger = logging.getLogger("hpx")
    previous_handlers = list(logger.handlers)
    previous_level = logger.level
    if not previous_handlers:
        handler = RichHandler(
            console=status_console,
            show_time=False,
            show_path=False,
            markup=False,
            keywords=[],
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    try:
        yield
    finally:
        logger.handlers[:] = previous_handlers
        logger.setLevel(previous_level)
