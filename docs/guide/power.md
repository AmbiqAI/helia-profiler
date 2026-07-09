# Power Measurement

heliaPROFILER can capture current, voltage, and energy alongside PMU data,
using a **GPIO-gated clean window**: the firmware runs a dedicated,
uninstrumented inference loop and asserts a sync GPIO high for exactly that
window, while a [Joulescope](https://www.joulescope.com/) integrates
charge/energy on-device and streams it to the host. This page walks through
wiring, the minimal config to get a first reading, and every knob you're
likely to need afterward — from simplest to most advanced.

## What you need

- **A Joulescope JS110 or JS220**, wired in series between your bench supply
  (or wall adapter) and the EVB's power input. `pyjoulescope_driver` ships as
  a core dependency of `helia-profiler` — no extra install.
- **One GPIO wire** from the board to the Joulescope's digital input
  `INPUT0` — this is the minimum wiring for a valid capture (see
  [Wiring reference](#wiring-reference)).
- **A J-Link probe** for flashing, connected as usual.
- Optionally, two more GPIO wires for the [lock-step handshake](#lock-step-3-wire-handshake)
  (recommended/auto-enabled on Apollo5-family boards).

!!! warning "Don't power the EVB from two sources"
    The Joulescope must be the board's only power source while capturing.
    If a target USB cable, J-Link debug USB, or coin cell also feeds power,
    current readings will be wrong (sometimes negative). Disconnect every
    other supply during a capture.

### Bench power wiring

The Joulescope sits *in series* between your bench supply and the EVB's
power input — separate from the GPIO gate wiring described later on this
page:

```text
                        +--------- USB to host (Joulescope) -----+
                        |                                         |
   Power supply --[+]---+--> Joulescope IN+   Joulescope OUT+ ---+--> EVB VBAT
                  [-]------> Joulescope IN-   Joulescope OUT- ---+--> EVB GND
                                                                  |
                  +-- USB to host (J-Link) -----> EVB J-Link ------+
```

1. Disconnect any USB power source from the EVB.
2. Wire Joulescope `IN±` to your bench supply or wall adapter, and
   Joulescope `OUT±` to the EVB's `VBAT`/`GND`. Match polarity.
3. Connect J-Link USB for flashing, and the Joulescope to the host via its
   own USB.
4. Wire the sync GPIO from [Wiring reference](#wiring-reference).

### Choosing a Joulescope

| `power.driver` | Instrument | Status |
|---|---|---|
| `joulescope` *(default)* | Auto-detect JS110 or JS220 | Stable |
| `joulescope-js110` | JS110 only | Stable |
| `joulescope-js220` | JS220 only | Stable |
| `ondevice` | Apollo SoC internal power monitoring | Not yet implemented — see [Troubleshooting](#troubleshooting) |

| Aspect | JS110 | JS220 |
|---|---|---|
| Current range | nA → 3 A (auto-ranging) | nA → 10 A (auto-ranging) |
| Sample rate | 250 kSPS | up to 2 MSPS |
| Voltage range | 0–15 V | 0–15 V |

Both expose the same `JoulescopeDriver` interface inside heliaPROFILER.
Auto-detect (`joulescope`) is fine unless you have both connected and want
to pin one explicitly.

## Quick start

On an `apollo510_evb`, the board's sync/state/go GPIO pins are already
registered in heliaPROFILER's board registry, so a minimal config is enough:

```yaml
target:
  board: apollo510_evb
  toolchain: arm-none-eabi-gcc
  transport: rtt

power:
  enabled: true
```

```bash
hpx profile model.tflite --board apollo510_evb --power
```

That's it — `power.driver` defaults to `joulescope` (auto-detects JS110 or
JS220), `power.mode` defaults to `external`, and the sync/state/go GPIO pins
default to the board's registered wiring (GPIO 29 / 36 / 14 on
`apollo510_evb`). Results land in `summary.json`'s `power` section and the
terminal summary; see [Verifying a capture](#verifying-a-capture) for what a
healthy run looks like.

## How the measurement works

1. heliaPROFILER flashes firmware that includes a dedicated **clean**
   inference loop — no per-layer PMU instrumentation, just warmed-up,
   back-to-back inferences.
2. The firmware asserts the sync GPIO high for exactly the duration of that
   loop and low otherwise.
3. The Joulescope samples current/voltage at ~2 MSPS internally and streams
   *statistics* packets to the host at `power.stats_rate_hz` (default
   1000 Hz) — each packet already contains an on-device-integrated
   charge/energy total for that slice.
4. The host watches the Joulescope's `INPUT0` (the sync GPIO) to find the
   rising and falling edges of the gate, then sums the stats packets that
   fall inside it to get gated energy, charge, and a spike-robust
   current/power distribution (median, p95, p99).
5. Energy-per-inference = gated energy ÷ the firmware-reported clean
   inference count (`HPX_CLEAN_INFER_COUNT`).

`summary.json` records `power.measurement_scope: "gpio_gated_clean_window"`
for this path, plus health signals described in
[Verifying a capture](#verifying-a-capture).

## Wiring reference

The **minimum** wiring is one wire: the board's sync/gate GPIO into the
Joulescope's `INPUT0`. Optionally, a 3-wire lock-step handshake adds a
state/error line (device → host, `INPUT1`) and a GO line (host → device,
Joulescope `OUTPUT0`).

| Signal | Direction | Config field | Joulescope side | Default input/output index |
|---|---|---|---|---|
| Sync / gate | device → host | `power.sync_gpio_pin` | `INPUT0` | `power.sync_input_index = 0` |
| State / error (lock-step only) | device → host | `power.state_gpio_pin` | `INPUT1` | `power.state_input_index = 1` |
| GO (lock-step only) | host → device | `power.go_gpio_pin` | `OUTPUT0` | `power.go_output_index = 0` |

### Board-registered defaults

Some boards already have wiring registered, so you don't need to set these
pins yourself:

| Board | `sync_gpio_pin` | `state_gpio_pin` | `go_gpio_pin` |
|---|---|---|---|
| `apollo510_evb` | 29 | 36 | 14 |
| `apollo510b_evb` | 29 | 36 | 14 |

`apollo330mP_evb` has **no** registered GPIO wiring yet, so you must set the
pins explicitly. The shipped `configs/mlperf_tiny/*_ap330*.yaml` examples use
the validated J8 header pins:

```yaml
power:
  sync_gpio_pin: 5    # J8 GP5 — sync/gate
  state_gpio_pin: 6   # J8 GP6 — state
  go_gpio_pin: 7      # J8 GP7 — go
```

### `io_voltage`

`power.io_voltage` (default `1.8`) tells the Joulescope's GPI reference what
voltage represents a logic-high on the gate/state lines. It must match the
board's GPIO I/O rail — a mismatch reads a gate that never appears to go
high (or reads noise as always-high).

## Lock-step (3-wire handshake)

`power.lockstep` (default `None`, i.e. auto) adds a GO/state handshake: the
firmware parks in a wait state until the host confirms its GPIO poller is
armed and asserts GO, so reset latency and host scheduling jitter can never
race the start of the gated window.

- **Auto-enables** when both `state_gpio_pin` and `go_gpio_pin` are wired
  (> 0) *and* the target SoC family's default power reset policy needs it to
  stay race-free — currently true for all Apollo5-family SoCs (including
  Apollo330P), because their default reset strategy chains two sequential
  J-Link operations (`debug_reset+swpoi_reset`), leaving a window where an
  unsynchronized gate can rise and fall before the host poller starts
  watching.
- An **explicit** `true`/`false` always wins over the auto behavior.
- Setting `lockstep: true` requires both `state_gpio_pin > 0` and
  `go_gpio_pin > 0` — heliaPROFILER raises a config error otherwise.

```yaml
power:
  lockstep: true   # force on, e.g. for a custom board with the wiring
  # or: lockstep: false to force off (e.g. bring-up without the extra wires)
```

## Dedicated power firmware

PMU capture needs a host transport (`rtt`, `uart`, `swo`, or `usb_cdc`) to get
per-layer counters off the target. That same transport, if still initialized
during the power capture window, contaminates the current reading — measured
on an Apollo510 EVB (KWS DS-CNN int8, TCM/TCM, LP 96 MHz, GCC):

| Transport left active during capture | Current inflation vs. trusted baseline |
|---|---|
| UART | +17% (UART0-3 peripherals stay powered) |
| SWO | +33% (debug power domain stays powered) |
| USB CDC | +60% (PHY + enumeration) |

Tearing the transport down at runtime right before the window only partially
helps — pad/pinmux configuration residue still shifts the current draw.

To eliminate this, heliaPROFILER renders the same firmware template a second
time with `power_only=true` into `src/main_power.cc`. This build has no
transport at all: the system debug transport is `NSX_DEBUG_NONE`, `hpx_printf`
compiles to a no-op, and there is no RTT/UART/USB/SWO code in the binary. It
does model init, warmup, a GPIO 3-wire lockstep sync, and the gated clean
inference window, then parks. Both executables — `hpx_profiler` (PMU capture)
and `hpx_profiler_power` (power capture) — build from one NSX/CMake project.

During the power stage, hpx flashes `hpx_profiler_power` (via the
NSX-generated per-target J-Link flash script) right before arming the gated
capture, then runs the existing race-free arm → reset → `READY` → `GO`
lock-step flow against it.

With the dedicated binary, all four transports converge on effectively the
same power number — measured on the same Apollo510 EVB/model:

| Transport used for the PMU phase | Current (relative to RTT baseline) | Energy/inference (relative to RTT baseline) |
|---|---|---|
| RTT | 1.00× (baseline) | 1.00× (baseline) |
| UART | ~1.00× | ~0.99× |
| SWO | ~1.00× | ~0.99× |
| USB CDC | ~1.00× | ~1.00× |

All four are within 0.3% of each other. This is controlled by
`power.firmware` (default `dedicated`); `summary.json`'s
`power.power_firmware` field records which mode produced the result.

### Escape hatch: `shared` firmware

```yaml
power:
  firmware: shared
```

or `--power-firmware shared`. This reverts to the pre-existing behavior of
measuring current on the already-flashed transport binary — useful for
bring-up or when no probe is free to reflash — but it carries the
transport-dependent contamination described above. Prefer `dedicated` (the
default) for any number you intend to report or compare across runs.

## Window sizing and duration

`profiling.window_mode` (default `auto`) sizes the clean/gated window at
runtime: the firmware targets `profiling.window_target_ms` of wall-time,
clamped to `[window_min, window_max]`, and reports back exactly how many
clean inferences it ran. `window_mode: fixed` instead runs exactly
`profiling.iterations` clean inferences, no matter how long that takes.

Ordinary (non-power) runs target `window_target_ms: 1000` (1 s) by default.
When `power.enabled: true`, heliaPROFILER automatically raises the *effective*
target to at least 5000 ms (`max(profiling.window_target_ms, 5000)`), because
host-side GPIO polling and Joulescope packet alignment need more time to
settle than a plain PMU capture does.

### Very short inferences

For models whose single inference takes only a couple of milliseconds, even
a multi-second window is dominated by GPIO-edge/gate-boundary timing jitter
as a fraction of the total. Widening the window further reduces that jitter:

```yaml
profiling:
  # This model's inference is extremely short, so the default auto-sized
  # clean/power window contains relatively few milliseconds of gated signal
  # per gate edge. Push the gated window out to several seconds (thousands
  # of inferences) so gate-boundary jitter becomes negligible as a fraction
  # of the measured total.
  window_target_ms: 8000
  window_max: 10000
```

`power.duration_s` (default `None`) is the *host-side safety bound* for the
whole capture, separate from the firmware-side window. Left unset,
heliaPROFILER auto-tunes it from PMU-phase timing (boot settle + estimated
firmware runtime + margin); an explicit value always wins and disables that
auto-tuning.

## Reset strategies

`power.reset_strategy` (default `auto`) controls how the target is reset
before power capture:

| Value | Meaning |
|---|---|
| `auto` | Board/SoC family default (recommended for almost everyone) |
| `power_cycle` | Cycle Joulescope current passthrough off/on |
| `none` | Don't reset — assumes firmware is already running |
| `debug_reset` | J-Link debug reset only |
| `swpoi_reset` | Software point-of-interest reset only |
| `debug_reset+swpoi_reset` | Both, sequentially (Apollo5 family default) |

Explicit values are bring-up/experiment tools — `auto` already picks the
board/SoC-appropriate strategy (Apollo5-family boards default to
`debug_reset+swpoi_reset`, which is also why lock-step auto-enables on those
boards; see [Lock-step](#lock-step-3-wire-handshake)).

## Advanced power floors

These knobs deliberately lower the measured power floor. Use them only when
you understand the tradeoff:

- **`profiling.extreme_mode`** (default `false`) — powers down the shared
  SSRAM (3 MB) and collapses MRAM to a single bank (NVM0 only). **Only safe
  when the model's weights and arena are entirely TCM-resident** — code
  keeps running from MRAM, so transports and `hpx_printf` remain available,
  but any SRAM/MRAM-resident data access will fault or read garbage.
- **`profiling.force_shared_sram`** (default `false`) — a diagnostic that
  unconditionally powers and retains the full shared SSRAM array at boot
  (mirroring AutoDeploy's `ns_power_config(bNeedSharedSRAM=true)`), even when
  the model runs entirely from TCM. Use it to measure SSRAM's static/
  retention contribution to the power floor.
- **Crypto/OTP/radio shutdown** — the dedicated power binary automatically
  shuts down the crypto and OTP subsystems (and the radio subsystem, where
  the HAL exposes it) on AP5-family SoCs. This is capability-gated and needs
  no configuration.

## Verifying a capture

The terminal prints a compact power table at the end of a run:

```text
                 Power
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Metric         ┃        Value ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ Avg current    │   12.345 mA  │
│ Avg power      │   22.222 mW  │
│ Peak current   │   14.567 mA  │
│ Energy         │  666.630 µJ  │
└────────────────┴──────────────┘
```

(Synthetic placeholder values — actual figures depend on your board, model,
and clock configuration.)

`summary.json`'s `power` section carries both the measured numbers and
health signals you should check before trusting a result:

```json
{
  "power": {
    "measurement_scope": "gpio_gated_clean_window",
    "avg_current_a": 0.012345,
    "avg_power_w": 0.022222,
    "median_current_a": 0.012300,
    "p95_current_a": 0.012900,
    "p99_current_a": 0.013100,
    "energy_per_inference_j": 0.00001305,
    "inferences_per_joule": 76628.4,
    "gated_window_count": 1,
    "gated_window_duration_ratio": 0.998,
    "gated_vs_whole_current_ok": true,
    "power_firmware": "dedicated"
  }
}
```

(All numeric values above are synthetic placeholders — actual figures depend
on your board, model, and clock configuration.)

- **`gated_window_duration_ratio`** — measured gate duration ÷ expected
  duration (`clean_infer_count × clean_infer_avg_us`). Healthy captures land
  around 0.99–1.01. Far from 1.0 means the gate/handshake didn't line up
  with the actual inference loop.
- **`gated_vs_whole_current_ok`** — `false` means the gated (inference)
  average current was **not** higher than the whole-capture average, which
  usually signals a gate/timing problem — but can be a legitimate reading
  for very light or bursty models where out-of-gate protocol phases (boot,
  handshake) draw more current than the light gated steady-state.
- **`power.sync.ready_observed`** — `true` once the host observed the
  firmware's lock-step `READY` handshake. `false`/absent with lock-step
  enabled points at a wiring or GO-line problem.
- **`gated_window_duration_suspect`** — set when the duration check above
  fails tolerance, or when the device-reported clean-window timing itself
  looks corrupted (an inference reporting zero time).

`detailed/power_summary.csv` (with `output.detailed: true`) breaks all of
this down per gated window, plus a `whole_capture_window` reference row for
comparison.

### Diagnostics for bring-up

- **`profiling.clean_window_trace`** (default `false`) — makes the firmware
  emit an `HPX_CLEAN_ITER=<n>` line over the transport every clean-window
  iteration, proving the device is genuinely looping inferences for the
  whole gated window rather than stalling. **Perturbs the measurement**
  (extra transport traffic inside the gate) — leave it off for real runs.
- **`profiling.clean_window_probe: busy_loop`** (default: `infer`) — replaces
  the clean window's inference loop with a calibrated CPU spin. Useful during
  bring-up to distinguish "the gate semantics are wrong" from "the model's
  inference behavior is wrong," independent of actual model execution.

## Power config reference

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable power capture |
| `driver` | string | `joulescope` | `joulescope` (auto-detect), `joulescope-js110`, `joulescope-js220`, or `ondevice` (see note below) |
| `mode` | string | `external` | `external` (Joulescope inline) or `internal` (on-device) |
| `duration_s` | int \| null | `null` | Host-side safety bound; `null` auto-tunes from PMU-phase timing |
| `io_voltage` | float | `1.8` | Joulescope GPI reference voltage — must match the board's I/O rail |
| `sync_gpio_pin` | int | board default (`10` generic; `29` on `apollo510_evb`/`apollo510b_evb`) | Gate GPIO the firmware toggles around the clean window |
| `sync_input_index` | int | `0` | Joulescope digital `INPUTn` wired to the sync GPIO |
| `lockstep` | bool \| null | `null` (auto) | Force the 3-wire handshake on/off; `null` auto-enables per board/SoC (see [Lock-step](#lock-step-3-wire-handshake)) |
| `state_gpio_pin` | int | board default (`0` generic; `36` on `apollo510_evb`/`apollo510b_evb`) | State/error GPIO (device → host); `0` disables the wire |
| `go_gpio_pin` | int | board default (`0` generic; `14` on `apollo510_evb`/`apollo510b_evb`) | GO GPIO (host → device); `0` disables the wire |
| `state_input_index` | int | `1` | Joulescope `INPUTn` wired to the state GPIO |
| `go_output_index` | int | `0` | Joulescope `OUTPUTn` wired to the GO line |
| `stats_rate_hz` | int | `1000` | Host stats-packet cadence for gated capture |
| `firmware` | string | `dedicated` | `dedicated` (transport-free binary, see [Dedicated power firmware](#dedicated-power-firmware)) or `shared` |
| `reset_strategy` | string | `auto` | See [Reset strategies](#reset-strategies) |
| `serial` | string \| null | `null` | Joulescope serial number to disambiguate multiple connected devices |

### Related `profiling` and `target` fields

| Field | Type | Default | Description |
|---|---|---|---|
| `profiling.window_mode` | string | `auto` | `auto` sizes the clean window at runtime; `fixed` runs exactly `iterations` |
| `profiling.window_target_ms` | int | `1000` | Target wall-time for the clean window (auto-raised to ≥ 5000 when power is enabled) |
| `profiling.window_min` / `window_max` | int | `10` / `2000` | Clamp bounds for the auto-sized clean-window iteration count |
| `profiling.extreme_mode` | bool | `false` | See [Advanced power floors](#advanced-power-floors) |
| `profiling.force_shared_sram` | bool | `false` | See [Advanced power floors](#advanced-power-floors) |
| `profiling.clean_window_trace` | bool | `false` | See [Diagnostics for bring-up](#diagnostics-for-bring-up) |
| `profiling.clean_window_probe` | string | `infer` | `infer` or `busy_loop`; see [Diagnostics for bring-up](#diagnostics-for-bring-up) |
| `target.ensure_board_powered` | bool | `false` | Pre-run Joulescope current passthrough so the board powers up before flashing; always on when `power.enabled: true` |

### `hpx power-on`

```bash
hpx power-on [--driver joulescope|joulescope-js110|joulescope-js220]
```

Opens the Joulescope and enables current passthrough so the target board
stays powered, holding the connection open until Ctrl-C. Useful when you
want the board powered for manual debugging (JLinkExe, a serial console,
etc.) without running a profiling session.

## Troubleshooting

??? failure "`joulescope: device not found`"
    Joulescope USB driver not installed, or device claimed by another
    process. On Linux, confirm the udev rule for the device is installed
    and replug it (see [Installation](../getting-started/install.md)).
    `pyjoulescope_driver` ships as a core dependency of `helia-profiler`,
    so no extra install is needed.

??? failure "Current reads negative or implausibly high"
    Another power source is also feeding the EVB. Disconnect target USB,
    debug USB power, or coin cell during the capture window.

??? failure "No GPIO gate rising/falling edge detected"
    Check the sync GPIO wiring and `power.sync_gpio_pin` /
    `power.sync_input_index` against the board's `INPUTn` mapping.
    Confirm the firmware reached the power window wait state, and — if
    using a reset strategy that reflashes/resets twice — verify lock-step
    is enabled so reset latency can't race the gate.

??? failure "Wrong `io_voltage` or wrong input index"
    A GPI configured for the wrong voltage threshold, or wired to the wrong
    Joulescope `INPUTn`, reads a gate that's always low (or always
    "high" from noise). Double check `power.io_voltage` matches the
    board's I/O rail and that `sync_input_index`/`state_input_index` match
    the physical wiring.

??? failure "`ready_observed: false` or `gated_window_duration_ratio` far from 1.0"
    The lock-step handshake or gate timing didn't line up. Check the GO/
    state wiring, confirm `power.lockstep` reflects your actual wiring, and
    verify the selected reset strategy relaunches the firmware cleanly
    before capture.

??? failure "\"gated avg current <= whole-capture avg\" warning"
    Usually a gate/timing problem, but can be a legitimate reading for a
    very light or bursty model where out-of-gate protocol phases (boot
    handshake, etc.) draw more current than the gated steady-state. Compare
    against the `whole_capture_window` row in `detailed/power_summary.csv`.

??? failure "`driver: ondevice` raises `PowerError: not yet implemented`"
    The on-device driver is present in `power.driver`'s choices for forward
    compatibility, but its capture path is currently a stub and always
    raises. Use `power.driver: joulescope` (the default) for any real
    measurement today.

??? failure "TOPS-per-Watt missing from summary"
    Only emitted for heliaAOT runs with power enabled. heliaRT/TFLM
    don't expose the MAC count needed for the TOPS calculation.

??? failure "Power numbers differ between transports"
    This should not happen with the default `power.firmware: dedicated` —
    all transports converge to within ~0.3% on Apollo510 EVB testing. If
    you still see transport-dependent drift, check whether
    `power.firmware: shared` is set (explicitly or via `--power-firmware
    shared`); `shared` measures the transport-carrying binary directly and
    is expected to show the contamination described in
    [Dedicated power firmware](#dedicated-power-firmware). Switch back to
    `dedicated` for comparable numbers.
