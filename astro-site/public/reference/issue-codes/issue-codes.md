# Issue codes

Every machine-readable diagnostic code hpx can emit: 27 run-validity codes and 8 comparability codes, generated from the registry.

## Run-validity issues

| Code | Severity | Description |
| --- | --- | --- |
| `firmware.model_identity_unverifiable` | warning | The firmware's reported model size is not an integer, so the model identity check could not run; no mismatch is not evidence of a match. |
| `firmware.model_mismatch` | error | The model the firmware reports executing is not the model HPX sent; every measurement in the run belongs to an unknown graph. |
| `pmu.counter_overflow` | error | One or more PMU counters overflowed during capture. |
| `pmu.missing` | error | The run has no PMU result. |
| `power.gate_below_minimum` | error | Measured power gate is shorter than the minimum accepted window. |
| `power.gate_duration_mismatch` | warning | Measured power-gate duration does not agree with the expected window. |
| `power.gate_duration_unverifiable` | warning | Power-gate duration cannot be verified because clean inference timing is invalid. |
| `power.gate_edges_missing` | error | GPIO-gated power capture is missing a gate edge. |
| `power.gate_not_lowered` | error | Power firmware did not confirm GATE low. |
| `power.observation_degraded` | warning | Power observation is diagnostic and not valid for efficiency metrics. |
| `power.observation_invalid` | error | Power observation integrity is invalid. |
| `power.observation_missing` | error | Power observation is missing. |
| `power.on_device_count_mismatch` | error | On-device measurement count differs from completed work. |
| `power.on_device_measurement_missing` | error | Internal power mode has no on-device measurement. |
| `power.on_device_overflow` | error (internal) / warning (external) | On-device power monitor reported accumulator overflow. |
| `power.plan_count_mismatch` | error | Power firmware requested count differs from the host plan. |
| `power.terminal_error` | error | Power firmware reported an error. |
| `power.terminal_incomplete` | error | Power firmware completed a different inference count than requested. |
| `power.terminal_missing` | error | Dedicated power firmware did not publish terminal status. |
| `power.window_clock_exceeds_host_time` | warning | Firmware-reported window is longer than the host wall time that contained it. |
| `power.window_clock_frozen` | error (internal) / warning (external) | Power firmware reported zero elapsed time for completed inferences. |
| `power.window_clock_mismatch` | warning | Firmware-reported window duration does not agree with the independently measured window. |
| `power.window_observer_mismatch` | error | The instrument-timed gate and the firmware's own window clock disagree about the same physical window. |
| `profile.clean_window_check_inoperative` | warning | The clean window's partial-stall check could not run; absence of stalls is not evidence of a healthy window. |
| `profile.clean_window_clock_rate_low` | warning | The clean window's cycle counter ran far below its expected rate, measured against an independent clock. |
| `profile.clean_window_frozen` | warning | The clean window completed inferences in zero elapsed time; the clock timing it never advanced. |
| `profile.clean_window_stalled` | warning | The clean-inference window's cycle counter stalled; derived timings understate the true per-inference time. |

## Comparability issues

| Code | Severity | Description |
| --- | --- | --- |
| `identity.model_mismatch` | blocking | Model SHA-256 differs; run-level performance deltas are not comparable. |
| `metric.power_integrity_invalid` | metric_blocking | Power metrics omitted because a power result's integrity is not valid. |
| `result.degraded` | informative | A result is degraded; affected metrics should be interpreted cautiously. |
| `result.incomplete` | blocking | A result bundle is not complete; no comparison is possible. |
| `result.invalid` | blocking | A result is invalid and cannot be compared. |
| `result.invalid_pmu_overflow` | blocking | A legacy result without a manifest has PMU counter overflow. |
| `topology.layer_count_mismatch` | layer_blocking | Per-layer deltas omitted because layer counts differ. |
| `topology.operation_sequence_mismatch` | layer_blocking | Per-layer deltas omitted because operation sequences differ. |

## Parameterized families

### metric.power_<dimension>_mismatch

metric_blocking. Power metrics omitted because a power comparison dimension differs between the runs.

| Code | Dimension |
| --- | --- |
| `metric.power_power_scope_mismatch` | `power_scope` |
| `metric.power_power_mode_mismatch` | `power_mode` |
| `metric.power_power_firmware_mismatch` | `power_firmware` |
| `metric.power_power_monitor_mismatch` | `power_monitor` |
| `metric.power_power_lockstep_mismatch` | `power_lockstep` |
| `metric.power_power_clean_window_probe_mismatch` | `power_clean_window_probe` |
| `metric.power_power_clean_workload_mismatch` | `power_clean_workload` |
| `metric.power_power_firmware_fingerprint_mismatch` | `power_firmware_fingerprint` |

### metric.memory_<dimension>_mismatch

metric_blocking. Per-region memory metrics omitted because a memory comparison dimension differs between the runs.

| Code | Dimension |
| --- | --- |
| `metric.memory_link_family_mismatch` | `link_family` |

### dimension.<dimension>_differs

informative. A comparison dimension differs between the runs; deltas remain comparable but should be read in that light.

| Code | Dimension |
| --- | --- |
| `dimension.hpx_version_differs` | `hpx_version` |
| `dimension.engine_differs` | `engine` |
| `dimension.board_differs` | `board` |
| `dimension.soc_differs` | `soc` |
| `dimension.cpu_clock_differs` | `cpu_clock` |
| `dimension.toolchain_differs` | `toolchain` |
| `dimension.compiler_version_differs` | `compiler_version` |
| `dimension.system_clock_hz_differs` | `system_clock_hz` |
| `dimension.run_summary_schema_version_differs` | `run_summary_schema_version` |
| `dimension.run_metadata_schema_version_differs` | `run_metadata_schema_version` |
| `dimension.transport_differs` | `transport` |
| `dimension.arena_location_differs` | `arena_location` |
| `dimension.weights_location_differs` | `weights_location` |
| `dimension.engine_version_differs` | `engine_version` |
| `dimension.architecture_flags_differs` | `architecture_flags` |
| `dimension.engine_backend_differs` | `engine_backend` |

Generated from the `src/helia_profiler` tree `17bfdf5c68d6c73c3d22f7afb063ac9cd722152f`.
