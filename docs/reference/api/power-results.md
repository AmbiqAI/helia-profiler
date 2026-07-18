# Power Result Types

`ProfileResult.power` remains the compatible aggregate `PowerResult`. The
following fields provide the typed execution and provenance contract for new
code.

Authority rules:

- `power_observation` is authoritative for host-instrument integrity and GPIO
  evidence.
- `power_terminal` is authoritative for firmware completion, count, elapsed
  time, final phase, and error status.
- `on_device_power` is authoritative only for its named firmware-side monitor
  and fixed-N scope.
- `power` is the normalized compatibility aggregate used by existing callers.
  In external mode it reflects the host observation; in internal mode it is
  synthesized from `on_device_power`. Do not compare host and on-device energy
  unless their count, duration, scope, and integrity agree.

## Power observation

::: helia_profiler.PowerObservation

A GPIO-gated observation is valid for per-inference metrics. A free-form
observation is diagnostic and carries degraded integrity.

## Firmware terminal status

::: helia_profiler.PowerTerminalRecord

This record is emitted after the measured window closes. It confirms the fixed
inference count, elapsed time, final phase, error status, and GATE state.

## On-device power summary

::: helia_profiler.OnDevicePowerSummary

This optional payload is reserved for firmware-side monitors such as INA228.
It uses integer base units and identifies the fixed-N measurement window,
count, duration, overflow state, source, and calibration provenance.
