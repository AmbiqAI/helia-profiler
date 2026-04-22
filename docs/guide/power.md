# Power Measurement

heliaPROFILER integrates with [Joulescope](https://www.joulescope.com/) instruments
to capture current, voltage, and power traces alongside PMU data.

## Supported instruments

| Driver | Instrument | Status |
|---|---|---|
| `joulescope` | Auto-detect (JS110 or JS220) | Stable |
| `joulescope-js110` | Joulescope JS110 | Stable |
| `joulescope-js220` | Joulescope JS220 | Stable |
| `ondevice` | On-device measurement | Experimental |

## Setup

Install the power extras:

```bash
pip install 'helia-profiler[power]'
```

### Hardware setup

1. Connect the Joulescope **in series** between the EVB power supply and the
   board's power input
2. Connect the EVB to your host via J-Link USB (for flash and SWO)
3. The Joulescope provides power-cycle capability (used for target reset)

## Usage

### CLI

```bash
hpx profile model.tflite --power --power-duration 30
```

### Config file

```yaml title="hpx.yml"
power:
  enabled: true
  driver: joulescope         # auto-detects JS110 or JS220
  mode: external
  duration_s: 30             # capture window in seconds
  io_voltage: 1.8            # I/O voltage
  sync_gpio_pin: 10          # GPIO toggled during inference
```

## How it works

When power capture is enabled, the profiling pipeline adds an extra step
after PMU capture:

1. **Power-cycle reset** — the Joulescope cuts and restores power to the EVB,
   ensuring a clean reset state
2. **Capture** — the Joulescope records current/voltage for `duration_s` seconds
   while the firmware runs inference
3. **Summarize** — statistics are computed: average current, average power,
   peak current, energy consumption

## Output

Power results appear in multiple places:

### Terminal summary

```
  Power:
    avg_current:  12.345 mA
    avg_power:    22.221 mW
    peak_current: 45.678 mA
    energy:       666.630 µJ
```

### summary.json

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

### detailed/power_summary.csv (with `--detailed`)

```csv
metric,value
avg_current_a,0.012345
avg_power_w,0.022221
peak_current_a,0.045678
energy_j,0.00066663
duration_s,30.0
sample_count,300000
```

## Power config reference

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable power capture |
| `driver` | string | `joulescope` | Driver: `joulescope`, `joulescope-js110`, `joulescope-js220` |
| `mode` | string | `external` | `external` (Joulescope inline) or `internal` (on-device) |
| `duration_s` | int | `30` | Capture duration in seconds |
| `io_voltage` | float | `1.8` | I/O voltage for Joulescope |
| `sync_gpio_pin` | int | `10` | GPIO pin firmware toggles during inference |
