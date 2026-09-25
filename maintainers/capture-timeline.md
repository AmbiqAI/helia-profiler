# Gated power capture: timeline contract

Status: draft. This is the acceptance specification for bench validation of the
gated power capture (`hpx profile --power` with an external gated instrument,
the Joulescope JS220/JS320 path). It states what the capture does, in order,
how long the host waits for each step and why, what each published interval
brackets, which error wins when a step fails, and what each hint may claim.
Where the code does not yet meet a clause, the clause says so and names the
open item that resolves it.

Every number carries a provenance mark:

- **[M]** measured on hardware, with the source named;
- **[E]** an estimate reasoned from other constants or from how the code
  works, with no hardware measurement behind it;
- **[U]** no provenance found in code, comments, docs or history.

A bench run turns [E] and [U] into [M], or revises the number.

## 1. Terms

| Term | Meaning |
| --- | --- |
| Dedicated | `power.firmware: dedicated` (default). A separate `power_only` binary with no transport, a host-planned inference count `N`, and a power terminal record after the window. |
| Shared | `power.firmware: shared`. The transport-attached profile binary is reset and reused; its window is sized by that binary at runtime and it emits no power terminal. |
| Lock-step (LS) | `power.lockstep` resolves true and the driver provides a GO output: the firmware signals READY on the state line and waits for GO before the window. |
| Free-running (FR) | No READY/GO handshake. The firmware runs from reset into the window. |
| `D` | The configured capture bound: `power.duration_s`, or the stage estimate when unset (§3). |
| `N`, `a` | Planned inference count and reference per-inference time (`clean_infer_avg_us`); dedicated from the power plan, shared from the profile boot. |
| `W` | Planned window, `N·a`. |
| `L` | Longest window the gate-duration check accepts: `W` plus `max(a/2 if N>1, 2/stats_rate_hz, W·max(tol, 0.15))`, with `tol` 0.10 for counted probes and 0.25 for busy_loop. |
| `P` | Warm-up before the window as the host budgets it: `max(3, profiling.warmup)·a` for probes that run inferences, `0` for busy_loop. |

## 2. Steps

Host code: `capture/__init__.py` (`capture_power`, `_release`),
`power/joulescope/capture_gated.py` (`capture_gated`), `power/diagnostics.py`,
`power/joulescope/sync.py`, `target/lifecycle.py`. Firmware code:
`firmware/templates/_main_base.cc.j2`, `_gpio_sync.j2`, `_stimer_init.j2`,
`_busy_loop_calibration.j2`, `_power_terminal*.j2`.

| # | Step | Actor | LS | FR |
| --- | --- | --- | --- | --- |
| S1 | Compute bounds `F` (gate wait) and `R` (READY wait) (§3) | host | yes | `F` only |
| S2 | Arm: drive GO low | host | yes | no-op |
| S3 | Start capture: open the instrument, start the stats and GPI streams, start the GPI poller | host | yes | yes |
| S4 | Reset the target per `power.reset_strategy` | host | yes | yes |
| S5 | Boot and init: system, clocks, INA228 (when configured), engine pre-start, model init. Shared USB CDC firmware first blocks until the host asserts DTR. | firmware | yes | yes |
| S6 | Warm-up: dedicated `max(1, profiling.warmup)` inferences; shared at least 3 for counted probes | firmware | yes | yes |
| S7 | READY: state line high | firmware | yes | no-op |
| S8 | Host qualifies READY: 0.5 s grace [U], then 3 consecutive high GPI samples [U], within `R` | host | yes | none |
| S9 | Shared USB CDC only: open the port with DTR asserted (0.5 s floor, then polls up to 15 s [U]) | host | after S8 | after S4 |
| S10 | GO high. The capture phase is set to `go_signaled` first, so an early gate edge is not discarded | host | yes | phase only |
| S11 | Firmware waits for GO: up to about 3 s [U], then continues without it | firmware | yes | no-op |
| S12 | Pre-window setup: reset inputs; busy_loop calibration (includes a STIMER settle); STIMER settle; INA228 arm | firmware | yes | yes |
| S13 | Gate rise; the host drops GO on seeing it | both | yes | rise only |
| S14 | Measured window: `N` inferences, or the busy_loop spin | firmware | yes | yes |
| S15 | Gate fall | firmware | yes | yes |
| S16 | Early stop 0.15 s [U] after a qualifying fall; teardown (poller join 1.0 s [U]) | host | yes | yes |
| S17 | Dedicated only: power terminal record, emitted repeatedly, then park | firmware | yes | yes |
| S18 | Dedicated only: host collects the terminal record | host | yes | yes |

**C-S1.** The gate wait `F` is the only wait covering both gate edges. It
starts after GO in LS, and after the reset returns in FR (after S9 for shared
USB CDC).

**C-S2.** A gate pulse shorter than the minimum qualifying gate (1.0 s [U],
`DEFAULT_POWER_MIN_WINDOW_MS`) is not a window. The poller resets and waits for
the next rise.

**C-S3.** With shared firmware over USB CDC in LS, S5 waits for DTR while the
host waits for READY (S8) before asserting DTR (S9). As written, that
combination cannot complete. It is not reproduced on hardware and is not
rejected at preflight. Open: #373.

## 3. Host wait bounds

| Bound | Formula | Starts |
| --- | --- | --- |
| `D` (auto) | dedicated or busy_loop plan: `8.0 + W + 6.0`; shared: `8.0 + profiled run + clean run + 6.0`; capped at 30 s; 30 s when no estimate | n/a |
| `F`, LS | `max(D, L + 2.0)` | GO |
| `F`, FR | `max(D, L + 2.0 + 8.0 + P)` | reset return |
| `F`, window unknown | `D` | as above |
| `R` (LS) | `max(D, 8.0 + P + 2.0)` | after the 0.5 s grace |
| `rise_due` (classification only) | FR: `8.0 + P`; LS: none | reset return |
| Terminal collect | `max(2, min(10, F/10))` | after capture |

**C-W1.** `F` must cover everything the firmware does between the start of the
wait and the gate fall:
- LS: S11 to S15, which is pre-window setup plus the window;
- FR: S5 to S15, which is boot, init, warm-up, setup and the window.

With the constants below, this holds for counted probes if the 8 s boot
allowance and the 2 s headroom hold. Both are [E]. It does not yet hold for
busy_loop:
- the host budgets `P = 0`, while the firmware still runs warm-up inferences;
- calibration and a second STIMER settle (up to 1 s each cold [M]) come
  before the window.

Open: #302 follow-up 3.

**C-W2.** `R` must cover S5 to S7, which is boot, init and warm-up. It has the
same [E] dependencies and the same busy_loop gap as C-W1.

**C-W3.** A configured `D` above the derived minimum is kept. A lower one is
raised, and the raise is logged, at WARNING when `power.duration_s` is set
explicitly. `D` never changes the window the firmware runs.

**C-W4.** `F` must also cover a window that runs longer than planned, up to
`L`. A stalled clean-window reference reads `a` low, which sizes `N` too high,
so the real window runs longer than `W` and can pass `L`. The planning
docstring and warning in `stages/plan_power.py` state the opposite direction
(a short window). Open: #302 follow-up 4, stall-aware sizing and corrected text.

## 4. Published intervals

| Interval | Brackets | Excludes | Published as |
| --- | --- | --- | --- |
| Firmware gate | STIMER just before gate rise to just after gate fall: both GPIO writes and the window | settle, INA228 arm and read | `power.terminal.gate_elapsed_us` (dedicated) |
| INA228 accumulation | after the accumulator reset to just before the register reads | the reads, which latch a few I2C transactions later | `power.on_device_summary.duration_us` (dedicated with INA228) |
| Firmware whole window | after the STIMER settle to after the INA228 reads: setup, gate and reads | settle, calibration, warm-up, READY/GO | `power.terminal.elapsed_us` |
| Instrument gate | sum of stats-packet durations whose time falls within the detected rise and fall | anything outside the gate; packet-quantized (about 1 ms at 1 kHz) | `summary.json` `power.capture_duration_s`, and `profile_results.json` `power.duration_s` |
| Capture window | host monotonic, from capture start to result | nothing: it includes reset, READY, GO, the wait and teardown | `power.capture_window_s` |
| Gate bound | the value of `F` | n/a | `observation_deadline_s` (`profile_results.json`) |
| READY wait | host time from after the grace to READY qualified | reset and grace | `power.sync.ready_wait_s` |

**C-I1.** Dedicated firmware: `gate ≤ accumulation ≤ whole window`. The
terminal parser enforces this.

**C-I2.** The instrument gate and the firmware gate agree within 1 % plus
absolute slack (`EXTERNAL_WINDOW_CLOCK_TOLERANCE`). This is [M] by the cited
evidence of #142, #181 and #195. Validity arbitration decides which reading
stands.

**C-I3.** `summary.json` `power.capture_duration_s` is the instrument gate,
not the capture. `timing.capture_duration_s` is the PMU capture. These names
are kept for compatibility, and this contract is the reference for what they
mean.

## 5. Error precedence

**C-E1.** If a start step (reset, READY, DTR or GO) fails, the capture ends
at once. The step's own exception surfaces unchanged, with its cause kept,
ahead of any error the capture would otherwise raise. A replaced capture error
is hidden as context. The gate bound is not waited out.

**C-E2.** The capture stage passes a `PowerError` through unchanged and wraps
any other exception in a `PowerError` whose hint names the instrument. This
clause is not met for errors from the reset probe or the USB CDC port. A J-Link
reset failure, or a CDC port failure, surfaces with the hint "Check that the joulescope is
connected and powered on", and the correct hint is kept only in the cause. Open: the
#302 stage-hint follow-up.

**C-E3.** No usable window, but stats packets arrived: the capture returns a
degraded result carrying a `gate_failure` classification. Dedicated firmware
then collects the terminal, whose own error surfaces next.

**C-E4.** No usable window and no stats packets: the classifier's
`PowerError` surfaces.

**C-E5.** A gate duration outside the accepted band is a warning at capture
time. Validity arbitration decides the run's status.

**C-E6.** Terminal errors (status not ok, `stimer_dead`, incomplete record,
gate not lowered, zero energy, reversed wiring) surface after a successful or
degraded capture.

How a firmware failure surfaces depends on the mode:

| Firmware failure | LS | FR |
| --- | --- | --- |
| Init or model failure before READY (dedicated) | READY timeout after `R`. The terminal record holding the cause is never collected. | Degraded `no_gate_rise` after `F`, then the terminal error |
| `stimer_dead` before the gate | Degraded `no_gate_rise` with the wiring hint after `F`, then the terminal error | same |
| GO later than the firmware's ~3 s GO wait | The firmware free-runs; a rise before `go_signaled` is discarded, giving `no_gate_rise` | n/a |

## 6. Hint claims

**C-H1.** A hint may name a single cause only when its inputs rule out the
others. Otherwise it says "likely" or lists the alternatives.

| Hint | May claim | Meets C-H1 | Gap and item |
| --- | --- | --- | --- |
| READY timeout | wiring, reset strategy, firmware not reaching the sync wait | no | Does not mention a dedicated init failure (terminal not collected) or the #373 ordering. Candidate for the capture follow-up PR. |
| Stage wrapper | the step that failed | no | Names the instrument for probe and CDC failures. #302 stage-hint follow-up. |
| `no_gate_rise`, bound exhausted | the bound likely ended before the window was due | yes | FR only, worded "likely" |
| `no_gate_rise`, lock-step suspect | a free-running window raced the poller | no | Also fires for firmware that never reached the window. Candidate. |
| `no_gate_rise`, wiring | wiring, wait state or reset | partly | Also covers `stimer_dead` and a late GO. Candidate. |
| `no_stats_window` | host timestamps did not overlap the stats timeline | no | Also fires when the only pulse was shorter than 1.0 s (C-S2). Candidate. |
| `no_gate_fall` | hang when high longer than `L`; bound too short when shorter | yes | |
| Stalled reference warning | the window will be longer than planned | no | States "short". #302 follow-up 4. |

## 7. Lock-step and free-running

| Aspect | LS | FR |
| --- | --- | --- |
| Gate wait starts | after GO | after reset returns |
| `F` | `max(D, L + 2)` | `max(D, L + 2 + 8 + P)` |
| READY wait | `R` | none |
| Reset race | edges before `go_signaled` are discarded, and the firmware is parked at GO | the firmware runs from reset. A window already high when the phase switches is missed, and a slow reset primitive narrows the margin (#114). |
| Init failure surfaces as | READY timeout | degraded capture, then the terminal error |
| `go_release_to_gate_rise_s` | GO to rise | reset return to rise |

## 8. Constants

| Constant | Value | Mark | Evidence |
| --- | --- | --- | --- |
| `BOOT_SETTLE_S` | 8.0 s | E | Raised from 4.0 with the comment "reset/SBL/firmware init allowance"; #367 and #372 describe it as an estimate |
| `FALL_WAIT_HEADROOM_S` | 2.0 s | E | Reasoned from other constants in #367 |
| Stage estimate margin | 6.0 s | U | |
| Warm-up reps floor (host) | 3 | E | Mirrors the shared firmware's floor; dedicated firmware uses `max(1, warmup)` |
| GPI poll sleep | 0.004 s | U | |
| Post-fall guard | 0.15 s | U | |
| Reset grace (LS) | 0.5 s | U | |
| READY qualification | 3 samples | U | |
| GPI read timeout | 0.5 s | U | |
| Poller join | 1.0 s | U | |
| Firmware GO wait | about 3 s | U | |
| Default capture bound | 30 s | U | |
| Minimum qualifying gate | 1.0 s | U | |
| Window target | 5000 ms | U | Qualitative rationale only |
| Drift plausibility | 0.15 | M | Up to about 12 % observed on a cold AP510 first run after idle (#181) |
| Counted window tolerance | 0.10 | U | |
| busy_loop window tolerance | 0.25 | E | Reasoned from the calibration band |
| STIMER settle deadline, band 245..410 ticks | 1 s | M | Apollo4 Blue Plus bench (#124) |
| Instrument/firmware gate agreement | 1 % | M | #142, #181, #195 |
| JLinkExe timeout | 15 s | U | |
| SWPOI reset script sleeps | 2 × 1 s | U | |
| Power-cycle off / settle | 0.5 / 2.0 s | U | |
| CDC re-enumeration floor / poll / timeout | 0.5 / 0.1 / 15 s | U | |

## 9. Open items

| Item | Clauses | Status under this contract |
| --- | --- | --- |
| #302 follow-up 3: busy_loop warm-up not budgeted | C-W1, C-W2 | open |
| #302 follow-up 4: stalled reference | C-W4, C-H1 | open. The contract fixes the direction (the window runs longer). |
| #302 stage-hint follow-up | C-E2, C-H1 | open |
| #373: shared USB CDC with lock-step | C-S3 | open, unconfirmed |
| #374: live definitions only | none | outside the timeline (render hygiene) |
| #376: guarded test harness | all clauses | open. Each clause needs a guarded test; several capture tests cannot yet run under the guard. |

Found while writing this contract, and not covered by an open item:
- READY timeout hides a dedicated init failure;
- `no_stats_window` for a sub-1 s pulse;
- `stimer_dead` and a late GO get the wiring hint;
- the stall text direction (covered by #302 follow-up 4).

They are candidates for the capture follow-up PR.

## 10. Bench acceptance

A bench run validates this contract when it records, for each case:
- the resolved mode (dedicated or shared, LS or FR, probe);
- `D`, `F` and `R`;
- the host times reset return, READY qualified, GO, gate rise and gate fall
  (`power.sync_timing_s`, `power.sync.ready_wait_s`);
- the firmware gate, accumulation and whole-window intervals, and the
  instrument gate (§4);
- the error or `gate_failure` classification and hint, verbatim.

From those it measures:
- the reset-to-READY time (checks the 8 s boot allowance and C-W2);
- the GO-to-rise time (checks the 2 s headroom and C-W1 in LS);
- the reset-to-rise time (checks C-W1 in FR).

A clause passes when every case meets it with the measured numbers, which then
replace the [E] marks above.
