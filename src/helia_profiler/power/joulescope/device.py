"""Shared ``pyjoulescope_driver`` handle and device open/close/enumerate helpers.

Backed by the ``pyjoulescope_driver`` package, which supports both Joulescope
families (JS110, JS220) via the same publish/subscribe API. The device family
is detected from the device path (``u/js110/...`` vs ``u/js220/...``) and the
small number of family-specific topic names is dispatched by the tables
below.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ...errors import PowerError

log = logging.getLogger("hpx")

_OPEN_RETRY_TIMEOUT_S = 3.0
_OPEN_RETRY_INTERVAL_S = 0.25


# ---------------------------------------------------------------------------
# Family-specific topic / value tables
# ---------------------------------------------------------------------------

#: Stats topic per family.  JS110 has an always-on instrument-side stats
#: stream (``s/sstats/value``) that does not need to be enabled; JS220 uses
#: the host-side ``s/stats/value`` stream gated by ``s/stats/ctrl``.
_STATS_TOPIC = {
    "js110": "s/sstats/value",
    "js220": "s/stats/value",
}

#: ``(topic, on_value, off_value)`` triple for enabling stats streaming.
#: For JS110 the stream is on by default and the entry is ``None`` to mean
#: "no-op".  For JS220 we toggle ``s/stats/ctrl``.
_STATS_CTRL = {
    "js110": None,
    "js220": ("s/stats/ctrl", 1, 0),
}

#: ``(topic, off_value, on_value)`` for cutting / restoring target power.
#: JS110: ``s/i/range/select`` (0 = off, 128 = auto).
#: JS220: ``s/i/range/mode`` ('off' / 'auto').
_POWER_CYCLE = {
    "js110": ("s/i/range/select", 0, 128),
    "js220": ("s/i/range/mode", "off", "auto"),
}

#: Native full-rate sampling frequency per family (Hz).  Used to convert a
#: desired host-side stats rate into the device ``s/stats/scnt`` sample count
#: (``scnt = native_rate / stats_rate_hz``).
_NATIVE_SAMPLE_RATE = {
    "js110": 2_000_000,
    "js220": 1_000_000,
}

#: Host-side configurable statistics topics ``(scnt, ctrl, value)``.  Unlike the
#: fixed-2 Hz JS110 sensor-side ``s/sstats`` used by :meth:`capture`, this stream
#: is rate-settable and each packet carries the instrument's full-rate charge and
#: energy integrals — exactly what the gated window needs, with KB (not MB) of
#: data and no raw sample streaming.
_HOST_STATS = {
    "js110": ("s/stats/scnt", "s/stats/ctrl", "s/stats/value"),
    "js220": ("s/stats/scnt", "s/stats/ctrl", "s/stats/value"),
}


def _family_from_path(device_path: str) -> str:
    if "js110" in device_path.lower():
        return "js110"
    if "js220" in device_path.lower():
        return "js220"
    raise PowerError(
        f"Unsupported Joulescope device path: {device_path}",
        hint="This driver supports JS110 and JS220 only.",
    )


# ---------------------------------------------------------------------------
# Process-wide pyjoulescope_driver.Driver singleton
#
# ``pyjoulescope_driver`` is implemented in C/Cython and is designed for a
# single long-lived ``Driver`` instance per process.  Constructing and
# ``finalize()``-ing it repeatedly (e.g. once per capture, once per power-
# cycle) leads to USB-state confusion and, in practice, hard segfaults on
# macOS.  We keep one shared instance, opened lazily on first use, and
# released only at interpreter shutdown via ``atexit``.
# ---------------------------------------------------------------------------

_shared_driver: Any = None

#: Per-device-path open refcount.  Several callers can hold a logical "open"
#: on the same device path at once (e.g. the sync controller opens it for
#: GPI/GPO access while ``capture_gated`` independently opens/closes it for
#: the measured window). ``drv.open``/``drv.close`` on the underlying
#: ``pyjoulescope_driver`` handle are idempotent, but the *path* must only be
#: closed once nobody holds it, or a still-active caller's handle would be
#: torn down. ``_open_device`` increments this on every successful open;
#: ``_close_device`` decrements it and only calls ``drv.close`` once the
#: count reaches zero.
_open_refcounts: dict[str, int] = {}


def _get_shared_driver() -> Any:
    global _shared_driver
    if _shared_driver is not None:
        return _shared_driver
    import atexit

    try:
        import pyjoulescope_driver as jsdrv
    except ImportError as exc:
        raise PowerError(
            "pyjoulescope_driver package not installed",
            hint="pip install pyjoulescope_driver",
        ) from exc

    try:
        drv = jsdrv.Driver()
    except Exception as exc:
        raise PowerError(
            f"Failed to initialise pyjoulescope_driver: {exc}",
            hint="Ensure the Joulescope is connected via USB.",
        ) from exc

    def _finalize() -> None:
        try:
            drv.finalize()
        except Exception:
            pass

    atexit.register(_finalize)
    _shared_driver = drv
    return drv


def _is_device_busy_error(message: str) -> bool:
    message = message.lower()
    return (
        "claim" in message
        or "libusb" in message
        or "-3" in message
        or "access" in message
        or "in_use" in message
        or "busy" in message
    )


def _open_device(serial: str | None) -> tuple[Any, str, str]:
    """Open the selected device on the shared driver, returning ``(driver, path, family)``.

    The caller must release the device with :func:`_close_device` (or ignore
    that step if the device handle should remain open across calls — e.g.
    passthrough).
    """
    drv = _get_shared_driver()

    try:
        paths = list(drv.device_paths())
    except Exception as exc:
        raise PowerError(
            f"Joulescope enumeration failed: {exc}",
            hint="Check USB connection.",
        ) from exc

    if not paths:
        raise PowerError(
            "No Joulescope detected",
            hint="Plug in a Joulescope (JS110 or JS220) and ensure it is powered on.",
        )

    if serial is not None:
        wanted = str(serial).lstrip("0") or "0"
        matched = [p for p in paths if wanted in p]
        if not matched:
            raise PowerError(
                f"Joulescope serial '{serial}' not found among connected devices",
                hint=f"Connected devices: {', '.join(paths)}. "
                "Update power.serial / --js-serial to match.",
            )
        device_path = matched[0]
    elif len(paths) > 1:
        raise PowerError(
            f"{len(paths)} Joulescopes connected — please disambiguate",
            hint=f"Set power.serial / --js-serial to one of: {', '.join(paths)}",
        )
    else:
        device_path = paths[0]

    family = _family_from_path(device_path)

    deadline = time.monotonic() + _OPEN_RETRY_TIMEOUT_S
    while True:
        try:
            drv.open(device_path)
            break
        except Exception as exc:
            msg = str(exc).lower()
            if _is_device_busy_error(msg):
                if time.monotonic() < deadline:
                    log.warning(
                        "Joulescope %s busy during open; retrying in %.2fs",
                        device_path,
                        _OPEN_RETRY_INTERVAL_S,
                    )
                    time.sleep(_OPEN_RETRY_INTERVAL_S)
                    continue
                raise PowerError(
                    f"Joulescope {device_path} is already in use by another process",
                    hint=(
                        "Close the Joulescope desktop app or any other process "
                        "holding the device, then retry. On macOS you can also "
                        "run 'pkill -f jsdrv' to release stuck handles."
                    ),
                ) from exc
            # Idempotent re-open is OK; treat "already open" as success.
            if "already" in msg or "open" in msg:
                log.debug("Joulescope %s already open — reusing handle", device_path)
                break
            raise PowerError(
                f"Failed to open Joulescope {device_path}: {exc}",
                hint="Check USB connection and re-plug the device if needed.",
            ) from exc

    log.info("Joulescope opened: %s (%s)", device_path, family.upper())
    _open_refcounts[device_path] = _open_refcounts.get(device_path, 0) + 1
    return drv, device_path, family


def _close_device(drv: Any, device_path: str) -> None:
    """Release one logical open on *device_path*.

    Only calls ``drv.close`` once every :func:`_open_device` caller for this
    path has released it, so one caller finishing early (e.g. a sync
    controller during an active gated capture) never tears down another
    caller's still-active handle.
    """
    remaining = _open_refcounts.get(device_path, 0) - 1
    if remaining > 0:
        _open_refcounts[device_path] = remaining
        return
    _open_refcounts.pop(device_path, None)
    try:
        drv.close(device_path)
    except Exception:
        pass


def enumerate_devices() -> list[tuple[str, str]]:
    """Return ``[(device_path, family), ...]`` for connected Joulescopes.

    Lightweight discovery: opens the shared :mod:`pyjoulescope_driver`
    handle but does **not** open any individual device. Raises
    :class:`PowerError` if the driver package is missing or the underlying
    enumeration call fails (e.g. libusb permissions).
    """
    drv = _get_shared_driver()
    try:
        paths = list(drv.device_paths())
    except Exception as exc:
        raise PowerError(
            f"Joulescope enumeration failed: {exc}",
            hint="Check USB connection.",
        ) from exc
    out: list[tuple[str, str]] = []
    for p in paths:
        try:
            out.append((p, _family_from_path(p)))
        except PowerError:
            # Unknown family — skip silently rather than fail enumeration.
            log.debug("Skipping unknown Joulescope device path: %s", p)
    return out


def _extract_scalar(node: Any, default: float = 0.0) -> float:
    """Return a float from an stats sub-node.

    The ``pyjoulescope_driver`` stats packet wraps numeric values in a
    ``{'value': <number>, 'units': <str>}`` dict.  Older packets (and the
    JS220 host-side stream variant) use bare floats.  Handle both.
    """
    if isinstance(node, dict):
        v = node.get("value", default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default
    try:
        return float(node)
    except (TypeError, ValueError):
        return default
