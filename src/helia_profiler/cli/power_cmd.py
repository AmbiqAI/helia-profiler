"""Implementation of the ``hpx power-on`` command."""

from __future__ import annotations

import sys
import threading


def _cmd_power_on(driver_name: str, *, power_serial: str | None = None) -> None:
    """Enable Joulescope current passthrough and hold open until Ctrl-C."""
    from ..power import get_driver
    from ..errors import PowerError

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
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            driver.disable_passthrough()
        except Exception:
            pass
        print("\nJoulescope released.")
