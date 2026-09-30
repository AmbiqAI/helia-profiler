"""Implementation of the ``hpx power-on`` command."""

from __future__ import annotations

import logging
import sys
import threading

log = logging.getLogger("hpx")


def _cmd_power_on(driver_name: str, *, power_serial: str | None = None) -> None:
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
        except Exception as exc:
            log.warning("Failed to release %s: %s", driver.name, exc)
        else:
            print(f"\n{driver.name} released.")
