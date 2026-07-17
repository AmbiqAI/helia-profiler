"""Implementation of the ``hpx power-on`` command."""

from __future__ import annotations

import argparse
import sys


def _cmd_power_on(args: argparse.Namespace) -> None:
    """Enable Joulescope current passthrough and hold open until Ctrl-C."""
    from ..power import get_driver
    from ..errors import PowerError

    driver_name = args.driver
    power_serial = getattr(args, "power_serial", None)

    try:
        driver = get_driver(driver_name, serial=power_serial)
    except PowerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  Hint: {exc.hint}", file=sys.stderr)
        sys.exit(1)

    print(f"Enabling current passthrough via {driver.name}...")

    try:
        driver.enable_passthrough()
    except PowerError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if exc.hint:
            print(f"  Hint: {exc.hint}", file=sys.stderr)
        sys.exit(1)

    print("Board powered — press Ctrl-C to release.")
    try:
        import signal

        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            driver.disable_passthrough()
        except Exception:
            pass
        print("\nJoulescope released.")
