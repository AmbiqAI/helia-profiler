# Issue codes

<!-- GENERATED FILE — do not edit by hand.
     Source: src/helia_profiler/results/issues.py
     Regenerate: uv run python tools/gen_issue_code_reference.py -->

Every machine-readable diagnostic code HPX can emit, generated from the
registry in `helia_profiler.results.issues`.

## Run-validity issues

Emitted by run evaluation into `summary.json` (`issues[]`) and
`result_manifest.json`. Severity `error` makes the run **invalid**; `warning`
makes it **degraded**. Two codes are mode-dependent: whether the broken
number is the measurement of record decides fatal-vs-warn.

| Code | Severity | Description |
| --- | --- | --- |
| `pmu.counter_overflow` | `error` | One or more PMU counters overflowed during capture. |
| `pmu.missing` | `error` | The run has no PMU result. |
| `power.gate_duration_mismatch` | `warning` | Measured power-gate duration does not agree with the expected window. |
| `power.gate_duration_unverifiable` | `warning` | Power-gate duration cannot be verified because clean inference timing is invalid. |
| `power.gate_edges_missing` | `error` | GPIO-gated power capture is missing a gate edge. |
| `power.gate_not_lowered` | `error` | Power firmware did not confirm GATE low. |
| `power.observation_degraded` | `warning` | Power observation is diagnostic and not valid for efficiency metrics. |
| `power.observation_invalid` | `error` | Power observation integrity is invalid. |
| `power.observation_missing` | `error` | Power observation is missing. |
| `power.on_device_count_mismatch` | `error` | On-device measurement count differs from completed work. |
| `power.on_device_measurement_missing` | `error` | Internal power mode has no on-device measurement. |
| `power.on_device_overflow` | `error` (internal) / `warning` (external) | On-device power monitor reported accumulator overflow. |
| `power.plan_count_mismatch` | `error` | Power firmware requested count differs from the host plan. |
| `power.terminal_error` | `error` | Power firmware reported an error. |
| `power.terminal_incomplete` | `error` | Power firmware completed a different inference count than requested. |
| `power.terminal_missing` | `error` | Dedicated power firmware did not publish terminal status. |
| `power.window_clock_exceeds_host_time` | `warning` | Firmware-reported window is longer than the host wall time that contained it. |
| `power.window_clock_frozen` | `error` (internal) / `warning` (external) | Power firmware reported zero elapsed time for completed inferences. |
| `power.window_clock_mismatch` | `warning` | Firmware-reported window duration does not agree with the independently measured window. |
| `profile.clean_window_check_inoperative` | `warning` | The clean window's partial-stall check could not run; absence of stalls is not evidence of a healthy window. |
| `profile.clean_window_clock_rate_low` | `warning` | The clean window's cycle counter ran far below its expected rate, measured against an independent clock. |
| `profile.clean_window_frozen` | `warning` | The clean window completed inferences in zero elapsed time; the clock timing it never advanced. |
| `profile.clean_window_stalled` | `warning` | The clean-inference window's cycle counter stalled; derived timings understate the true per-inference time. |

## Comparability issues

Emitted by `hpx compare` when assessing whether two runs may be compared.
Severity governs scope: `blocking` stops the whole comparison,
`layer_blocking` omits per-layer deltas, `metric_blocking` omits one metric
group, and `informative` annotates without blocking anything.

| Code | Severity | Description |
| --- | --- | --- |
| `identity.model_mismatch` | `blocking` | Model SHA-256 differs; run-level performance deltas are not comparable. |
| `metric.power_integrity_invalid` | `metric_blocking` | Power metrics omitted because a power result's integrity is not valid. |
| `result.degraded` | `informative` | A result is degraded; affected metrics should be interpreted cautiously. |
| `result.incomplete` | `blocking` | A result bundle is not complete; no comparison is possible. |
| `result.invalid` | `blocking` | A result is invalid and cannot be compared. |
| `result.invalid_pmu_overflow` | `blocking` | A legacy result without a manifest has PMU counter overflow. |
| `topology.layer_count_mismatch` | `layer_blocking` | Per-layer deltas omitted because layer counts differ. |
| `topology.operation_sequence_mismatch` | `layer_blocking` | Per-layer deltas omitted because operation sequences differ. |

### Parameterized families

These codes embed a comparison dimension name. The `metric.power_…` family
doubles the `power` prefix because the dimension names themselves start with
`power_`; that is the shipped wire format, pinned until a deliberate
wire-format change renames it.

- **`metric.power_<dimension>_mismatch`** (`metric_blocking`) — Power metrics omitted because a power comparison dimension differs between the runs.
  Dimensions: `power_scope`, `power_mode`, `power_firmware`, `power_monitor`, `power_lockstep`, `power_clean_window_probe`
- **`dimension.<dimension>_differs`** (`informative`) — A comparison dimension differs between the runs; deltas remain comparable but should be read in that light.
  Dimensions: `hpx_version`, `engine`, `board`, `soc`, `cpu_clock`, `toolchain`, `compiler_version`, `system_clock_hz`, `run_summary_schema_version`, `run_metadata_schema_version`, `transport`, `arena_location`, `weights_location`
