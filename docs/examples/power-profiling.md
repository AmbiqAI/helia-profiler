# Power Profiling

**Goal:** capture current, voltage, and energy alongside PMU data using a
Joulescope, in addition to the normal cycle-count profile.

## Setup

A Joulescope JS110 or JS220 wired in series with the EVB (`pyjoulescope_driver`
ships as a core dependency — see [Installation](../getting-started/install.md)
for udev/USB setup).

```yaml title="hpx_power.yml"
model:
  path: my_model.tflite
  arena_size: 131072

engine:
  type: helia-rt
  config:
    variant: release-with-logs
    dist_path: path/to/helia_rt_v1_7_0

target:
  board: apollo510_evb

profiling:
  pmu_counters:
    cpu: all
  per_layer: true
  iterations: 5
  warmup: 2

power:
  enabled: true
  driver: joulescope          # auto-detects JS110 or JS220
  mode: external
  duration_s: 30

output:
  format: csv
  dir: ./results/power_run
  detailed: true
```

## Run

```bash
hpx profile --config hpx_power.yml
```

The profiler runs the normal PMU passes, then power-cycles the EVB and
captures current/voltage for `duration_s` seconds during a clean,
uninstrumented inference window.

## What you get

```
  Power:
    avg_current:  12.345 mA
    avg_power:    22.221 mW
    peak_current: 45.678 mA
    energy:       666.630 µJ
```

`summary.json` gets a matching `power` section (`avg_current_a`,
`avg_power_w`, `peak_current_a`, `energy_j`), and `--detailed` adds
`detailed/power_summary.csv`.

!!! tip "Comparing engines"
    Run the same power config with different engines to compare energy
    efficiency — see [Engine Comparison](engine-comparison.md).

## Where to go deeper

- [Power Measurement](../guide/power.md) — wiring, sync GPIO, dedicated
  power firmware, window sizing, and troubleshooting a bad capture.
- [Output & Results](../guide/output.md) — every result file and field.
