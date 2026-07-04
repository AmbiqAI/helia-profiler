# Power Measurement

heliaPROFILER can capture current, voltage, and energy alongside PMU data.
Two driver families are supported:

- **External Joulescope** — the [Joulescope](https://www.joulescope.com/)
  JS110 or JS220 wired in series between the EVB power supply and the
  board. Best accuracy and dynamic range.
- **On-device** — the SoC's internal power management unit reports its
  own current draw. Lower fidelity, no extra hardware.

Most users want external Joulescope.

## Driver overview

| `power.driver` | Instrument / source | Status |
|---|---|---|
| `joulescope` *(default)* | Auto-detect JS110 or JS220 | Stable |
| `joulescope-js110` | JS110 only | Stable |
| `joulescope-js220` | JS220 only | Stable |
| `ondevice` | Apollo SoC internal PMU | Experimental |

```yaml
power:
  enabled: true
  driver: joulescope
```

or:

```bash
hpx profile model.tflite --power
```

## Joulescope JS220 vs JS110

| Aspect | JS110 | JS220 |
|---|---|---|
| Current range | nA → 3 A (auto-ranging) | nA → 10 A (auto-ranging) |
| Sample rate | 250 kSPS | 1 MSPS (default 2 MSPS available) |
| Voltage range | 0–15 V | 0–15 V |
| Power-cycle control | Yes | Yes |
| Best for | TinyML / always-on workloads | Larger transients, faster signals |

Both expose the same Python API and the same `JoulescopeDriver`
interface inside heliaPROFILER. Auto-detect (`joulescope`) is fine
unless you have both connected and want to pin one explicitly.

## Hardware setup

```text
                        +--------- USB to host (Joulescope) -----+
                        |                                         |
   Power supply --[+]---+--> Joulescope IN+   Joulescope OUT+ ---+--> EVB VBAT
                  [-]------> Joulescope IN-   Joulescope OUT- ---+--> EVB GND
                                                                  |
                  +-- USB to host (J-Link) -----> EVB J-Link ------+
                  |
                  +-- (optional) USB to host (target USB) -------> EVB USB
```

Steps:

1. Disconnect any USB power source from the EVB. The Joulescope must be
   the **only** source of board power for the current numbers to mean
   anything.
2. Wire Joulescope `IN±` to your bench supply or wall adapter, and
   Joulescope `OUT±` to the EVB's `VBAT`/`GND`. Match polarity.
3. Connect J-Link USB for flashing.
4. Connect the Joulescope to the host via its own USB.
5. Run `hpx profile --power`. The profiler power-cycles the EVB through
   the Joulescope before each capture, so reset is automatic.

!!! warning "Don't power the EVB from two sources"
    If a USB cable, J-Link debug USB, or coin cell provides power in
    parallel with the Joulescope, current readings will be wrong (or
    sometimes negative). Pull all other power sources during external
    captures.

## Synchronization

The firmware toggles the board's default sync GPIO pin at the start and end
of the inference window. On `apollo510_evb` and `apollo510b_evb` the built-in
default is GPIO 29; most other current built-in EVBs still use GPIO 10.
The Joulescope captures this signal on its
GPI input and the host uses the edges to bracket the measurement
window — only current drawn **during inference** is averaged.

Configure the pin if your wiring differs:

```yaml
power:
  sync_gpio_pin: 7
```

Wire EVB `GPIO N` → Joulescope `GPI 0` (or whichever GPI you use).

## On-device measurement

When external instrumentation isn't available, the on-device driver
reads the SoC's internal current monitors. Accuracy is much lower
(a few mA resolution), but it works without any extra hardware.

```yaml
power:
  enabled: true
  driver: ondevice
  mode: internal
```

This mode is **experimental** — values are useful for relative
comparisons across two runs on the same board, but not for absolute
power numbers.

## Output

Power results land in three places:

### Terminal summary

```text
  Power:
    avg_current  → 1.00× (relative to baseline run)
    avg_power    → 1.05×
    peak_current → 0.92×
    energy       → 0.97×
```

The CLI prints absolute values; this doc shows relatives only because
absolute mA numbers depend heavily on board, supply voltage, and
ambient conditions.

### `summary.json`

```json
{
  "power": {
    "avg_current_a": ...,
    "avg_power_w": ...,
    "peak_current_a": ...,
    "energy_j": ...,
    "duration_s": ...
  }
}
```

### `detailed/power_summary.csv` (with `--detailed`)

A flat CSV of all power scalar metrics for spreadsheet ingest.

## TOPS / TOPS-per-Watt

When power is enabled and the engine is heliaAOT, `summary.json` also
includes `model_analysis.tops` and `model_analysis.tops_per_watt`. These
combine the AOT-derived MAC count with measured energy to give a
per-inference energy efficiency figure useful for comparing models or
quantization schemes on the same hardware.

## Power config reference

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable power capture |
| `driver` | string | `joulescope` | `joulescope`, `joulescope-js110`, `joulescope-js220`, `ondevice` |
| `mode` | string | `external` | `external` (Joulescope inline) or `internal` (on-device) |
| `duration_s` | int | `30` | Capture window length |
| `io_voltage` | float | `1.8` | I/O rail voltage hint for the Joulescope |
| `sync_gpio_pin` | int | board default (`29` on `apollo510_evb` / `apollo510b_evb`) | GPIO pin the firmware toggles around inference |
| `sync_input_index` | int | `0` | Joulescope digital INPUT channel wired to the sync GPIO (distinct from `sync_gpio_pin`) |
| `stats_rate_hz` | int | `1000` | Host stats packet rate for gated capture; the device integrates charge/energy at full rate and reports per-packet integrals plus a spike-robust current/power distribution |

## Troubleshooting

??? failure "`joulescope: device not found`"
    Joulescope USB driver not installed, or device claimed by another
    process. On Linux check udev rules. Install power extras:
    `pip install 'helia-profiler[power]'`.

??? failure "Current reads negative or implausibly high"
    Another power source is also feeding the EVB. Disconnect target USB,
    debug USB power, or coin cell during the capture window.

??? failure "Average current ≈ peak current — no inference activity"
    The sync GPIO is wrong (pin number, wiring, or polarity). Check
    `power.sync_gpio_pin` and the GPI input on the Joulescope.

??? failure "TOPS-per-Watt missing from summary"
    Only emitted for heliaAOT runs with power enabled. heliaRT/TFLM
    don't expose the MAC count needed for the TOPS calculation.
