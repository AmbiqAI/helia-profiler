"""Joulescope external power measurement driver (JS110, JS220, and JS320)."""

from __future__ import annotations

import logging
import time
from typing import Any

from ...errors import PowerError
from ..base import PowerMode, PowerResult
from ..sync import NullSyncController, SyncController, SyncWiring
from .capture_gated import capture_gated
from .device import (
    _POWER_CYCLE,
    _STATS_CTRL,
    _STATS_TOPIC,
    _close_device,
    _get_shared_driver,
    _open_device,
    enumerate_devices,
)
from .stats import _process_stats
from .sync import JoulescopeSyncController

log = logging.getLogger("hpx")


class JoulescopeDriver:
    """External power driver for Joulescope JS110, JS220, and JS320.

    Always uses :mod:`pyjoulescope_driver`.  The device family is auto-
    detected from the enumerated device path. JS320 uses the JS220
    publish/subscribe topic protocol.
    """

    def __init__(self, *, serial: str | None = None) -> None:
        self._serial = serial

    #: This driver implements a real ``capture_gated`` (host-side GPI
    #: polling + on-instrument stats integration) — see ``supports_gated_
    #: capture`` on :class:`~helia_profiler.power.base.PowerDriver`.
    supports_gated_capture = True

    @property
    def name(self) -> str:
        return "Joulescope"

    @property
    def mode(self) -> PowerMode:
        return PowerMode.EXTERNAL

    @property
    def num_gpi(self) -> int:
        return 2  # JS110/JS220/JS320 expose 2 general-purpose inputs

    @property
    def has_gpo(self) -> bool:
        return True

    def make_sync_controller(self, wiring: SyncWiring) -> SyncController:
        """Return a 3-wire lock-step controller, or a gate-only fallback."""
        if not wiring.lockstep or not self.has_gpo:
            return NullSyncController()
        return JoulescopeSyncController(serial=self._serial, wiring=wiring)


    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def check_available(self) -> None:
        try:
            import pyjoulescope_driver  # noqa: F401
        except ImportError as exc:
            raise PowerError(
                "pyjoulescope_driver package not installed",
                hint="This should be installed automatically with helia-profiler. "
                "Reinstall with: pip install --force-reinstall helia-profiler",
            ) from exc
        except Exception as exc:
            # numpy/pyjls ABI mismatch surfaces as ValueError during import.
            raise PowerError(
                f"pyjoulescope_driver failed to import: {exc}",
                hint="Likely a numpy ABI mismatch. Try: "
                "pip install --force-reinstall 'pyjoulescope-driver' 'pyjls'.",
            ) from exc

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(
        self,
        *,
        duration_s: float,
        io_voltage: float,
        sampling_frequency: int = 1_000_000,
        **kwargs: Any,
    ) -> PowerResult:
        """Capture aggregate power statistics for *duration_s* seconds.

        Uses the on-instrument 1–2 Hz statistics stream rather than raw
        samples.  This avoids buffering millions of points on the host and
        gives accurate avg/peak/energy summaries for whole-inference timing.
        The firmware is expected to bracket the inference with a GPIO sync
        toggle but the driver does not gate on it; the entire capture window
        is summarised.
        """
        del sampling_frequency  # streaming stats are at instrument-fixed rate

        try:
            from pyjoulescope_driver import time64
        except Exception as exc:
            raise PowerError(
                f"Joulescope capture requires pyjoulescope_driver: {exc}",
                hint="Reinstall pyjoulescope-driver and pyjls in the active environment.",
            ) from exc

        driver, device_path, family = _open_device(self._serial)

        stats_topic = f"{device_path}/{_STATS_TOPIC[family]}"
        ctrl = _STATS_CTRL[family]
        cycle_topic, _off_value, on_value = _POWER_CYCLE[family]

        packets: list[dict[str, Any]] = []

        def _on_stats(_topic: str, value: Any) -> None:
            if isinstance(value, dict):
                packet = dict(value)
                packet["_host_time64"] = time64.now()
                packets.append(packet)

        try:
            # Make sure current is flowing through the shunt (auto range).
            try:
                driver.publish(f"{device_path}/{cycle_topic}", on_value)
            except Exception:
                # Some drivers reject re-setting the same value; ignore.
                pass

            driver.subscribe(stats_topic, "pub", _on_stats)
            try:
                if ctrl is not None:
                    ctrl_topic, ctrl_on, _ctrl_off = ctrl
                    driver.publish(f"{device_path}/{ctrl_topic}", ctrl_on)

                time.sleep(duration_s)
            finally:
                if ctrl is not None:
                    ctrl_topic, _ctrl_on, ctrl_off = ctrl
                    try:
                        driver.publish(f"{device_path}/{ctrl_topic}", ctrl_off)
                    except Exception:
                        pass
                try:
                    driver.unsubscribe(stats_topic, _on_stats)
                except Exception:
                    pass

            if not packets:
                raise PowerError(
                    "No statistics received from Joulescope",
                    hint="Check USB connection and that no other tool is holding the device.",
                )

            samples, summary = _process_stats(packets, duration_s, io_voltage)

            log.info(
                "Joulescope: avg=%.3f mA, peak=%.3f mA, energy=%.6f J (%d stat packets, %s)",
                summary.avg_current_a * 1000,
                summary.peak_current_a * 1000,
                summary.energy_j,
                len(packets),
                family.upper(),
            )

            return PowerResult(
                summary=summary,
                samples=samples,
                metadata={
                    "driver": f"joulescope-{family}",
                    "device": device_path,
                    "io_voltage": io_voltage,
                    "stat_packets": len(packets),
                },
            )

        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope capture failed: {exc}",
                hint="Check USB connection and ensure no other software is using the device.",
            ) from exc
        finally:
            _close_device(driver, device_path)

    # ``capture_gated`` is a long, self-contained method (GPIO polling,
    # full-rate cross-check, on-device stat integration); it lives in
    # ``capture_gated.py`` and is attached below to keep module sizes down.

    # ------------------------------------------------------------------
    # Power cycle
    # ------------------------------------------------------------------

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        """Cut and restore target power via the Joulescope current shunt.

        Uses the family-appropriate current-range topic.  Setting it to the
        "off" value opens the input relay, disconnecting the target from
        its supply; restoring "auto" re-enables it for a clean hardware reset.
        """
        log.info(
            "Power-cycle reset via Joulescope (off=%.1fs, settle=%.1fs)",
            off_time_s,
            settle_time_s,
        )

        driver, device_path, family = _open_device(self._serial)
        topic, off_value, on_value = _POWER_CYCLE[family]

        try:
            driver.publish(f"{device_path}/{topic}", off_value)
            log.info("Target power OFF")
            time.sleep(off_time_s)
            driver.publish(f"{device_path}/{topic}", on_value)
            log.info("Target power ON — waiting %.1fs for boot", settle_time_s)
            time.sleep(settle_time_s)
        except PowerError:
            raise
        except Exception as exc:
            raise PowerError(
                f"Joulescope power cycle failed: {exc}",
                hint="Check USB connection.",
            ) from exc
        finally:
            _close_device(driver, device_path)

        log.info("Power-cycle reset complete")

    # ------------------------------------------------------------------
    # Passthrough (used by EnsureBoardPoweredStage to keep target alive)
    # ------------------------------------------------------------------

    def enable_passthrough(self) -> None:
        """Open the Joulescope and enable current passthrough (close relay)."""
        driver, device_path, family = _open_device(self._serial)
        topic, _off_value, on_value = _POWER_CYCLE[family]
        try:
            driver.publish(f"{device_path}/{topic}", on_value)
        except Exception as exc:
            _close_device(driver, device_path)
            raise PowerError(
                f"Failed to enable Joulescope passthrough: {exc}",
                hint="Check USB connection.",
            ) from exc
        self._pt_device_path = device_path
        log.info("Joulescope passthrough enabled (%s)", family.upper())

    def disable_passthrough(self) -> None:
        """Release the Joulescope opened by :meth:`enable_passthrough`."""
        device_path = getattr(self, "_pt_device_path", None)
        if device_path is not None:
            _close_device(_get_shared_driver(), device_path)
            self._pt_device_path = None
            log.info("Joulescope passthrough released")

    # ------------------------------------------------------------------
    # High-level vendor-neutral hook
    # ------------------------------------------------------------------

    def ensure_target_powered(self, *, required: bool) -> bool:
        """Best-effort or strict passthrough enable, per the decision matrix.

        Owns *all* Joulescope-specific knowledge (enumeration, multi-device
        ambiguity, serial matching, hint strings) so the pipeline stage
        stays driver-agnostic. See :meth:`PowerDriver.ensure_target_powered`
        for the contract.
        """

        def _bail(msg: str, *, hint: str | None = None, level: int = logging.INFO) -> bool:
            if required:
                raise PowerError(msg, hint=hint)
            log.log(level, "%s — skipping Joulescope passthrough.", msg)
            return False

        # --- Check the driver package is importable.
        try:
            self.check_available()
        except PowerError as exc:
            if required:
                raise
            log.debug("Joulescope driver unavailable (%s) — skipping passthrough.", exc)
            return False

        # --- Enumerate devices without opening any.
        try:
            devices = enumerate_devices()
        except PowerError as exc:
            if required:
                raise
            log.debug("Joulescope enumeration failed (%s) — skipping passthrough.", exc)
            return False

        if not devices:
            return _bail(
                "No Joulescope detected",
                hint="Plug in a JS110, JS220, or JS320 and ensure it is powered on.",
                level=logging.DEBUG,
            )

        # --- Pick a device.
        if self._serial is not None:
            wanted = str(self._serial).lstrip("0") or "0"
            matched = [d for d in devices if wanted in d[0]]
            if not matched:
                paths = ", ".join(d[0] for d in devices)
                return _bail(
                    f"Joulescope serial '{self._serial}' not found",
                    hint=f"Connected devices: {paths}. "
                    "Update power.serial / --power-serial to match.",
                )
        elif len(devices) > 1:
            paths = ", ".join(d[0] for d in devices)
            return _bail(
                f"{len(devices)} Joulescopes connected — please disambiguate",
                hint=f"Set power.serial / --power-serial to one of: {paths}",
            )
        # else: exactly one device, no serial needed.

        # --- Enable passthrough; release USB handle immediately (relay is
        # latched in hardware so the board stays powered).
        try:
            self.enable_passthrough()
        except PowerError as exc:
            if required:
                raise
            log.warning("Joulescope passthrough failed: %s — continuing.", exc)
            return False

        try:
            self.disable_passthrough()
        except Exception:  # pragma: no cover - defensive
            log.debug("disable_passthrough after enable failed (ignored)")

        log.info("Joulescope passthrough enabled (relay latched).")
        return True


# Attached here (rather than defined inline) to keep this module under the
# repo's per-file line budget; see ``capture_gated.py`` for the docstring
# and implementation.
JoulescopeDriver.capture_gated = capture_gated
