# Power Profiling

Capture current, voltage, and energy measurements alongside PMU data using
a Joulescope instrument.

## Prerequisites

- Joulescope JS110 or JS220 connected in series with the EVB
- Power extras installed: `pip install 'helia-profiler[power]'`

## Config

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
  io_voltage: 1.8
  sync_gpio_pin: 10           # firmware toggles this GPIO during inference

output:
  format: csv
  dir: ./results/power_run
  detailed: true
```

## Run

```bash
hpx profile --config hpx_power.yml
```

The profiler will:

1. Run the normal PMU profiling passes
2. Power-cycle the EVB via the Joulescope
3. Capture current/voltage for 30 seconds while inference runs
4. Write both PMU and power results

## Results

### Terminal output

```
  Power:
    avg_current:  12.345 mA
    avg_power:    22.221 mW
    peak_current: 45.678 mA
    energy:       666.630 µJ
```

### summary.json includes power section

```json
{
  "power": {
    "avg_current_a": 0.012345,
    "avg_power_w": 0.022221,
    "peak_current_a": 0.045678,
    "energy_j": 0.00066663
  }
}
```

### detailed/power_summary.csv

With `--detailed`, a dedicated CSV is written:

```csv
metric,value
avg_current_a,0.012345
avg_power_w,0.022221
peak_current_a,0.045678
energy_j,0.00066663
duration_s,30.0
sample_count,300000
```

## Interpreting power results

| Metric | Description |
|---|---|
| `avg_current` | Mean current draw during capture — primary efficiency metric |
| `avg_power` | Mean power (voltage × current) |
| `peak_current` | Maximum instantaneous current — important for power supply sizing |
| `energy` | Total energy consumed during capture window |

!!! tip "Comparing engines"
    Run the same power config with different engines to compare energy
    efficiency. heliaAOT may show lower average current due to smaller
    code size (less flash reads) and tighter memory access patterns.
