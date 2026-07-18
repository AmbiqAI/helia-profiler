"""Parser for post-GATE power-firmware terminal status records."""

from __future__ import annotations

import time
from collections.abc import Iterable
from pathlib import Path

from ..artifacts import (
    OnDevicePowerSummary,
    PowerTerminalEnvelope,
    PowerTerminalRecord,
)
from ..errors import PowerError

POWER_TERMINAL_START = "--- HPX_POWER_TERMINAL_START ---"
POWER_TERMINAL_END = "--- HPX_POWER_TERMINAL_END ---"
POWER_TERMINAL_VERSION = 1

_REQUIRED_KEYS = {
    "HPX_POWER_TERMINAL_VERSION",
    "HPX_POWER_STATUS",
    "HPX_POWER_REQUESTED_COUNT",
    "HPX_POWER_COMPLETED_COUNT",
    "HPX_POWER_ELAPSED_US",
    "HPX_POWER_FINAL_PHASE",
    "HPX_POWER_ERROR_CODE",
    "HPX_POWER_GATE_ASSERTED",
    "HPX_POWER_GATE_LOWERED",
}
_OPTIONAL_KEYS = {
    "HPX_POWER_MEASUREMENT_SOURCE",
    "HPX_POWER_MEASUREMENT_SCOPE",
    "HPX_POWER_ENERGY_NJ",
    "HPX_POWER_MEASUREMENT_DURATION_US",
    "HPX_POWER_MEASUREMENT_COUNT",
    "HPX_POWER_MEASUREMENT_OVERFLOW",
    "HPX_POWER_CHARGE_NC",
    "HPX_POWER_BUS_VOLTAGE_UV",
    "HPX_POWER_SAMPLE_COUNT",
    "HPX_POWER_CALIBRATION_ID",
}


def _parse_int(fields: dict[str, str], key: str) -> int:
    try:
        return int(fields[key], 10)
    except ValueError as exc:
        raise PowerError(f"Malformed power terminal field {key}={fields[key]!r}.") from exc


def _parse_bool(fields: dict[str, str], key: str) -> bool:
    value = fields[key]
    if value == "0":
        return False
    if value == "1":
        return True
    raise PowerError(f"Malformed power terminal boolean {key}={value!r}.")


def parse_power_terminal_envelope(lines: Iterable[str]) -> PowerTerminalEnvelope:
    """Parse exactly one complete versioned post-run envelope."""
    in_record = False
    complete = False
    fields: dict[str, str] = {}

    for raw_line in lines:
        line = raw_line.strip()
        if line == POWER_TERMINAL_START:
            if in_record or complete:
                raise PowerError("Duplicate power terminal start marker.")
            in_record = True
            continue
        if line == POWER_TERMINAL_END:
            if not in_record:
                raise PowerError("Power terminal end marker appeared before start.")
            in_record = False
            complete = True
            continue
        if not in_record or not line:
            continue
        if "=" not in line:
            raise PowerError(f"Malformed power terminal line: {line!r}.")
        key, value = line.split("=", 1)
        if key in fields:
            raise PowerError(f"Duplicate power terminal field: {key}.")
        fields[key] = value

    if in_record or not complete:
        raise PowerError("Power terminal record is incomplete or missing.")
    missing = sorted(_REQUIRED_KEYS - fields.keys())
    if missing:
        raise PowerError(f"Power terminal record is missing fields: {', '.join(missing)}.")
    unknown = sorted(fields.keys() - _REQUIRED_KEYS - _OPTIONAL_KEYS)
    if unknown:
        raise PowerError(f"Power terminal record has unknown fields: {', '.join(unknown)}.")

    version = _parse_int(fields, "HPX_POWER_TERMINAL_VERSION")
    if version != POWER_TERMINAL_VERSION:
        raise PowerError(
            f"Unsupported power terminal version {version}; expected {POWER_TERMINAL_VERSION}."
        )
    status = fields["HPX_POWER_STATUS"]
    if status not in {"ok", "error"}:
        raise PowerError(f"Malformed power terminal status: {status!r}.")

    requested_count = _parse_int(fields, "HPX_POWER_REQUESTED_COUNT")
    completed_count = _parse_int(fields, "HPX_POWER_COMPLETED_COUNT")
    error_code = _parse_int(fields, "HPX_POWER_ERROR_CODE")
    elapsed_raw = fields["HPX_POWER_ELAPSED_US"]
    try:
        elapsed_us = int(elapsed_raw, 10)
    except ValueError as exc:
        raise PowerError(
            f"Malformed power terminal field HPX_POWER_ELAPSED_US={elapsed_raw!r}."
        ) from exc
    if requested_count < 0 or completed_count < 0 or error_code < 0:
        raise PowerError("Power terminal count and error fields must be non-negative.")
    if elapsed_us is not None and elapsed_us < 0:
        raise PowerError("Power terminal elapsed time must be non-negative.")
    if completed_count > requested_count:
        raise PowerError("Power terminal completed count exceeds requested count.")
    if status == "ok" and error_code != 0:
        raise PowerError("Successful power terminal status requires error code 0.")
    if status == "error" and error_code == 0:
        raise PowerError("Error power terminal status requires a nonzero error code.")
    final_phase = fields["HPX_POWER_FINAL_PHASE"]
    if not final_phase:
        raise PowerError("Power terminal final phase must not be empty.")

    terminal = PowerTerminalRecord(
        version=version,
        status=status,
        requested_count=requested_count,
        completed_count=completed_count,
        elapsed_us=elapsed_us,
        final_phase=final_phase,
        error_code=error_code,
        gate_asserted=_parse_bool(fields, "HPX_POWER_GATE_ASSERTED"),
        gate_lowered=_parse_bool(fields, "HPX_POWER_GATE_LOWERED"),
    )
    measurement_source = fields.get("HPX_POWER_MEASUREMENT_SOURCE")
    energy_raw = fields.get("HPX_POWER_ENERGY_NJ")
    if (measurement_source is None) != (energy_raw is None):
        raise PowerError(
            "Power measurement source and energy fields must be provided together."
        )
    measurement = None
    if measurement_source is not None and energy_raw is not None:
        if not measurement_source:
            raise PowerError("Power measurement source must not be empty.")
        required_measurement = {
            "HPX_POWER_MEASUREMENT_SCOPE",
            "HPX_POWER_MEASUREMENT_DURATION_US",
            "HPX_POWER_MEASUREMENT_COUNT",
            "HPX_POWER_MEASUREMENT_OVERFLOW",
        }
        missing_measurement = sorted(required_measurement - fields.keys())
        if missing_measurement:
            raise PowerError(
                "Power measurement payload is missing fields: "
                + ", ".join(missing_measurement)
                + "."
            )
        scope = fields["HPX_POWER_MEASUREMENT_SCOPE"]
        if scope != "fixed_n_inference":
            raise PowerError(f"Unsupported power measurement scope: {scope!r}.")
        try:
            measurement_fields = {
                key: int(fields[key], 10)
                for key in (
                    "HPX_POWER_ENERGY_NJ",
                    "HPX_POWER_CHARGE_NC",
                    "HPX_POWER_BUS_VOLTAGE_UV",
                    "HPX_POWER_SAMPLE_COUNT",
                    "HPX_POWER_MEASUREMENT_DURATION_US",
                    "HPX_POWER_MEASUREMENT_COUNT",
                    "HPX_POWER_MEASUREMENT_OVERFLOW",
                )
                if key in fields
            }
        except ValueError as exc:
            raise PowerError("Malformed integer in power measurement payload.") from exc
        if any(value < 0 for value in measurement_fields.values()):
            raise PowerError("Power measurement fields must be non-negative.")
        overflow_value = measurement_fields["HPX_POWER_MEASUREMENT_OVERFLOW"]
        if overflow_value not in {0, 1}:
            raise PowerError("Power measurement overflow must be 0 or 1.")
        measured_count = measurement_fields["HPX_POWER_MEASUREMENT_COUNT"]
        measured_duration_us = measurement_fields["HPX_POWER_MEASUREMENT_DURATION_US"]
        if measured_count > 0 and measured_duration_us == 0:
            raise PowerError("Power measurement duration must be positive for completed work.")
        if measured_count != terminal.completed_count:
            raise PowerError("Power measurement count does not match terminal completion count.")
        if terminal.elapsed_us is not None and measured_duration_us != terminal.elapsed_us:
            raise PowerError("Power measurement duration does not match terminal elapsed time.")
        measurement = OnDevicePowerSummary(
            source=measurement_source,
            scope="fixed_n_inference",
            energy_nj=measurement_fields["HPX_POWER_ENERGY_NJ"],
            duration_us=measured_duration_us,
            inference_count=measured_count,
            overflow=bool(overflow_value),
            charge_nc=measurement_fields.get("HPX_POWER_CHARGE_NC"),
            bus_voltage_uv=measurement_fields.get("HPX_POWER_BUS_VOLTAGE_UV"),
            sample_count=measurement_fields.get("HPX_POWER_SAMPLE_COUNT"),
            calibration_id=fields.get("HPX_POWER_CALIBRATION_ID"),
        )
    return PowerTerminalEnvelope(terminal=terminal, measurement=measurement)


def parse_power_terminal(lines: Iterable[str]) -> PowerTerminalRecord:
    """Compatibility parser returning only execution status."""
    return parse_power_terminal_envelope(lines).terminal


def collect_power_terminal_envelope_rtt(
    *,
    build_dir: Path,
    toolchain: str,
    device: str,
    jlink_serial: str | None,
    timeout_s: float,
) -> PowerTerminalEnvelope:
    """Attach without reset and collect one terminal envelope over RTT."""
    from .rtt_symbol import resolve_rtt_control_block_address
    from ..target.probe.jlink import attached_session

    address = resolve_rtt_control_block_address(
        build_dir,
        toolchain,
        target_name="hpx_profiler_power",
    )
    if address is None:
        raise PowerError(
            "Could not resolve the power firmware RTT control block address.",
            hint="Inspect the hpx_profiler_power ELF/map and toolchain symbol utilities.",
        )

    deadline = time.monotonic() + timeout_s
    buffer = bytearray()
    last_error: PowerError | None = None
    with attached_session(
        device=device,
        jlink_serial=jlink_serial,
        attach_timeout_s=timeout_s,
    ) as jlink:
        try:
            jlink.rtt_start(block_address=address)
            while time.monotonic() < deadline:
                chunk = bytes(jlink.rtt_read(0, 4096))
                if chunk:
                    buffer.extend(chunk)
                    while True:
                        text = buffer.decode("utf-8", errors="replace")
                        start = text.find(POWER_TERMINAL_START)
                        if start < 0:
                            break
                        end = text.find(POWER_TERMINAL_END, start)
                        if end < 0:
                            break
                        end += len(POWER_TERMINAL_END)
                        try:
                            return parse_power_terminal_envelope(
                                text[start:end].splitlines()
                            )
                        except PowerError as exc:
                            last_error = exc
                            del buffer[: len(text[:end].encode("utf-8"))]
                else:
                    time.sleep(0.01)
        finally:
            try:
                jlink.rtt_stop()
            except Exception:
                pass

    text = buffer.decode("utf-8", errors="replace")
    if text:
        try:
            return parse_power_terminal_envelope(text.splitlines())
        except PowerError as exc:
            last_error = exc
    if last_error is not None:
        raise PowerError(
            f"No valid power terminal record received within {timeout_s:.1f}s: {last_error}"
        ) from last_error
    raise PowerError(
        f"No power terminal record received within {timeout_s:.1f}s.",
        hint="Confirm the power firmware reached post-GATE diagnostics and RTT is linked.",
    )


def collect_power_terminal_rtt(
    *,
    build_dir: Path,
    toolchain: str,
    device: str,
    jlink_serial: str | None,
    timeout_s: float,
) -> PowerTerminalRecord:
    """Compatibility collector returning only execution status."""
    return collect_power_terminal_envelope_rtt(
        build_dir=build_dir,
        toolchain=toolchain,
        device=device,
        jlink_serial=jlink_serial,
        timeout_s=timeout_s,
    ).terminal


__all__ = [
    "POWER_TERMINAL_END",
    "POWER_TERMINAL_START",
    "POWER_TERMINAL_VERSION",
    "collect_power_terminal_envelope_rtt",
    "collect_power_terminal_rtt",
    "parse_power_terminal",
    "parse_power_terminal_envelope",
]
