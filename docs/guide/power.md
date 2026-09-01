# Power Measurement

heliaPROFILER can capture current, voltage, and energy alongside PMU data,
using a **GPIO-gated clean window**: the firmware runs a dedicated,
uninstrumented inference loop and asserts a sync GPIO high for exactly that
window, while a [Joulescope](https://www.joulescope.com/) integrates
charge/energy on-device and streams it to the host. This page walks through
wiring, the minimal config to get a first reading, and every knob you're
likely to need afterward — from simplest to most advanced.

No Joulescope on the bench? A **TI INA228 monitor on the target's own I2C
bus** (e.g. a MikroE Power Monitor Click) can measure whole-window energy
instead — see [On-device INA228 measurement](#on-device-ina228-measurement)
for the trade-offs.

## What you need

- **A Joulescope JS110, JS220, or JS320**, wired in series between your bench
  supply (or wall adapter) and the EVB's power input. The
  `pyjoulescope-driver` distribution ships as a core dependency of
  `helia-profiler` — no extra install.
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
5. For JS320 digital I/O, connect `Vref` to the target MCU I/O rail on the
  EVB side of Joulescope passthrough (1.8 V for the registered Apollo510
  wiring), and connect digital ground to EVB ground. Do not reference the
  upstream supply when it differs from the MCU GPIO voltage.

When JS320 `Vref` comes from the target-side I/O rail, normal capture keeps
passthrough enabled through flash, reset, handshake, and measurement. Cycling
passthrough would remove both target power and the digital logic reference, so
it is reserved for explicit recovery. GPIO observations made during flash/reset
are discarded; only fresh stable samples after a short reset grace period
participate in the READY/GO/GATE protocol.

### Choosing a Joulescope

| `power.driver` | Instrument | Status |
|---|---|---|
| `joulescope` *(default)* | Auto-detect JS110, JS220, or JS320 | Stable |
| `ondevice` | Apollo SoC internal power monitoring | Not yet implemented — see [Troubleshooting](#troubleshooting) |

| Aspect | JS110 | JS220 | JS320 |
|---|---|---|
| Current range | nA → 3 A (auto-ranging) | nA → 10 A (auto-ranging) |
| Sample rate | 250 kSPS | up to 2 MSPS | up to 2 MSPS |
| Voltage range | 0–15 V | 0–15 V | 0–15 V |
| Lock-step GO command | Per-output value | GPO bitmap set/clear | GPO bitmap set/clear |

Both expose the same `JoulescopeDriver` interface inside heliaPROFILER.
Auto-detect (`joulescope`) is fine unless you have both connected and want
to pin one explicitly with `power.serial` or `--power-serial`.

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

That's it — `power.driver` defaults to `joulescope` (auto-detects JS110, JS220,
or JS320), `power.mode` defaults to `external`, and the sync/state/go GPIO
pins default to the board's registered wiring (GPIO 29 / 36 / 14 on
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

### EVB-to-Joulescope wiring

Three signals plus a shared ground carry the handshake. Only the **gate**
is required to produce a valid gated measurement; state and GO add
race-robustness (see [Lock-step](#lock-step-3-wire-handshake)).

| Signal | Direction | Joulescope channel | Purpose |
|---|---|---|---|
| Gate | device → monitor | `INPUT0` | Brackets the measured window |
| State | device → monitor | `INPUT1` | Ready / fault flag |
| GO | monitor → device | `OUTPUT0` | Host says "poller armed, you may run" |

Joulescope channel numbers are always `INPUT0:INPUT1:OUTPUT0` (`0:1:0`
internally) regardless of board — the numbers in the table below are
**Apollo device GPIO pin numbers**.

| EVB | Gate | State | GO | Status |
|---|---|---|---|---|
| Apollo510 EVB | 29 | 36 | 14 | Verified (JS320) |
| Apollo510B EVB | 22 | 23 | 24 | See note below |
| Apollo4 Plus EVB (incl. Blue KBR/KXR) | 22 | 23 | 24 | AutoDeploy AP4P wiring |
| Apollo4 Lite EVB (incl. Blue) | 61 | 23 | 24 | 22 unavailable on AP4L |
| Apollo3 Plus EVB | 26 | 24 | 25 | 22/23 are the J-Link OB VCOM UART |
| Apollo330 Plus EVB | 5 | 6 | 7 | Verified (JS110); J8 header |

!!! warning "Registry defaults are not always the verified wiring"
    Two boards ship built-in defaults that differ from the wiring above,
    because the defaults were inherited rather than measured:

    - **Apollo510B EVB** defaults to the Apollo510 EVB's `29/36/14`, but
      those pins are not readily broken out on the 510B. Use `22/23/24`
      (their only BSP claim is IOM7, which the power binary never uses)
      and set them explicitly in config.
    - **Apollo330 Plus EVB** defaults to `10/0/0` (the generic fallback,
      state and GO disabled). The verified JS110 bench is `5/6/7`.

    Always set `sync_gpio_pin` / `state_gpio_pin` / `go_gpio_pin`
    explicitly for these two boards rather than trusting the defaults.

!!! danger "Apollo510B: avoid GPIO 47/48/49"
    They are accessible on the header but double as `VDD18_SWITCH`,
    `VDDUSB33_SWITCH` and `VDDUSB0P9_SWITCH` — driving them as GPIO during
    a power measurement can toggle supply rails. Check
    `am_bsp_pins.h` for your board before choosing alternatives, and avoid
    whichever IOM carries an on-target power monitor (`power.ina228.i2c_iom`;
    IOM1 = GPIO 8/9 on the 510B).

#### Vref: required on JS220 and JS320, absent on JS110

The JS110's GPI thresholds are fixed; `power.io_voltage` only tells HPX how
to interpret them. The **JS220 and JS320 GPIO connector carries a Vref pin
that sets both the input threshold and the output drive level**, and HPX
never programs it — there is no software knob, so it must be wired:

> "The GPIO includes an external Vref signal. When using the GPIO with your
> device under test, connect Vref to the supply voltage on the device under
> test." — [JS220 User's Guide](https://download.joulescope.com/products/JS220/JS220-K000/users_guide/Joulescope%20JS220%20User's%20Guide.pdf)

Leaving Vref floating on a JS220/JS320 gives undefined thresholds — the
usual symptom is a gate that never reads high, so every capture degrades to
"rose but did not fall" or free-run. The instrument can fall back to an
internal 3.3 V reference, but on a 1.8 V EVB rail that threshold will not
match your logic levels; wire Vref to the board's GPIO rail. Vref must also
satisfy `Vref < (VUSB − 0.5 V)`. Connecting it additionally prevents the
GPOs from back-powering the target.

`power.io_voltage` must match that same rail (default `1.8`).

For Apollo330 Plus, put the device-pin mapping in the profile config and pin
the instrument when more than one is connected:

```yaml
target:
  board: apollo330mP_evb
power:
  enabled: true
  serial: "004204"
  sync_gpio_pin: 5
  state_gpio_pin: 6
  go_gpio_pin: 7
```

```bash
hpx profile model.tflite --config hpx.yml --jlink-serial AP330_JLINK
```

### `io_voltage`

`power.io_voltage` (default `1.8`) tells HPX what voltage represents a
logic-high on the gate/state lines. It must match the board's GPIO I/O rail
— a mismatch reads a gate that never appears to go high (or reads noise as
always-high).

It is a *host-side interpretation* setting only: HPX never programs an IO
voltage on the instrument. On a JS220/JS320 the physical threshold comes
from the wired Vref pin, so `io_voltage` and Vref must describe the same
rail — see [Vref](#vref-required-on-js220-and-js320-absent-on-js110).

## Lock-step (3-wire handshake)

`power.lockstep` (default `None`, i.e. auto) adds a GO/state handshake: the
firmware parks in a wait state until the host confirms its GPIO poller is
armed and asserts GO, so reset latency and host scheduling jitter can never
race the start of the gated window.

- **Auto-enables** when both `state_gpio_pin` and `go_gpio_pin` are wired
  (> 0) and the run is a gated **external** capture (`power.enabled: true`
  with `power.mode: external`). It is *not* SoC-family dependent: without
  lock-step the firmware free-runs its measured window straight out of
  reset, so any host-side reset latency can race the gate on any board.
- An **explicit** `true`/`false` always wins over the auto behavior.
- Setting `lockstep: true` requires both `state_gpio_pin > 0` and
  `go_gpio_pin > 0` — heliaPROFILER raises a config error otherwise.
- Internal (on-device monitor) mode never auto-enables it: the measurement
  happens inside the firmware, so there is no host poller to race.

!!! note "Changed in issue #114"
    Auto-enable used to be Apollo5-only, justified by that family's
    two-invocation `debug_reset+swpoi_reset`. Apollo4 Blue Plus reproduced
    the same failure on a single-invocation `debug_reset`, so every wired
    board now gets the handshake by default. If you have an Apollo3 or
    Apollo4 config carrying an explicit `lockstep: true`, it is now
    redundant — harmless to keep, safe to delete.

    **This invalidates power comparison against older baselines, on purpose.**
    A run recorded before this change had the target free-running its measured
    window; a run after it is lock-stepped, and lock-step is a rail difference
    — the state pin becomes an output, the GO pin's input buffer is enabled,
    and the host holds GO high into it until gate rise. `hpx compare` therefore
    reports `metric.power_power_lockstep_mismatch` and omits power deltas
    between the two. Because `MissingMetricPolicy.FAIL` is the default, a
    power-gated comparison against a stored pre-#114 baseline flips from pass
    to fail. Re-record affected baselines rather than suppressing the check.

### Capture modes

| Mode | Wiring | When to use |
|---|---|---|
| Gated capture | Gate only (`INPUT0`) | Initial board bring-up or a bench without state/GO wiring. The host can miss a short window after a slow reset. |
| Lock-step capture **(preferred, and now the default)** | Gate + state + GO (`INPUT0`, `INPUT1`, `OUTPUT0`) | Production measurements. Firmware waits at `READY`; the host arms the GPI poller and asserts GO before inference begins. |

Every board wired for all three GPIOs auto-selects lock-step. Two registered
boards are not wired for it — `apollo5b_evb` and `apollo330mP_evb` both ship
`state_gpio_pin: 0` / `go_gpio_pin: 0` — so they stay on the gate-only path
until you assign the two extra pins in config. Set `power.lockstep: false`
only while bringing up incomplete wiring; do not use it as the normal
measurement mode.

### Relay and passthrough behavior

JS220 and JS320 use `s/i/range/mode` (`off` / `auto`) to open or close the
target-power relay; JS110 uses its family-specific range selector. `hpx
power-on` and the profiler's preflight passthrough set the relay to `auto`.
Releasing the host's passthrough handle does **not** turn target power off:
the relay remains latched until a power-cycle or explicit relay-off command.

```yaml
power:
  lockstep: true   # force on, e.g. for a custom board with the wiring
  # or: lockstep: false to force off (e.g. bring-up without the extra wires)
```

## Dedicated power firmware

PMU capture needs a host transport (`rtt`, `uart`, `swo`, or `usb_cdc`) to get
per-layer counters off the target. That same transport, if still initialized
during the power capture window, contaminates the current reading with
power draw that has nothing to do with the model:

| Transport left active during capture | Contamination source |
|---|---|
| UART | UART peripherals stay clocked and powered |
| SWO | debug power domain stays powered |
| USB CDC | USB PHY stays powered for enumeration (largest effect) |

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
When `power.enabled: true` **and** `window_mode: auto`, heliaPROFILER raises
the *effective* target to at least 5000 ms (`max(profiling.window_target_ms,
5000)`), because host-side GPIO polling and Joulescope packet alignment need
more time to settle than a plain PMU capture does. `window_mode: fixed` is
left alone — it means "use my number" — so a fixed sub-5 s power window is
built as written. Note the separate floor below it: gates shorter than 1 s
are discarded as glitches during window extraction, so a `fixed` target at
or below about 1000 ms typically yields **no qualifying window at all** and
the capture is recorded as a degraded free-form observation
(`power.observation_degraded`, warning — whole-capture energy retained,
per-inference metrics withheld). In the hairline case where a window passes
edge extraction but its packet-integral duration still lands under the
floor, `power.gate_below_minimum` (error) invalidates the run instead.
Either way a sub-floor target cannot produce a valid gated power run, and
either way the run completes and writes its artifact.

External captures verify the window in two layers before reporting
energy-per-inference. The **authoritative** check compares the firmware's own
STIMER-timed window (`power.terminal.elapsed_us`) against the instrument-timed
gate: two independent clocks watching the same physical window in the same
boot, so they must agree within 1% (plus a small absolute allowance for
stats-packet and gate-poll quantization, which dominates on short windows) —
disagreement is an error (`power.window_observer_mismatch`) and
per-inference metrics are suppressed. The
`clean_infer_count * clean_infer_avg_us` expectation is a **reference
diagnostic**: the profile boot timed it in a different thermal state, and the
LP core clock is HFRC-derived, so a cold power boot can legitimately run the
window ~10% short of it while every published number stays correct — the
energy-per-inference denominator is the inference count, which drift cannot
change. When the firmware envelope confirms the gate **and** the miss is
within the drift-plausible envelope (15%), an `est*count` miss is published
as `gated_window_reference_drift` next to the ratio instead of degrading the
run. Beyond that envelope the warning stands even with envelope agreement —
the observer only proves the gate brackets what the firmware timed, and a
miss that large means the window's *content* changed relative to the profile
phase. When no envelope exists to arbitrate (shared firmware mode, a lost
terminal, or a terminal reporting failed/incomplete work), the `est*count`
band keeps its original authority as a warning
(`power.gate_duration_mismatch`): agreement within the larger of
two stats packets, half an inference (only when more than one inference was
counted), or a cross-boot allowance — 10% for a counted window, 25% for a
`busy_loop` window, whose length is predicted from a calibration pass rather
than counted. That allowance follows the probe in both `power.firmware`
modes. Short GPIO pulses are ignored as glitches; a capture with no
qualifying window is recorded as degraded rather than publishing a plausible
but invalid power number. The accepted ratio and any ignored pulse count are
recorded in `summary.json`.

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

  The two levers are **mutually exclusive**: one powers the shared SSRAM
  array down, the other forces it on, and the config is rejected with a
  `ConfigError` if both are set.
- **Crypto/OTP/radio shutdown** — the dedicated power binary automatically
  shuts down the crypto and OTP subsystems (and the radio subsystem, where
  the HAL exposes it) on AP5-family SoCs. This is capability-gated and needs
  no configuration.

## On-device INA228 measurement

A TI **INA228** current/power monitor wired into the target rail and onto the
target's own I2C bus can replace the Joulescope for **aggregate energy**
measurements. The measurement model inverts: instead of a host instrument
watching a GPIO gate, the INA228 integrates energy and charge in hardware and
the firmware itself brackets the fixed-N inference window —

```
reset accumulators → run N inferences → read energy/charge → report
```

All I2C traffic happens strictly outside the measured region, and the
monitor's ADC integrates autonomously, so nothing the host does can
contaminate the window. Results arrive through the same post-run terminal
report the dedicated power firmware already emits.

`power.driver` names the monitor **chip** (that's what the firmware talks
to); `power.ina228.board` optionally names the **carrier board**, which
fills in the electrical facts that board fixes — address strapping, and the
onboard shunt when the board has one. Explicit values always win over the
preset.

```yaml
power:
  enabled: true
  driver: ina228
  mode: internal          # the target measures itself
  ina228:
    board: mikroe-power-monitor-click   # fills i2c_address 0x4A
    shunt_ohms: 0.5       # REQUIRED for this board — YOUR sense resistor
    max_current_a: 0.05   # size to your real peak, not the shunt rating
    i2c_iom: 1            # Ambiq IOM instance wired to the monitor
    # conversion_time_us: 540   # 50|84|150|280|540|1052|2074|4120
    # averaging_count: 16       # 1|4|16|64|128|256|512|1024
```

An Adafruit INA228 breakout (5832) carries its own 15 mΩ 0.1 % shunt and
default strapping, so the preset alone is a complete config:

```yaml
  ina228:
    board: adafruit-ina228
```

!!! warning "The Adafruit board's 15 mΩ shunt is sized for amps, not milliamps"

    That shunt is convenient — it makes the preset a complete config — but a
    low-power target develops only tens of µV across 15 mΩ, while the
    INA228's input offset is on the order of a µV (datasheet `V_OS`, per ADC
    range). At that signal level the offset alone lands as a
    *percentage-level* current error. Offset is fixed in volts, so the error
    scales inversely with the shunt drop: more sense voltage, proportionally
    less error.

    You do not need a second instrument to deal with this — accuracy here is
    a design-time choice, made when you pick the resistor:

    - **Size the shunt so the drop is mV, not µV**, at your target's typical
      current (see **Choosing a shunt** below). This is what shrinks the
      offset error, and it is entirely under your control.
    - **Buy tolerance.** Whatever error remains after the offset is
      swamped is dominated by the resistor itself, so a 0.1 % part bounds
      you near 0.1 %. This is why `shunt_ohms` must be the real value of a
      known resistor rather than a guess.
    - For **relative** work — A/B comparisons, regression tracking,
      optimisation deltas at a similar operating current — even the stock
      15 mΩ board is fine, because a stable bias cancels in the delta.

    The stock preset is the convenient choice, not the accurate one. If you
    care about absolute milliwatts on a sleepy target, wire your own sense
    resistor.

For custom wiring, omit `board` and set `shunt_ohms` (and `i2c_address` if
strapped away from 0x40) directly. `shunt_ohms` has **no bare default on
purpose**: a wrong shunt calibration produces plausible-looking but wrong
energy, so the value must come either from your wiring or from a board that
physically carries its shunt.

!!! warning "The MikroE Power Monitor Click has no onboard shunt"
    Per its [schematic](https://download.mikroe.com/documents/add-on-boards/click/power_monitor_click/Power_Monitor_click_v100_Schematic.PDF),
    the board's only resistors are `R1` 470 Ω (power LED), `R2`/`R3` 4.7 kΩ
    (I2C pull-ups) and `R4` 10 kΩ (ALERT pull-up). `IN+`/`IN-` go straight
    to a screw terminal — **you supply the sense resistor** and wire it
    across those terminals. `shunt_ohms` is therefore the value of *your*
    resistor, not a board property. (MikroE's own example uses
    `shunt = 0.28`, but that is an arbitrary placeholder for whatever the
    user wired up, not a measurement of the board.)

    The board also ships with both `ADDR SEL` jumpers in the **Down = SDA**
    position, which is I2C address **`0x4A`** — not the INA228's `0x40`
    power-on default. The `mikroe-power-monitor-click` board preset sets
    this automatically; only override `i2c_address` if you have moved the
    jumpers.

### Wiring the Click board

The board has two 2-position screw terminals:

| Terminal | Screw | Net | Wire it to |
|---|---|---|---|
| **IN1** | 1 | `IN+` | Supply side of the broken rail |
| **IN1** | 2 | `IN-` | Target side of the broken rail |
| **IN2** | 1 | `VBUS` | Target-side rail node (same node as `IN-`) |
| **IN2** | 2 | `GND` | Common ground |

Your sense resistor goes **across IN1** — i.e. in series with the rail you
are measuring. Break the target's supply, run the supply into `IN+` and the
target into `IN-`, and the resistor bridges the two.

Tie `VBUS` to the **target side** (the `IN-` node): the INA228 computes
power as `VBUS × CURRENT`, so sensing there reports the energy actually
delivered to the target and excludes the shunt's own dissipation. `GND` must
be common with both the supply and the target. The INA228 itself is powered
from mikroBUS `VCC`, independent of the rail under test, and tolerates a
common-mode voltage up to 85 V — so a 1.8 V or 3.3 V rail is well inside
range.

The screw terminal is also the silver lining of having no onboard shunt:
swapping the sense resistor to re-range the measurement is a screwdriver
turn, not a rework station.

**Choosing a shunt.** Two competing pressures: a larger resistor gives more
signal (and shrinks the INA228's input offset relative to it), a smaller one
steals less of the target's supply. Pick the largest value whose worst-case
drop still fits the high-resolution ±40.96 mV range, leaving headroom for
current peaks above your steady state:

| Peak current | Largest shunt in ±40.96 mV range | Burden at that peak |
|---|---|---|
| 50 mA | 0.8192 Ω (use 0.75 Ω) | ≤41 mV |
| 100 mA | 0.4096 Ω (use 0.39 Ω) | ≤41 mV |
| 400 mA | 0.1024 Ω (use 0.10 Ω) | ≤41 mV |

The limit values are exact: HPX selects the high-resolution range only when
`shunt_ohms × max_current_a ≤ 0.04096`, so a shunt *at* a rounded-up value
(0.82 Ω at 50 mA is 41.0 mV) silently lands on the wide range and loses the
4× resolution this table is trying to buy. Round **down** to a standard
value.

For a target drawing ~20 mA with peaks under ~80 mA, **0.5 Ω** is a good
choice: 10 mV of burden at 20 mA (0.6 % of a 1.8 V rail), ~128 k ADC counts
of resolution, and offset error well under 0.05 %. Power dissipation is
negligible at these currents (0.2 mW), so any 0603/0805 part works —
prioritise **tolerance over rating**, since the resistor's tolerance passes
straight through into your energy figure. A 1 % part means 1 % energy error;
a 5 % carbon film means 5 %.

The offset term is what bites at low current, and it bites hard: 0.5 Ω at
20 mA gives 10 mV of signal, while a 15 mΩ shunt at the same current gives
300 µV — a 33× difference in how much a µV of input offset matters. That
ratio is the whole argument for a larger resistor. What extra signal will
*not* fix is the resistor's own tolerance, which stays a fixed percentage of
the reading — so once the drop is comfortably in mV, your accuracy floor is
simply the part you bought.

HPX picks the ADC range for you from `shunt_ohms × max_current_a`: if the
worst-case shunt drop fits in ±40.96 mV it selects the 4×-resolution range,
otherwise the wide ±163.84 mV range. Setting `max_current_a` far above what
your board actually draws silently costs you resolution, so size it to the
real peak rather than to the shunt's rating.

What you get in `summary.json` is an `on_device_summary` block — integrated
energy (nJ), charge (nC), bus voltage, and the inference count — with
`measurement_scope: on_device_gated_inference`. Divide energy by count for
per-inference energy; average power is energy over the window duration.

!!! warning "That duration comes from the firmware, not the instrument"
    Energy and charge are integrated in hardware over real time and are
    robust. Average power and average current are not: both are energy (or
    charge) divided by the window duration the *firmware* reports, so a
    firmware timing fault scales them by exactly its own error while leaving
    every other field looking healthy. External mode is immune — the
    instrument measures the window itself — but in internal mode this is the
    single point of failure. HPX therefore cross-checks it: a zero duration
    is a hard error, and a duration more than 25% from the host's expectation
    (or longer than the wall time that contained it) is a warning. See
    `power.window_clock_frozen` below.

### When to use which instrument

| | Joulescope | INA228 |
|---|---|---|
| Cost | Bench instrument | A few dollars + Click module |
| Streaming samples | ~2 MSa/s | none (hardware accumulators) |
| Whole-window energy | ✓ (on-device integration) | ✓ (on-device integration) |
| Current distribution (median/p95/p99) | ✓ | ✗ |
| Per-layer power attribution | future | ✗ |
| Host wiring | series supply + GPIO gate wires | none (target I2C) |
| Powers/resets the target rail | ✓ (relay, power-cycle recovery) | ✗ |
| Adds to the measured current | ✗ (fully external) | ✓ (target IOM stays powered) |

The INA228 path is a *cheap aggregate-energy instrument*, not a Joulescope
replacement: with 50 µs minimum conversions it cannot resolve per-layer
detail, and it reports one integrated window, not a sample stream.

!!! warning "On-target monitoring is inside its own measurement"

    Talking to the INA228 requires an IOM to stay powered and clocked for the
    whole run. That current is drawn by the target, on the rail the INA228 is
    measuring — so it is counted in the reported energy. An external
    instrument has no equivalent cost, which is one reason the two do not
    agree out of the box.

    Do not expect to tune it away with conversion settings. The firmware
    brackets the window so no I2C transactions occur inside it (see
    `_ina228_power.j2`) — the adder is the *idle* IOM, not bus traffic — and
    on our bench it did not move between conversion-time settings. On a
    low-power target it can be a non-trivial fraction of the total.

    Measure it on your own board rather than assuming a figure: run once with
    the `power.ina228` block and once without it, using an external instrument
    for both. The block's presence — not `power.driver` — is what decides
    whether the firmware brings up a monitor, so removing it gives you a
    monitor-free baseline (the binaries differ only in the monitor code and
    its I2C/driver modules). Also note the flip side: a *leftover* `ina228:`
    block keeps costing that current on every run, so delete the block when
    the monitor comes off the board. If the block is present but the chip is
    missing or unpowered, an external-instrument run logs a warning and
    continues without a monitor payload (only `driver: ina228` treats a
    missing chip as fatal, since the monitor *is* the measurement there).

### Adding other monitors and boards

The on-target monitor stack is deliberately layered so each piece stays
small:

- **A new carrier board** for an already-supported chip (another INA228
  breakout) is one data entry in `INA228_BOARD_PRESETS` — its strapping,
  its shunt if it has one, and the hint to show when a required fact is
  missing. No new driver, no firmware change.
- **A new monitor chip** is a new `power.driver` value: a host driver class
  (subclass the internal-mode base, set
  `supports_firmware_measurement = True`), a firmware partial that brackets
  the fixed-N window with the chip's own measurement primitive, and a
  `HPX_POWER_MEASUREMENT_SOURCE` value. The terminal envelope, parser,
  result model, and report layer are chip-agnostic and unchanged.

Only the INA228 is supported today.

!!! note "Cross-instrument comparisons"
    Runs measured with different instruments carry different
    `measurement_scope` values, so `hpx compare` **omits power deltas**
    between a Joulescope run and an INA228 run (the comparison itself still
    works for cycles/PMU metrics, and the report says why power was
    omitted). Energy figures also legitimately differ between instruments:
    the INA228 measures whatever rail your shunt sits in, which is usually
    not the same net the Joulescope was in series with.

### Bring-up without a sense resistor

You can smoke-test the whole path before a proper shunt arrives. Short `IN+`
to `IN-` with a jumper — the target then runs through the terminal on the
wiring's own parasitic resistance (a few milliohms of wire and screw-contact
resistance) — and set a deliberately-labelled calibration:

```yaml
    shunt_ohms: 0.5
    calibration_id: "UNCALIBRATED-parasitic-path"
```

Short the terminal rather than leaving it open: floating sense inputs give a
meaningless differential and the target loses its supply path.

Everything except the absolute current scale is exercised for real — I2C
bring-up and the manufacturer/device ID probes, ADC configuration, the
accumulator reset/read bracketing, the terminal envelope, and the host-side
parse into `OnDevicePowerSummary`. **`bus_voltage_uv` is fully trustworthy**
because it does not involve the shunt at all: seeing ~1.8 V (or whatever your
rail is) confirms the chip, the bus, and the read path in one number.

What is *not* valid is magnitude. The reported current is the true current
scaled by `R_parasitic / shunt_ohms`, so with milliohms of wire against a
configured 0.5 Ω expect readings one to two orders of magnitude low. Energy
and charge inherit the same error. Label the run — that is what
`calibration_id` is for — so the numbers are never mistaken for a real
measurement later.

Two things worth reading into the result:

- **Energy exactly zero** (with a non-zero bus voltage) points at the
  calibration register, not your wiring — the pre-v0.2.0 `nsx-sensors`
  SHUNT_CAL bug wrote `SHUNT_CAL = 0` and zeroed every current-derived
  reading. The qualified baseline pins the fixed release, so this should not
  occur.
- **`charge_nc` zero while `energy_nj` is non-zero** means reversed polarity:
  the INA228's ENERGY register is unsigned but CHARGE is signed, and the
  firmware clamps negatives to zero rather than letting them wrap. Swap
  `IN+`/`IN-`.

### Failure modes

The monitor is brought up before any heavy setup, so a missing or mis-wired
part fails fast with a typed terminal phase:

- `ina228_init` — I2C bring-up, ID check (manufacturer `0x5449`, device
  `0x228`), or configuration failed. Check wiring, `i2c_iom`, and address
  strapping — on a MikroE Power Monitor Click the as-shipped address is
  `0x4A`, so leaving `i2c_address` at the `0x40` default fails here.
- `ina228_arm` — the accumulator reset write failed right before the window.
- `ina228_init` code 10/11 — the shunt calibration write failed, or read
  back a different value than was written. HPX computes `SHUNT_CAL`
  host-side and verifies the register after writing it, because an
  unverified calibration fails silently in the worst possible way: with
  `SHUNT_CAL = 0` the chip reports **exactly zero** current, power, energy
  and charge while bus voltage still reads perfectly, and every conversion
  raises `MATHOF`. If you hit this, the monitor is reachable but not
  calibrated — treat it as a driver/bus problem, not a wiring one.
- `ina228_read` — the post-window read-back failed; the window ran but the
  measurement was lost, so the run is treated as failed rather than
  silently reporting nothing.
- An accumulator **overflow** during a very long window fails the run
  explicitly when the monitor is the measurement of record
  (`driver: ina228`). When the monitor is a bystander on an external
  capture, the overflow logs a warning instead — the external instrument's
  result stands. Shorten the window or raise
  `conversion_time_us`/`averaging_count`.
- An internal-mode measurement of **exactly zero** energy and charge, or
  nonzero energy with zero charge, fails the run with a wiring/cadence
  hint rather than publishing a confidently wrong number: the first is a
  dead sense path or a window shorter than one accumulator update, the
  second is the signature of reversed IN+/IN- sense wiring.

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
  duration (`clean_infer_count × clean_infer_avg_us`). Warm back-to-back
  captures land around 0.99–1.01. Far from 1.0 **with**
  `gated_window_reference_drift` present means the firmware's own window
  clock confirmed the gate and only the profile-phase reference is stale
  (cold-start HFRC drift — harmless). Far from 1.0 **without** it means the
  gate/handshake didn't line up with the actual inference loop.
- **`gated_vs_whole_current_ok`** — `false` means the gated (inference)
  average current was **not** higher than the whole-capture average, which
  usually signals a gate/timing problem — but can be a legitimate reading
  for very light or bursty models where out-of-gate protocol phases (boot,
  handshake) draw more current than the light gated steady-state.
- **`power.sync.ready_observed`** — `true` once the host observed the
  firmware's lock-step `READY` handshake. `false`/absent with lock-step
  enabled points at a wiring or GO-line problem.
- **`gated_window_duration_suspect`** — set when the gate is below the 1 s
  floor, when the firmware's window clock disagrees with the gate
  (`power.window_observer_mismatch`), when the power terminal reported
  failed or incomplete work, when the duration check above fails tolerance
  with no firmware envelope to arbitrate it, or when the device-reported
  clean-window timing itself looks corrupted (an inference reporting zero
  time). Per-inference metrics are suppressed when it is set.

The dedicated power firmware also times its own measured window and reports it
as `power.terminal.elapsed_us`. Because nothing else on the host depends on
that clock, it is the one number that can be wrong while every other check
passes, so it is cross-checked directly. Four issue codes come out of that:

- **`power.window_clock_frozen`** — the firmware completed its inferences but
  reported zero elapsed time, so its window clock never advanced. In
  **internal** mode this fails the run: that duration is the denominator for
  average power and current, so the measurement of record is corrupt. In
  **external** mode it is only a warning that degrades the run — the
  instrument owns the power numbers and they are unaffected; only
  `elapsed_us` is meaningless. Two causes produce it: a window timed with
  `DWT->CYCCNT` on a Cortex-M4F part whose debug power domain is down, or a
  STIMER-timed window whose 32.768 kHz XTAL is stopped or unpopulated.
- **`power.window_observer_mismatch`** — external mode: the firmware's
  window disagrees with the host-timed gate beyond 1% (plus an absolute
  stats-packet/gate-poll quantization allowance). The two are
  independent clocks timing the same physical window in the same boot, so
  drift cannot explain a miss — the gate did not bracket what the firmware
  timed. An **error**: it is the authoritative window-integrity verdict, and
  per-inference metrics are suppressed when it fires.
- **`power.window_clock_mismatch`** — internal mode: the firmware's window
  disagrees with `inference_count × reference_inference_us` (25% tolerance —
  loose because that reference comes from a different binary, in a different
  boot and thermal state). A warning.
- **`power.window_clock_exceeds_host_time`** — internal mode only: the
  reported window is longer than the host wall time between starting the
  power binary and collecting its record, which is physically impossible.
  A blunt backstop — it needs roughly 2–3× inflation before it fires.

The envelope comparison behind that last warning is written to
`power.window_clock_ceiling` in `summary.json` on every internal-mode run that
produces a summary at all — not only when the warning fires — so you can see the margin on a healthy run
as well as a breached one. Its five fields are `elapsed_us` and `elapsed_s`
(the firmware's reported window), `host_envelope_s` (the host-timed interval
from starting the power binary to collecting its record), `slack_s`, and
`ratio` (`elapsed_s / host_envelope_s`). The warning fires when `elapsed_s`
exceeds `host_envelope_s + slack_s`, so `slack_s` is part of the bound rather
than a separate note. (The field is absent only if the deployment timestamp
the envelope is measured from could not be read, which no shipped flow does.)

The *profile* binary's clean window has its own clock problem, and its own
code:

- **`profile.clean_window_stalled`** — a warning: one or more clean-window
  iterations lost their cycle measurement. On the Cortex-M4F families
  (Apollo3/3P, Apollo4/4P/4L) `DWT->CYCCNT` only advances while a debugger
  asserts `CDBGPWRUPREQ`, and nothing does between the J-Link reset
  subprocess exiting and the host attach completing. The window then reads
  **short** — 21% low on the Apollo4 Blue Plus KBR runs that found this —
  which makes `clean_infer_avg_us`, the latency reported from it, and any
  power window sized from it all short by the same factor.

    Two shapes are counted, because the counter has been seen doing both.
    `stalled_iters` counts deltas of **exactly zero** (a stopped counter; an
    inference cannot take zero cycles, so this cannot false-positive).
    `partial_iters` counts deltas that are non-zero but below an eighth of the
    run's own warm reference — a counter that kept advancing far too slowly,
    observed once on Apollo4 at ~0.6% of the expected rate. Without the second
    count that shape would accumulate uncounted and the run would report
    itself healthy. `understatement_lower_bound` is derived from the frozen
    count alone, where the arithmetic is exact; partials push the true figure
    higher by an unknown amount. `device_clean_ref_cycles` in `summary.json`
    records the warm reference the floor came from.

    Issue context: `stalled_iters`, `partial_iters`, `total_iters`,
    `affected_fraction`, `understatement_lower_bound`, `ref_cycles`, and —
    only when they apply — `counts_are_inconsistent` (more affected iterations
    than the window ran, so the report itself is corrupt),
    `total_is_unknown`, and `partial_check_inoperative`.

    `understatement_lower_bound` charges a frozen iteration the full `1/total`
    and a partial one `0.875/total` (partials are bounded above by the floor).
    It stays a lower bound: partials are capped by the floor, not pinned to it.

- **`profile.clean_window_clock_rate_low`** — a warning, and the only
  clean-window check whose reference is not itself DWT. Before the window
  opens the firmware times a fixed `nsx_delay_us` interval with DWT and
  reports the cycle count (`device_clean_dwt_rate_cyc` /
  `device_clean_dwt_rate_us` in `summary.json`). `nsx_delay_us` is the
  AmbiqSuite BOOTROM cycle loop: no DWT, no CoreSight register, no debug power
  domain, so it keeps its calibration exactly when DWT loses its. Expected is
  `SystemCoreClock × probe_us / 1e6` in closed form, and the warning fires
  below half of that.

    This exists because the two counters above are **DWT-relative and
    therefore scale-invariant**: the partial floor is a warm sample taken with
    the same counter moments earlier, so a slowdown of factor `k` scales both
    sides and cancels. A perfectly uniform slowdown reports zero of both —
    replaying the measured ~0.6%-of-rate case gives exactly that — and only
    this check sees it.

- **`profile.clean_window_check_inoperative`** — a warning: the partial floor
  was zero (every warm sample was itself frozen), so no iteration could fall
  below it. Zero counts in that state are not evidence of a healthy window,
  and this says so rather than letting silence read as health.

**What none of them cover**, stated plainly: a slowdown that begins exactly at
window open, ends exactly at window close, and is perfectly uniform throughout.
The rate probe is taken before the window and the counters are relative, so
that shape is the residual. A GPIO-gated run catches it anyway via
`gated_window_duration_ratio`.

RTT builds **reduce** the exposure by waiting, before opening the window,
for the host to drain the RTT up-buffer — which the J-Link can only do with
the DAP alive. That wait is bounded (1 s by default,
`HPX_CLEAN_WINDOW_ATTACH_WAIT_MS`) and reports how long it actually spent as
`HPX_CLEAN_ATTACH_WAIT_US`, so a run that timed out is distinguishable from
one that attached on the first poll. An unusually slow host attach can still
outlast the budget; the detectors above are what cover that case, and the
transports with no such signal (UART, USB CDC).

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
  inference behavior is wrong," independent of actual model execution. The
  spin is sized to approximately `window_target_ms`, but the duration the run
  reports is always the one STIMER measured — the probe never echoes the
  nominal target back as if it had been measured.

## Power config reference

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable power capture |
| `driver` | string | `joulescope` | `joulescope` (auto-detects JS110, JS220, or JS320), or `ondevice` (see note below) |
| `mode` | string | `external` | `external` (Joulescope inline) or `internal` (on-device) |
| `duration_s` | int \| null | `null` | Host-side safety bound; `null` auto-tunes from PMU-phase timing |
| `io_voltage` | float | `1.8` | Joulescope GPI reference voltage — must match the board's I/O rail |
| `sync_gpio_pin` | int | board default (`10` generic; `29` on `apollo510_evb`/`apollo510b_evb`) | Gate GPIO the firmware toggles around the clean window |
| `sync_input_index` | int | `0` | Joulescope digital `INPUTn` wired to the sync GPIO |
| `lockstep` | bool \| null | `null` (auto) | Force the 3-wire handshake on/off; `null` auto-enables on any board wired for it doing a gated external capture (see [Lock-step](#lock-step-3-wire-handshake)) |
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
| `profiling.window_target_ms` | int | `1000` | Target wall-time for the clean window (raised to ≥ 5000 when power is enabled **and** `window_mode: auto`) |
| `profiling.window_min` / `window_max` | int | `10` / `500000` | Clamp bounds for the auto-sized clean-window iteration count |
| `profiling.extreme_mode` | bool | `false` | See [Advanced power floors](#advanced-power-floors) |
| `profiling.force_shared_sram` | bool | `false` | See [Advanced power floors](#advanced-power-floors) |
| `profiling.clean_window_trace` | bool | `false` | See [Diagnostics for bring-up](#diagnostics-for-bring-up) |
| `profiling.clean_window_probe` | string | `infer` | `infer` or `busy_loop`; see [Diagnostics for bring-up](#diagnostics-for-bring-up) |
| `target.ensure_board_powered` | bool | `false` | Pre-run Joulescope current passthrough so the board powers up before flashing; always on when `power.enabled: true` |

### `hpx power-on`

```bash
hpx power-on [--driver joulescope] \
    [--power-serial SERIAL]
```

Opens the Joulescope and enables current passthrough so the target board
stays powered, holding the connection open until Ctrl-C. Useful when you
want the board powered for manual debugging (JLinkExe, a serial console,
etc.) without running a profiling session.

When multiple Joulescopes are attached, `--power-serial` is required. For
example, use `hpx power-on --driver joulescope --power-serial 25QG` for
the JS320 bench.

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

??? failure "No GPIO gate rising edge detected (`no_gate_rise`)"
    **Check `power.lockstep` before you check the wiring.** If lock-step is
    disabled while `state_gpio_pin`/`go_gpio_pin` *are* configured, that is
    the likeliest cause and heliaPROFILER now says so in the warning and in
    the run's `gate_failure` metadata: with lock-step off the firmware never
    waits for the host, so it can open **and close** its measured window
    before the Joulescope GPI poller is armed. The run comes back
    `integrity: degraded (no_gate_rise)` and looks exactly like a dead gate
    wire. Set `power.lockstep: true`, or drop an explicit
    `power.lockstep: false` and let it auto-enable.

    Only if the gate is still missed with lock-step on: check the sync GPIO
    wiring and `power.sync_gpio_pin` / `power.sync_input_index` against the
    board's `INPUTn` mapping, and confirm the firmware reached the power
    window wait state.

??? failure "GPIO gate rose but did not fall (`no_gate_fall`)"
    The firmware entered the measured window but did not close it before the
    safety bound. Increase `power.duration_s`, or check for a firmware hang
    inside the clean window. This one is *not* a lock-step problem — the gate
    was observed rising, so the poller was armed in time.

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

    **Check the firmware's own clock first.** Both symptoms above are about
    the *host-observed* gate, but a firmware timing fault can look similar and
    has a completely different fix. The distinguishing symptom is
    `power.terminal.elapsed_us` disagreeing with the gate: compare it against
    `power.capture_duration_s` in `summary.json` (they should agree to well
    under 1%). If `elapsed_us` is `0`, or several times the gate, the wiring
    is fine and the firmware timed its window with a clock it could not read —
    see `power.window_clock_frozen` and `power.window_observer_mismatch`
    above. The remedy is a firmware rebuild, not a rewire. If instead
    `elapsed_us` *agrees* with the gate and only the est×count ratio is off,
    that is cold-start clock drift, reported as
    `gated_window_reference_drift` — nothing is wrong.

??? failure "\"gated avg current <= whole-capture avg\" warning"
    Usually a gate/timing problem, but can be a legitimate reading for a
    very light or bursty model where out-of-gate protocol phases (boot
    handshake, etc.) draw more current than the gated steady-state. Compare
    against the `whole_capture_window` row in `detailed/power_summary.csv`.

??? failure "`driver: ondevice` raises `PowerError: not yet implemented`"
    The generic `ondevice` driver is a stub with no firmware-side producer
    and always raises. For a real on-target measurement use
    `power.driver: ina228` with an INA228 wired to the target's I2C bus
    (see [On-device INA228 measurement](#on-device-ina228-measurement)),
    or `power.driver: joulescope` (the default) for a bench instrument.

??? failure "TOPS-per-Watt missing from summary"
    `tops`/`tops_per_watt` are emitted for any power run whose model can be
    analyzed for MACs — heliaRT, TFLM, and heliaAOT tflite models (not
    ExecuTorch). They are suppressed when the power window is not
    inference-bracketed (free-form or whole-capture capture, or a busy-loop
    probe), because TOPS scales the per-inference op count by the number of
    inferences measured inside the window.

??? failure "Power numbers differ between transports"
    This should not happen with the default `power.firmware: dedicated` —
    all transports converge to within ~0.3% on Apollo510 EVB testing. If
    you still see transport-dependent drift, check whether
    `power.firmware: shared` is set (explicitly or via `--power-firmware
    shared`); `shared` measures the transport-carrying binary directly and
    is expected to show the contamination described in
    [Dedicated power firmware](#dedicated-power-firmware). Switch back to
    `dedicated` for comparable numbers.
