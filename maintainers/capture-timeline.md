# Gated power capture: timeline contract

Status: draft. This is the acceptance specification for bench validation of the
gated power capture: `hpx profile --power` with an external gated instrument,
the Joulescope JS220/JS320 path. It states:
- what the capture does, in order;
- how long the host waits for each step, and why;
- what each published interval brackets;
- which error wins when a step fails;
- what each hint may claim.

Where the code does not yet meet a clause, the clause says so and names the
open item that resolves it.

Every number below carries a provenance mark:

- **[M]** measured on hardware, with the source named;
- **[E]** an estimate reasoned from other constants or from how the code
  works, with no hardware measurement behind it;
- **[D]** a design margin chosen above a named hardware measurement;
- **[U]** no provenance found in code, comments, docs or history.

A bench run turns [E] and [U] into [M], or revises the number.

## 1. Terms

| Term | Meaning |
| --- | --- |
| Dedicated | `power.firmware: dedicated` (the default). A separate `power_only` binary: no transport, a host-planned inference count `N`, and a power terminal record after the window. |
| Shared | `power.firmware: shared`. The transport-attached profile binary is reset and reused. Its window is sized by that binary: at runtime in auto window mode, or `profiling.iterations` in fixed mode. It emits no power terminal. |
| Lock-step (LS) | `power.lockstep` resolves true and the driver provides a GO output. The firmware signals READY on the state line and waits for GO before the window. |
| Free-running (FR) | No READY/GO handshake. The firmware runs from reset into the window. |
| `D` | The configured capture bound: `power.duration_s`, or the stage estimate when unset (§3). |
| `N`, `a` | Planned inference count and reference per-inference time (`clean_infer_avg_us`). Dedicated firmware takes them from the power plan; shared firmware from the profile boot. |
| `W` | Planned window, `N·a`. |
| `L` | The longest window the gate wait is sized for: `W` plus `max(a/2 if N>1, 2/stats_rate_hz, W·max(tol, 0.15))`. `tol` is 0.10 for counted probes and 0.25 for busy_loop; the 0.15 floor covers cross-boot drift. The capture-time duration check (C-E5) uses `tol` without the floor, so for counted probes a gate between `1.10·W` and `1.15·W` is within `L` but still draws the C-E5 warning. |
| `P` | Warm-up before the window, as the host budgets it: `max(3, profiling.warmup)` inferences. A counted probe prices them at `a`; busy_loop, whose `a` is the whole spin, prices them from the profiled per-inference cycles at the run's CPU clock. `0` when neither is known. |

## 2. Steps

**Host code:**
- `capture/__init__.py` (`capture_power`, `_release`);
- `power/joulescope/capture_gated.py` (`capture_gated`);
- `power/diagnostics.py`;
- `power/joulescope/sync.py`, `power/sync.py`;
- `power/joulescope/device.py`, `power/joulescope/diagnostics.py`;
- `stages/capture_power.py`, `stages/collect_power_terminal.py`;
- `capture/power_terminal.py`, `results/artifacts.py` (`PowerRunPlan.planned_window_s`);
- `target/lifecycle.py`.

**Firmware code:**
- `firmware/templates/_main_base.cc.j2`;
- `_gpio_sync.j2`;
- `_stimer_init.j2`;
- `_busy_loop_calibration.j2`;
- `_power_terminal*.j2`.

| # | Step | Actor | LS | FR |
| --- | --- | --- | --- | --- |
| S1 | Compute bounds `F` (gate wait) and `R` (READY wait) (§3) | host | yes | `F` only |
| S2 | Arm: drive GO low | host | yes | no-op |
| S3 | Start capture: open the instrument, start the stats and GPI streams, start the GPI poller | host | yes | yes |
| S4 | Reset the target per `power.reset_strategy` | host | yes | yes |
| S5 | Boot and init: system, clocks, INA228 (when configured), engine pre-start, model init. Shared firmware also runs the core clock probe (STIMER-windowed builds: a STIMER settle plus 3 passes of about 17 ms, about 0.07 s warm and up to about 1.05 s cold) and, for shared RTT on DWT-timed builds, a clean-window attach wait of up to 1 s [D] (about 4.9 times a 204 ms gap observed on an Apollo4 Blue Plus, `_clean_window_attach_wait.j2`). Shared USB CDC firmware first blocks until the host asserts DTR. | firmware | yes | yes |
| S6 | Warm-up: dedicated, `max(1, profiling.warmup)` inferences; shared, at least 3 for counted probes | firmware | yes | yes |
| S7 | READY: state line high | firmware | yes | no-op |
| S8 | Host qualifies READY: 0.5 s grace [U], then 3 consecutive high GPI samples [U], within `R` | host | yes | none |
| S9 | Shared USB CDC only: open the port with DTR asserted (0.5 s floor, then polls up to 15 s [U]; a pinned `target.usb_port` skips both, and without a known marker the scan polls every 0.5 s) | host | after S8 | after S4 |
| S10 | GO high. The capture phase is set to `go_signaled` first, so the snapshot poller does not discard an early gate edge. | host | yes | phase only |
| S11 | Firmware waits for GO: up to about 3 s [U], then continues without it | firmware | yes | no-op |
| S12 | Pre-window setup: reset inputs; busy_loop calibration (includes a STIMER settle); STIMER settle; INA228 arm | firmware | yes | yes |
| S13 | Gate rise; the host drops GO on seeing it | both | yes | rise only |
| S14 | Measured window: `N` inferences, or the busy_loop spin | firmware | yes | yes |
| S15 | Gate fall | firmware | yes | yes |
| S16 | Early stop 0.15 s [U] after a qualifying fall; teardown (poller join 1.0 s [U]) | host | yes | yes |
| S17 | Dedicated only: power terminal record, then park. Over RTT it is written once; over UART, SWO and USB it is re-emitted every 250 ms [U]. | firmware | yes | yes |
| S18 | Dedicated only: host collects the terminal record | host | yes | yes |

When the driver cannot gate, or no inference count is known, the capture falls
back to an ungated capture of length `D` with no handshake, and none of the
gate clauses below apply.

**C-S1.** The gate wait `F` is the only wait covering both gate edges. It
starts after GO in LS, and after the reset returns in FR, or after S9 for
shared USB CDC.

**C-S2.** A gate pulse shorter than the minimum qualifying gate (1.0 s [U],
`DEFAULT_POWER_MIN_WINDOW_MS`) is not a window. The poller resets and waits for
the next rise.

When the planned window is known, gates are also ranked against the shortest
window the plan accepts, `W − max(a/2 if N>1, 2/stats_rate_hz,
W·max(tol, 0.15))`, the mirror of `L`:
- the snapshot poller ends the capture early only on a high that reaches it;
  a shorter high past the minimum resets the poller, which keeps waiting;
- the window chosen from the GPI stream (JS220/JS320, not phase-gated) or from
  the poll samples is the last high that reaches it;
- if no high reaches it, the capture runs to its bound and the last high past
  the fixed minimum is kept, for the C-E5 duration check (which still uses the
  fixed minimum) to judge; a high still open at the bound is not replaced by a
  shorter one but degrades as `no_gate_fall`;
- the poll-edge uncertainty counts only the kept window's edges.

So a sync-line high during reset or boot that is shorter than the plan accepts
neither ends the capture nor displaces the real window. A reset-time pulse of
3.33 s on GPIO 29 was observed on an Apollo510 EVB [M] (§11). A boot-time high
at least as long as the plan accepts, or any high when no window is known, is
still only caught afterwards, by the C-E5 warning and, for dedicated firmware,
the terminal arbitration (C-I2).

**C-S3.** With shared firmware over USB CDC in LS, S5 waits for DTR while the
host waits for READY (S8) before asserting DTR (S9), so that combination
cannot complete. Preflight rejects it (shared firmware, USB CDC, lock-step
resolved true) and names the alternatives: free-running, dedicated firmware
or another transport. Not reproduced on hardware.

## 3. Host wait bounds

| Bound | Formula | Starts |
| --- | --- | --- |
| `D` (auto) | Dedicated, any probe: `8.0 + W + 6.0`. Shared, any probe: `8.0 + profiled run + clean run + 6.0`. Capped at 30 s; 30 s when there is no estimate. An estimate exists only when the PMU result carries cycles, and the dedicated branch needs both `N` and `a`; it is used only when `power.duration_s` is unset and the estimate is below the configured bound. | n/a |
| `F`, LS | `max(D, L + 2.0)` | GO |
| `F`, FR | `max(D, L + 2.0 + 8.0 + P)` | reset return |
| `F`, window unknown | `D` | as above |
| `R` (LS) | `max(D, 8.0 + P + 2.0)` | after the 0.5 s grace |
| `rise_due` (classification only) | FR: `8.0 + P`; LS: none | reset return |
| Terminal collect [U] | `max(2, min(10, F/10))` | after capture |

**C-W1.** `F` must cover everything the firmware does between the start of the
wait and the gate fall:
- LS: S11 to S15, which is pre-window setup plus the window;
- FR: S5 to S15, which is boot, init, warm-up, setup and the window.

For counted probes this holds if the 8 s boot allowance and the 2 s headroom
hold. Both are [E].

For busy_loop in FR, `P` prices the firmware's warm-up inferences from the
profiled per-inference cycles (#302 follow-up 3).

In LS, busy_loop calibration and a second STIMER settle run after GO (S12),
each up to 1 s cold [D]. For busy_loop `L` is `1.25·W`, so `F` leaves
`0.25·W + 2` s beyond `W`, which covers both settles; C-W1 holds there.

**C-W2.** `R` must cover S5 to S7, which is boot, init and warm-up (and, for
shared firmware, the core clock probe and any attach wait in S5). It depends
on the same [E] allowance.

**C-W3.** A configured `D` above the derived minimum is kept. A lower one is
raised:
- when `F` exceeds `D`, the raise is logged, at WARNING if `power.duration_s`
  is set explicitly and at INFO otherwise;
- when `R` exceeds `D`, the raise is silent.

`D` never changes the window the firmware runs.

**C-W4.** `F` must also cover a window that runs longer than planned. A
stalled clean-window reference reads `a` low, which sizes `N` too high, so the
real window runs longer than `W`. When the profile window stalled, `L` and the
warm-up term use `a` stretched by `1/(1 - u)`, where `u` is the stall's
understatement lower bound, capped at 0.9 [D]. A stall report with an unknown
total gives `u = 0` and an inconsistent one (more affected iterations than
ran) is ignored, so neither stretches. The C-E5 duration check still
compares against the planned `W`.

## 4. Published intervals

| Interval | Brackets | Excludes | Published as |
| --- | --- | --- | --- |
| Firmware gate (dedicated) | STIMER, from just before gate rise to just after gate fall: both GPIO writes and the window | settle, INA228 arm and read | `power.terminal.gate_elapsed_us` |
| INA228 accumulation (dedicated with INA228) | from after the accumulator reset to just before the register reads | the reads, which latch a few I2C transactions later | `power.on_device_summary.duration_us` |
| Firmware whole window (dedicated) | from after the STIMER settle to after the INA228 reads: setup, gate and reads | settle, calibration, warm-up, READY/GO | `power.terminal.elapsed_us` |
| Instrument gate | sum of stats-packet durations whose time falls within the chosen rise and fall. Edges come from the GPI stream, taking the last qualifying segment, with snapshot polling as the fallback. | anything outside the gate. Packet-quantized: about 1 ms at 1 kHz. | `summary.json` `power.capture_duration_s` and `profile_results.json` `power.duration_s`, for a valid gated result only (C-I3) |
| Capture window | host monotonic, from after the instrument is opened to the result | nothing: includes reset, READY, GO, the wait and teardown | `power.capture_window_s` (`summary.json`) |
| Gate bound | the value of `F` | n/a | `power.observation.observation_deadline_s` (`profile_results.json` only) |
| READY wait | host time from after the grace to READY qualified | reset and grace | `power.sync.ready_wait_s` (`summary.json`) |
| Reset | duration of the reset primitive and any power cycle | n/a | `power.target_lifecycle.timings_s` (`summary.json`; keys `reset`, `power_cycle`) |
| Edge timing (successful gated result only) | host monotonic: capture start to rise, capture start to fall, and wait start (C-S1) to rise | n/a | `power.sync_timing_s` (`summary.json`): `capture_to_gate_rise_s`, `capture_to_gate_fall_s`, `go_release_to_gate_rise_s` |

**C-I1.** Dedicated firmware: `gate ≤ accumulation ≤ whole window`. The
terminal parser enforces all three orderings.

**C-I2.** The instrument gate and the firmware gate agree within 1 %
(`EXTERNAL_WINDOW_CLOCK_TOLERANCE`) [D] or an absolute slack, whichever is
larger (`tolerance_s = max(reference·0.01, slack)`). The slack
(`external_observer_slack_s`) is `2/stats_rate_hz` plus the larger of two poll
intervals and, for snapshot edges, the measured poll uncertainty. The margin is
set above the disagreements recorded in #142, #181 and #195. Validity arbitration
decides which reading stands.

**C-I3.** `summary.json` `power.capture_duration_s` is the instrument gate
only when the result is a valid gated measurement:
- in a degraded capture (C-E3), or in the ungated fallback (§2), it is the
  length of the free-form capture;
- read it together with the result's measurement scope and integrity;
- `timing.capture_duration_s` is the PMU capture.

These names are kept for compatibility, and this contract is the reference for
what they mean.

## 5. Error precedence

**C-E1.** If a start step (reset, READY, DTR or GO) fails:
- the capture ends at once, and the gate bound is not waited out;
- the step's own exception surfaces unchanged, with its cause kept, ahead of
  any error the capture would otherwise raise;
- a replaced capture error is hidden as context.

**C-E2.** The capture stage passes any heliaPROFILER error through unchanged:
a `PowerError`, and the `CaptureError` from a failed J-Link reset or USB CDC
port open, keep their own message and hint. It wraps any other exception in a
`PowerError` whose hint names the instrument.

**C-E3.** No usable window, but stats packets arrived: the capture returns a
degraded result carrying a `gate_failure` classification, with no
`sync_timing_s`. Dedicated firmware then collects the terminal, whose own error
surfaces next.

**C-E4.** No usable window and no stats packets: the classifier's
`PowerError` surfaces.

**C-E5.** A gate duration outside the capture-time band
`W ± max(a/2 if N>1, 2/stats_rate_hz, W·tol)` is a warning at capture time.
Validity arbitration decides the run's status.

**C-E6.** Terminal errors surface after a successful or degraded capture:
- requested count differs from the host plan;
- status not ok (with the `stimer_dead` hint when that is the cause);
- no record, or a malformed or incomplete record;
- gate not lowered.

Zero energy, reversed wiring and accumulator overflow are terminal errors only
in internal mode (INA228) and do not apply to the external path this contract
covers.

How a firmware failure surfaces depends on the mode:

| Firmware failure | LS | FR |
| --- | --- | --- |
| Init or model failure before READY (dedicated) | READY timeout after `R`. The firmware drops the state line and parks, and the terminal record holding the cause is never collected. | Degraded `no_gate_rise` after `F`, then the terminal error |
| `stimer_dead` before the gate | Degraded `no_gate_rise` with the wiring hint after `F`, then the terminal error | Degraded `no_gate_rise` after `F`: the lock-step-suspect hint if lock-step wiring is configured, otherwise the wiring hint. Then the terminal error. |
| GO later than the firmware's ~3 s GO wait | The firmware free-runs. The snapshot poller discards a rise before `go_signaled`; on JS110 that gives `no_gate_rise`. On JS220/JS320 the GPI stream can still select the window: a valid `gpi_stream` result with no early stop and no edge timing published. | n/a |

## 6. Hint claims

**C-H1.** A hint must list every cause its inputs cannot rule out. It may name
a single cause only when its inputs rule out the others, and it says "likely"
when it ranks them.

Every `no_gate_rise`, `no_stats_window` and `no_gate_fall` hint gains a
poll-failure suffix when GPI snapshot reads failed during the capture
(`power.gating_diagnostics.gpi_poll_failures` > 0). The READY-timeout hint
does not: a failed read does not advance the READY sample count, so the wait
simply times out.

| Hint | Claims | Meets C-H1 | Missing cause and item |
| --- | --- | --- | --- |
| READY timeout | wiring, reset strategy, firmware not parked at the sync wait | no | A dedicated init failure (the terminal is not collected), the shared USB CDC ordering (C-S3, #302), and failed GPI reads. Candidate for the capture follow-up PR. |
| Stage wrapper | the instrument is not connected, for an exception that is not a heliaPROFILER error | yes | Probe and CDC errors keep their own hint (C-E2). |
| `no_gate_rise`, bound exhausted | the bound likely ended before the window was due | yes | FR only. Reachable only when the window is unknown, because a known window makes FR `F` at least `L + 10 + P`, which exceeds `rise_due`. |
| `no_gate_rise`, lock-step suspect | the likeliest cause is a free-running window racing the poller, with wiring as the fallback | no | Firmware that never reached the window (init failure, `stimer_dead`). Candidate. |
| `no_gate_rise`, wiring | wiring, the wait state or reset | no | `stimer_dead`, and a GO later than the firmware's GO wait. Candidate. |
| `no_stats_window` | host timestamps did not overlap the stats timeline | no | The only pulse was shorter than the 1.0 s minimum (C-S2). Candidate. |
| `no_gate_fall` | a hang when high longer than `L`; a bound too short when shorter | yes | |
| Stalled reference warning | the fixed count will run longer than the planned window | yes | |

## 7. Lock-step and free-running

| Aspect | LS | FR |
| --- | --- | --- |
| Gate wait starts | after GO | after the reset returns (after the DTR open for shared USB CDC) |
| `F` | `max(D, L + 2)` | `max(D, L + 2 + 8 + P)` |
| READY wait | `R` | none |
| Reset race | the snapshot poller discards edges before `go_signaled`, and the firmware is parked at GO; the JS220/JS320 GPI stream is not phase-gated (C-S2) | the firmware runs from reset. A window already high when the phase switches is missed by the poller, and a slow reset primitive narrows the margin (#114). A boot-time high of at least the minimum is accepted (C-S2). |
| Init failure surfaces as | READY timeout | degraded capture, then the terminal error |
| `go_release_to_gate_rise_s` | GO to rise | wait start to rise |

## 8. Constants

| Constant | Value | Mark | Evidence |
| --- | --- | --- | --- |
| `BOOT_SETTLE_S` | 8.0 s | E | Raised from 4.0 in #23 (a5991054) as a reset, bootloader and init allowance; no measurement cited. Current comment: `power/diagnostics.py` "Reset, secondary bootloader and firmware init before the clean window can start." |
| `FALL_WAIT_HEADROOM_S` | 2.0 s | U | Introduced in #367; no derivation recorded there |
| Stage estimate margin | 6.0 s | U | |
| Warm-up reps floor (host) | 3 | E | Mirrors the shared firmware's floor; dedicated firmware uses `max(1, warmup)` |
| GPI poll sleep | 0.004 s | U | |
| Post-fall guard | 0.15 s | U | |
| Reset grace (LS), `_LOCKSTEP_RESET_GRACE_S` | 0.5 s | U | |
| READY qualification | 3 samples | U | |
| GPI read timeout | 0.5 s | U | |
| Poller join | 1.0 s | U | |
| Firmware GO wait | about 3 s | U | |
| Default capture bound | 30 s | U | |
| Minimum qualifying gate | 1.0 s | U | |
| Window target | 5000 ms | U | Qualitative rationale only |
| Drift plausibility | 0.15 | D | Set above up to about 12 % observed on a cold AP510 first run after idle (#181) |
| Counted window tolerance | 0.10 | U | |
| busy_loop window tolerance | 0.25 | E | Reasoned from the calibration band |
| STIMER settle deadline; band `wire.STIMER_SETTLE_TICKS` (245..410 ticks) | 1 s | D | Set at about 1.5 times a 400–650 ms cold transient measured on an Apollo4 Blue Plus (#124); not measured on Apollo5 |
| Instrument/firmware gate agreement | 1 % | D | Set above the disagreements recorded in #142, #181 and #195. Bench [M] (§11): 150 JS320 gates against firmware DWT-cycle windows agreed within 0.02 % to 0.09 %, from standalone firmware rather than the hpx terminal |
| JLinkExe timeout | 15 s | U | |
| SWPOI reset script sleeps | 2 × 1 s | U | |
| Power-cycle off / settle | 0.5 / 2.0 s | U | |
| CDC re-enumeration floor / poll / timeout | 0.5 / 0.1 / 15 s | U | |

## 9. Open items

| Item | Clauses | Status under this contract |
| --- | --- | --- |
| #302 follow-up 3: busy_loop warm-up not budgeted (FR) | C-W1, C-W2 | resolved: `P` prices busy_loop warm-up from the profiled cycles |
| #302 follow-up 4: stalled reference | C-W4, C-H1 | resolved for the external gated capture: the fall wait stretches by the stall's understatement bound, and the planner text says the window runs long. Internal (INA228) mode's terminal wait is not stretched. |
| #302 stage-hint follow-up | C-E2, C-H1 | resolved: heliaPROFILER errors pass through the capture stage unchanged |
| #302 (from #373): shared USB CDC with lock-step | C-S3 | resolved: rejected at preflight. #373 was closed as not planned and folded into #302. |
| #374: live definitions only | none | outside the timeline (render hygiene) |
| #376: guarded test harness | all clauses | open. Each clause needs a guarded test, and several capture tests cannot yet run under the guard. |

**Not covered by an open item.** These are
candidates for the capture follow-up PR:
- a READY timeout hides a dedicated init failure;
- `no_stats_window` for a sub-1 s pulse;
- `stimer_dead` and a late GO get the wiring or lock-step hint;
- timeline records the bench needs are not published (§10);
- a sync-line high of at least the minimum during reset or boot was accepted
  as the window; with a known plan, gates are now ranked against the plan's
  shortest accepted window (C-S2, #302);
- the current range is never restored on teardown. A capture sets `auto` at
  start and only a power-cycle reset writes `off`; that reset now restores
  `auto` on every exit from its off window, an interrupt included (#302).

## 10. Bench acceptance

For each case, a bench run records the following. Where the value is published,
the source is the result; otherwise the source is named.

| Record | Source |
| --- | --- |
| Resolved mode (dedicated or shared, LS or FR, probe) | the run configuration and `power.sync` |
| `D` | the explicit `power.duration_s`, or the auto estimate recomputed from §3. The raise log line gives `D` and `F` when `F > D`. |
| `F` | `power.observation.observation_deadline_s` (`profile_results.json`) |
| `R` | recomputed from §3; it appears in the READY-timeout hint only on failure |
| Reset duration | `power.target_lifecycle.timings_s` (`summary.json`) |
| READY qualified | `power.sync.ready_wait_s` (`summary.json`, after the 0.5 s grace) |
| Gate rise and fall | `power.sync_timing_s` (`summary.json`) on a successful result. On a degraded result, only the DEBUG "gate-race timeline" log lines give the detection times. |
| Firmware and instrument intervals | §4 fields |
| Error or `gate_failure` | classification, message and hint, verbatim |
| GPI read failures | `power.gating_diagnostics.gpi_poll_failures` (`summary.json`, absent when 0) |

**Not published:** absolute times for reset return, READY qualified and GO,
and `D`, `R` and edge timing on a degraded result. The bench derives what it
can from the records above, and says which clauses it therefore could not
check.

**Derived measurements:**

| Measurement | How derived | Checks |
| --- | --- | --- |
| Reset to READY | reset duration (`reset`, plus `power_cycle` when used) plus the 0.5 s grace plus `ready_wait_s` | the 8 s boot allowance; C-W2 |
| GO to rise | `go_release_to_gate_rise_s` in LS | the 2 s headroom; C-W1 LS |
| Wait start to rise | `go_release_to_gate_rise_s` in FR | `8 + P`; C-W1 FR |

A clause passes when every case meets it with the measured numbers, which then
replace the [E] marks above.

## 11. Bench observations

Apollo510 EVB with a JS320 on the MCU rail, 2026-10-02 and 2026-10-03.
These runs used standalone firmware and host scripts that call
`capture_gated` directly, not `hpx profile`. They bear on the clauses named,
but they do not exercise the hpx reset, READY/GO or terminal paths.

| Observation | Bears on | Mark |
| --- | --- | --- |
| 150 GPIO-gated windows of 3.9 to 5.1 s: the instrument gate exceeded the firmware's own DWT-cycle window by 0.018 % to 0.085 % (mean 0.057 %), with no outlier. | C-I2 | M |
| After a flash and reset, GPIO 29 read high for 3.33 s, ending about 4 s after the flash returned. A capture armed at that moment took it as the first window, which shifted every later window by one; a check against the firmware's window index caught it. | C-S2, §9 | M |
| `capture_gated` returns one window per capture (`min_high_windows` must be 1). Twelve back-to-back windows 2 s apart were captured by re-arming it after each fall. | §2 | M |
| The JS320 current range was found `off` (no current at the MCU rail; J-Link attach timed out) after a host process holding the instrument was killed. The capture path never writes `off`, so the kill alone does not explain it; the instrument's own state is the likelier cause and is not established. Setting `auto` restored the rail. | §9 (range not restored) | M (state), U (cause) |
| A second `pyjoulescope_driver.Driver` in a process where `capture_gated` already initialised the shared driver fails with `IN_USE`. | §2 S3 | M |
