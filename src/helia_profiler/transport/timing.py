"""Centralised timing constants for capture transports.

This module is the **single place** for the few unavoidable blind delays in
the capture path — the windows where the target is doing something the host
cannot observe (secure-bootloader bring-up, USB re-enumeration).  Everything
else should use bounded, signal-driven polling (see ``readiness.py``) rather
than a fixed ``time.sleep()``.

Keeping these here means new fixed delays are discouraged and the existing
ones stay discoverable and tunable in one spot instead of accreting as magic
numbers scattered across the readers.
"""

from __future__ import annotations

#: Post-reset settle window for the Apollo secure bootloader (SBL) before the
#: host attempts its first J-Link attach.  The SBL bring-up is not observable
#: from the host, so a small floor is used; the host then *polls* for attach
#: readiness (see ``readiness.open_jlink_with_retry``) rather than assuming the
#: target is ready after this delay.
SBL_SETTLE_S = 0.2

#: Floor delay after target reset before scanning for a re-enumerated USB CDC
#: device.  The old TinyUSB device takes a moment to drop off the host USB bus;
#: this floor avoids racing the host enumerator.  After the floor we *poll* for
#: the new device with a deadline rather than sleeping the full window.
USB_REENUM_FLOOR_S = 0.5

#: Default cadence for host-side readiness polling loops (J-Link attach,
#: device re-enumeration).  Small enough to feel responsive, large enough to
#: avoid hammering the probe / USB subsystem.
READINESS_POLL_INTERVAL_S = 0.1
