# Wire protocol

<!-- GENERATED FILE — do not edit by hand.
     Source: src/helia_profiler/wire/
     Regenerate: uv run python tools/gen_wire_protocol_reference.py -->

Every line the profiler firmware puts on a transport, and the one command the
host writes back, generated from the registry in `helia_profiler.wire`.
`tests/contracts/test_wire_protocol.py` renders the firmware templates across a
matrix of SoCs, transports, engines and binaries and asserts that what they
emit is exactly what this page says.

## How to read the tables

* **Scope** — which engines emit the token. `all` means every engine.
* **Condition** — the firmware render gate. `always` means the token's source
  is in every build within its scope. This is a *template* condition: the
  dedicated power binary compiles `hpx_printf` to a no-op, so a token whose
  source is present there still prints nothing (see the power-binary rule
  below).
* **Consumer** — what on the host reads it. `unconsumed` means firmware emits
  it and nothing currently reads it: honest provenance, not a bug.
* **Criticality** — `protocol` breaks capture or a verdict, `metric` silently
  degrades a reported number, `diagnostic` costs only diagnosability.

## The power-binary rule

`hpx_printf` compiles to an empty function when the dedicated power binary is
built, so that binary emits **only** the power terminal record. Every
transport-stream token below is either excluded from its source or present and
silent there.

## Sentinels

Frame the stream. The parser ignores everything before the start sentinel and stops at the end sentinel.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_START` | all | `not power_only` | `transport_control` | `protocol` | Opens the profile stream; the parser ignores every line before it. Line: `--- HPX_START ---`. Absence is a hard CaptureError; its arrival time is the TimingInfo.hpx_start_latency_s reference for every transport. |
| `HPX_END` | all | `not power_only` | `transport_control` | `protocol` | Closes the profile stream and ends line collection. Line: `--- HPX_END ---`. Missing END within the last 10 lines is a truncation warning, not an error — a lossy transport may drop it after valid data. |
| `HPX_PRESET` | all | `not power_only` | `transport_control` | `protocol` | Opens one PMU pass; the name selects the preset the rows belong to. Line: `--- HPX_PRESET <name> ---`. Value: `pass name (no spaces)`. |
| `HPX_ITER` | all | `not power_only` | `transport_control` | `protocol` | Opens one profiled iteration; the CSV body follows. Line: `--- HPX_ITER <n> ---`. Value: `decimal iteration index`. A stream with iterations but no preset marker is legacy; the parser auto-creates a '_default' preset for it. |
| `HPX_POWER_TERMINAL_START` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Opens the power binary's terminal record. Line: `--- HPX_POWER_TERMINAL_START ---`. Power binary only. Delivery differs by transport: on RTT the whole record is written once and the firmware parks, while UART, SWO and USB CDC retransmit it in full every 250 ms forever (the binary never terminates and the host may attach late). The envelope parser's find-a-complete-start/end-pair-and-discard-partials loop exists for exactly that repetition. |
| `HPX_POWER_TERMINAL_END` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Closes the power binary's terminal record. Line: `--- HPX_POWER_TERMINAL_END ---`. Power binary only. |

## Handshake lines

Valueless lines that coordinate host and firmware around attach and model upload.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_READY` | all | `not power_only and transport != usb_cdc` | `transport_control` | `protocol` | Firmware liveness line printed before the start header. RTT prints it once and uses it as the attach gate; SWO/UART print 40 disposable copies to keep the link warm while the host attaches; USB CDC prints none and polls DTR instead. |
| `HPX_GO` | helia-rt, tflm | `weights_region == psram and transport == rtt and not power_only` | `transport_control` | `protocol` | Host->device release after the model is written into PSRAM. Direction: host to device. **No template emits this.** The firmware never compares the bytes — it waits for six characters on RTT down-channel 0 — so the token appears in the rendered source only as a comment. |

## Metadata keys

`HPX_<KEY>=<value>` lines under the start header, parsed by `^HPX_(\w+)=(.+)$` into a key/value map with the key lower-cased.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_VERSION` | all | `not power_only` | `diagnostic` | `diagnostic` | Protocol version of the emitting firmware. Value: `int (currently 1)`. Compared against HPX_PROTOCOL_VERSION and then discarded: a mismatch logs a warning and never reaches summary.json. |
| `HPX_ENGINE` | all | `not power_only` | `unconsumed` | `diagnostic` | Which inference engine produced this build. Value: `engine id with hyphens underscored (helia_aot)`. |
| `HPX_EXTREME_MODE` | all | `not power_only` | `unconsumed` | `diagnostic` | Whether the extreme low-power mode was actually engaged. Value: `0 \| 1`. Resolved at render time: requires arena and weights both in TCM. |
| `HPX_ITERATIONS` | all | `not power_only` | `unconsumed` | `diagnostic` | Profiled iterations per pass, as compiled in. Value: `int (Jinja literal)`. |
| `HPX_WARMUP` | all | `not power_only` | `unconsumed` | `diagnostic` | Warmup iterations per pass, as compiled in. Value: `int (Jinja literal)`. |
| `HPX_NUM_PRESETS` | all | `not power_only` | `firmware_meta` | `diagnostic` | Number of PMU passes this build runs. Value: `int`. |
| `HPX_PRESETS` | all | `not power_only` | `firmware_meta` | `diagnostic` | Names of the PMU passes, in execution order. Value: `comma-separated names`. |
| `HPX_POWER_SYNC` | all | `not power_only` | `unconsumed` | `diagnostic` | Whether the build brackets its clean window with the GPIO gate. Value: `gpio \| none`. |
| `HPX_SYNC_GPIO` | all | `not power_only`; runtime: if constexpr (kPowerSyncEnabled) | `unconsumed` | `diagnostic` | Pin number carrying the power gate signal. Value: `int pin number`. |
| `HPX_SYSTEM_CLOCK_HZ` | all | `always` | `firmware_meta` | `metric` | Ground-truth SystemCoreClock as configured on the device. Value: `Hz`. Checked against the platform registry (>5% divergence warns) and used by the clean-window clock-rate validity check: it is the expected-rate term of PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW, with HPX_CLEAN_DWT_RATE_CYC and HPX_CLEAN_DWT_RATE_US. |
| `HPX_BURST_AVAIL` | all | `apollo3_burst` | `unconsumed` | `diagnostic` | Whether Apollo3 burst mode was available on this part. Value: `0 \| 1`. |
| `HPX_BURST_ENGAGED` | all | `apollo3_burst` | `unconsumed` | `diagnostic` | Whether Apollo3 burst mode was actually engaged. Value: `0 \| 1`. |
| `HPX_HEARTBEAT_ENABLED` | all | `not power_only` | `unconsumed` | `diagnostic` | Whether progress heartbeats are compiled in. Value: `0 \| 1`. |
| `HPX_HEARTBEAT_EVERY_N_OPS` | all | `not power_only` | `unconsumed` | `diagnostic` | Heartbeat cadence in operators. Value: `int (0 = disabled)`. |
| `HPX_HEARTBEAT_EVERY_MS` | all | `not power_only` | `unconsumed` | `diagnostic` | Heartbeat cadence in milliseconds. Value: `int ms (0 = disabled)`. |
| `HPX_MODEL_SIZE` | executorch, helia-rt, tflm | `not power_only` | `firmware_meta` | `diagnostic` | Size of the embedded model blob. Value: `bytes`. heliaAOT compiles its weights in and reports no model size. |
| `HPX_ARENA_SIZE` | executorch, helia-rt, tflm | `not power_only` | `firmware_meta` | `diagnostic` | Configured arena size. Value: `bytes`. For ExecuTorch this is the summed arena size — planned + method + temporary; I/O buffers are separate keys — so the figure is comparable with TFLM's single-arena number (#165); the per-arena breakdown stays in the host's build record. heliaAOT reports HPX_ARENAS_BOUND instead. |
| `HPX_ALLOCATED_ARENA` | helia-rt, tflm | `always` | `firmware_meta` | `metric` | Arena bytes TFLM actually used after AllocateTensors(). Value: `bytes`. |
| `HPX_INPUT_SIZE` | executorch, helia-rt, tflm | `always` (helia-rt, tflm); `not power_only` (executorch) | `firmware_meta` | `diagnostic` | Byte size of the model's (first) input tensor. Value: `bytes`. |
| `HPX_OUTPUT_SIZE` | executorch, helia-rt, tflm | `always` (helia-rt, tflm); `not power_only` (executorch) | `firmware_meta` | `diagnostic` | Byte size of the model's (first) output tensor. Value: `bytes`. |
| `HPX_NUM_TENSORS` | helia-rt, tflm | `always` | `firmware_meta` | `diagnostic` | Tensor count of subgraph 0. Value: `int`. |
| `HPX_NUM_INPUTS` | helia-aot, helia-rt, tflm | `always` | `firmware_meta` | `diagnostic` | Number of model inputs. Value: `int`. |
| `HPX_NUM_OUTPUTS` | helia-aot, helia-rt, tflm | `always` | `firmware_meta` | `diagnostic` | Number of model outputs. Value: `int`. |
| `HPX_ARENAS_BOUND` | helia-aot | `not allocate_arenas and arena_regions` | `unconsumed` | `diagnostic` | Number of external arena regions bound before model init. Value: `int`. |
| `HPX_PSRAM_SIZE_BYTES` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Total PSRAM size reported by the driver. Value: `bytes`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_CLOCK_HZ` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `metric` | Configured PSRAM clock. Value: `Hz`. Presence of HPX_PSRAM_CLOCK_HZ is what makes the parser build a PsramInfo at all; the other seven default to 0 inside it. |
| `HPX_PSRAM_CAPABILITIES` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Driver capability bitfield. Value: `bitmask`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_STATE` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Driver state enum after bring-up. Value: `int enum`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_LAST_INIT_STATUS` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Status code of the last init attempt. Value: `int status`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_XIP_ENABLED` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Whether execute-in-place is on. Value: `0 \| 1`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_TIMING_STATUS` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Timing-scan result. Value: `int status`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_RXDQS_DELAY` | helia-aot, helia-rt, tflm | `psram_needed and not power_only` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram and not power_only` (helia-aot) | `firmware_meta` | `diagnostic` | Chosen RXDQS delay tap. Value: `int`. Lands in summary['psram'] via PsramInfo. |
| `HPX_PSRAM_ARENA` | helia-rt, tflm | `arena_region == psram` | `unconsumed` | `diagnostic` | Base address and size of the arena placed in PSRAM. Value: `0x<addr>,<bytes>`. |
| `HPX_PSRAM_READY` | helia-rt, tflm | `weights_region == psram` | `transport_control` | `protocol` | PSRAM is up and awaiting the host's model upload at this address. Value: `0x<addr>,<bytes>`. The RTT transport blocks on this line, writes the model over SWD, then releases the firmware with HPX_GO. |
| `HPX_PSRAM_ARENA_REGION` | helia-aot | `not allocate_arenas and arena_regions with placement == psram` | `unconsumed` | `diagnostic` | One heliaAOT arena region placed in PSRAM. Value: `<region_id>,0x<addr>,<bytes>`. |
| `HPX_CLEAN_WINDOW_PROBE` | all | `busy_loop_probe` | `unconsumed` | `diagnostic` | The clean window ran a busy loop instead of inferences. Value: `busy_loop`. The host reaches the same conclusion from its own config, so this line is currently informational only. |
| `HPX_CLEAN_ITER` | all | `clean_window_trace and transport not in (swo, uart)` | `unconsumed` | `diagnostic` | Per-iteration trace marker inside the clean window. Value: `int iteration index`. Opt-in diagnostic; excluded on SWO/UART because printing inside the window would contaminate the measurement. |
| `HPX_CLEAN_INFER_COUNT` | all | `always`; runtime: clean_count > 0 | `firmware_meta` | `metric` | Inferences completed inside the gated clean window. Value: `int`. Divides the gated energy, so losing it downgrades power results to whole-capture estimates. The clean_window_begin heartbeat's iters= is the host's fallback for exactly that case. |
| `HPX_CLEAN_INFER_TOTAL_CYCLES` | all | `always`; runtime: clean_count > 0 | `firmware_meta` | `metric` | Total cycles measured across the clean window. Value: `cycles`. Back-derived from the STIMER measurement on the STIMER path. With HPX_CLEAN_INFER_AVG_US it feeds PROFILE_CLEAN_WINDOW_FROZEN (zero elapsed time against completed inferences); the verdict warns, so the criticality stays metric. |
| `HPX_CLEAN_INFER_AVG_CYCLES` | all | `always`; runtime: clean_count > 0 | `firmware_meta` | `metric` | Mean cycles per clean inference. Value: `cycles`. |
| `HPX_CLEAN_INFER_AVG_US` | all | `always`; runtime: clean_count > 0; on the cycle-counter path additionally SystemCoreClock > 0 | `firmware_meta` | `metric` | Mean wall time per clean inference. Value: `microseconds`. Seeds the power-window iteration count, so a stalled or zero value undersizes the next power run. With HPX_CLEAN_INFER_TOTAL_CYCLES it feeds PROFILE_CLEAN_WINDOW_FROZEN (zero elapsed time against completed inferences); the verdict warns, so the criticality stays metric. |
| `HPX_CLEAN_STALLED_ITERS` | helia-aot, helia-rt, tflm | `not use_stimer_window` | `firmware_meta` | `metric` | Clean iterations whose cycle counter did not advance at all. Value: `int (0 on a healthy run)`. Always emitted on the cycle-counter path so the host can tell a firmware that checks from one that does not. Feeds PROFILE_CLEAN_WINDOW_STALLED with HPX_CLEAN_PARTIAL_ITERS. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_CLEAN_PARTIAL_ITERS` | helia-aot, helia-rt, tflm | `not use_stimer_window` | `firmware_meta` | `metric` | Clean iterations that advanced far less than the warm reference. Value: `int (0 on a healthy run)`. Feeds PROFILE_CLEAN_WINDOW_STALLED with HPX_CLEAN_STALLED_ITERS — the two failure shapes are counted separately and neither is inferred from the other. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_CLEAN_REF_CYCLES` | helia-aot, helia-rt, tflm | `not use_stimer_window` | `firmware_meta` | `diagnostic` | Warm per-inference cycle reference the partial check compares to. Value: `cycles`. Emitted so the stall threshold is auditable from the capture rather than taken on trust. Sole input to PROFILE_CLEAN_WINDOW_CHECK_INOPERATIVE: a zero reference means no iteration could fall below the floor, so losing this key silently disables the verdict that says the partial-stall check did not run. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_CLEAN_DWT_RATE_CYC` | helia-aot, helia-rt, tflm | `not use_stimer_window` | `firmware_meta` | `metric` | Cycles the counter advanced during a fixed calibration probe. Value: `cycles`. With HPX_CLEAN_DWT_RATE_US and HPX_SYSTEM_CLOCK_HZ this feeds PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW — the only check that can see a uniform slowdown, since the in-window counters are DWT-relative and cancel under one. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_CLEAN_DWT_RATE_US` | helia-aot, helia-rt, tflm | `not use_stimer_window` | `firmware_meta` | `metric` | Duration of that calibration probe. Value: `microseconds`. Printed from the HPX_CLEAN_DWT_RATE_PROBE_US macro — one of the few cases where a compile-time constant travels on the wire. The denominator of PROFILE_CLEAN_WINDOW_CLOCK_RATE_LOW, with HPX_CLEAN_DWT_RATE_CYC and HPX_SYSTEM_CLOCK_HZ. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_CLEAN_ATTACH_WAIT_US` | helia-aot, helia-rt, tflm | `clean_window_needs_probe_attach and transport == rtt and not power_only and not use_stimer_window` | `firmware_meta` | `diagnostic` | How long the firmware waited for the debug probe before the window. Value: `microseconds`. Out of ExecuTorch's scope because its engine_clean_window block override replaces the shared window and emits none of the check keys — a template structure, not an apollo510 coincidence. |
| `HPX_PROFILED_INFER_COUNT` | helia-aot, helia-rt, tflm | `not power_only`; runtime: profiled_infer_count > 0 && SystemCoreClock > 0 | `firmware_meta` | `metric` | Instrumented inferences summed across all PMU passes. Value: `int`. ExecuTorch overrides this block empty on purpose: its invoke path is not a pure inference call, so the same keys would carry different semantics. |
| `HPX_PROFILED_INFER_TOTAL_US` | helia-aot, helia-rt, tflm | `not power_only`; runtime: profiled_infer_count > 0 && SystemCoreClock > 0 | `firmware_meta` | `metric` | Total instrumented inference time. Value: `microseconds`. |
| `HPX_PROFILED_INFER_AVG_US` | helia-aot, helia-rt, tflm | `not power_only`; runtime: profiled_infer_count > 0 && SystemCoreClock > 0 | `firmware_meta` | `metric` | Mean instrumented inference time. Value: `microseconds`. Fallback latency source when the clean window produced none. |
| `HPX_PMU_INIT_STATUS` | executorch | `always` | `unconsumed` | `diagnostic` | Status returned by nsx_pmu_init() for this pass. Value: `int status`. |
| `HPX_PMU_SELFTEST_CPU_CYCLES` | executorch | `always`; runtime: the pass selects ARM_PMU_CPU_CYCLES (event 0x0011) | `unconsumed` | `diagnostic` | Cycles observed during the PMU self-test busy loop. Value: `cycles`. Zero here means a powered-down or frozen PMU, which would otherwise produce plausible-looking all-zero layer data. |

## Indexed metadata keys

One printf format, one line per index — heliaAOT reports per-input and per-output sizes where the other engines report one static key.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_INPUT_%d_SIZE` | helia-aot | `always` | `unconsumed` | `diagnostic` | Byte size of input <i>, one line per input. Value: `bytes`. A genuine token-shape divergence: heliaAOT reports per-index sizes where TFLM and ExecuTorch report one static HPX_INPUT_SIZE. The parser stores input_0_size, input_1_size, ... and drops them. |
| `HPX_OUTPUT_%d_SIZE` | helia-aot | `always` | `unconsumed` | `diagnostic` | Byte size of output <i>, one line per output. Value: `bytes`. |

## Records

Token-then-payload lines. The space after the token defeats the key/value regex, so these reach no consumer.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_CONST_BLOB_LOADED` | helia-aot | `not allocate_arenas and arena_regions with blob_filename` | `unconsumed` | `diagnostic` | One constant sidecar blob was copied into its bound arena. Value: `region=<id> size=<bytes>`. Space-separated, so the generic key/value regex never matches it: this line reaches no host consumer at all. |

## Heartbeat phases

`HPX_HEARTBEAT phase=<phase> …` progress records. Any heartbeat refreshes the host's inactivity deadline; only `clean_window_begin` carries data the host acts on.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_HEARTBEAT phase=clean_window_begin` | all | `always` | `transport_control` | `protocol` | Announces the silent clean window before it starts. Value: `iters=<n> est_ms=<n>`. The host widens its capture deadline from est_ms and keeps iters as a fallback for HPX_CLEAN_INFER_COUNT. Every profile build's `clean_window_begin` heartbeat carries a real duration statement: infer windows announce a measured warm-inference estimate (both window modes), and busy-loop windows announce `window_target_ms` itself as a compile-time constant — the busy loop is calibrated to fill exactly that, and the iteration count drives nothing inside it (#170). The hardcoded `est_ms=0` survives only in dedicated power binaries, where `hpx_printf` compiles to a no-op and the host times the capture from its planned duration — no listener exists, and the power arm is the template's first branch in both window modes, so no power render measures anything pre-window. A runtime `est_ms=0` can still appear on an infer window if the measurement degrades (DWT frozen through every warmup by a debugger-attach transient); the host then reads 0 as 'no estimate' and keeps its flat heartbeat timeout. Byte-stream transports (RTT/SWO/UART, via collect_lines) hold an announced budget as a floor on their inactivity deadline until it expires; USB CDC raises its overall capture deadline instead, and its 300 s per-read line gap is not widened — a silent window longer than that is cut short on USB CDC regardless of the announce. Both derive the budget from window_budget_s, capped at WINDOW_BUDGET_CAP_S. A busy-loop announce carries iters=1 — the window completes exactly one busy pass, and on a lossy transport that drops HPX_CLEAN_INFER_COUNT the iters fallback feeds the gate-duration check, which a planned inference count the window never runs would fail as a duration mismatch. (Per-inference energy is never derived for busy windows — the summary omits it by probe.) |
| `HPX_HEARTBEAT phase=init` | all | `not power_only` | `diagnostic` | `diagnostic` | Firmware reached engine initialisation. TFLM/heliaRT append a 't=0' field the other engines omit. |
| `HPX_HEARTBEAT phase=allocate` | helia-rt, tflm | `not power_only` | `diagnostic` | `diagnostic` | About to call AllocateTensors(). |
| `HPX_HEARTBEAT phase=allocated` | helia-rt, tflm | `always` | `diagnostic` | `diagnostic` | AllocateTensors() succeeded. Value: `arena_used=<bytes>`. |
| `HPX_HEARTBEAT phase=model_init_done` | helia-aot | `always` | `diagnostic` | `diagnostic` | heliaAOT model_init() returned successfully. |
| `HPX_HEARTBEAT phase=infer` | all | `always`; runtime: heartbeat cadence reached (ops or elapsed time) | `diagnostic` | `diagnostic` | Progress inside an instrumented inference, between operators. Value: `pass=<n> iter=<n> layer=<n>`. Three emit sites, one format: TFLM's profiler class, heliaAOT's operator callback, ExecuTorch's end_operator hook. TFLM's lives in hpx_pmu_profiler.cc, not main.cc. |
| `HPX_HEARTBEAT phase=warmup_done` | all | `not power_only` | `diagnostic` | `diagnostic` | One pass finished its warmup iterations. Value: `pass=<n>`. |
| `HPX_HEARTBEAT phase=flushing` | all | `not power_only and transport == rtt` | `diagnostic` | `diagnostic` | Draining the RTT buffer before the end sentinel. Refreshes the host inactivity timer while the drain runs. |

## Error codes

`HPX_ERROR=<code> …`. The host raises on the first one it sees, with a code-specific hint where it has one.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_ERROR=schema_mismatch` | helia-rt, tflm | `always` | `transport_control` | `protocol` | The model's TFLite schema version is not the one firmware was built for. Value: `schema_mismatch:<found>_vs_<expected>`. Host hint: yes. |
| `HPX_ERROR=unsupported_op` | helia-rt, tflm | `always` | `transport_control` | `protocol` | An operator in the model is not registered in the resolver. Value: `kind=custom\|builtin [builtin=<n>] name=<s> index=<n>`. Host hint: yes. |
| `HPX_ERROR=missing_ops` | helia-rt, tflm | `always` | `transport_control` | `protocol` | Summary count of unregistered operators after the preflight walk. Value: `count=<n> hint=rebuild_with_op_registration`. Host hint: yes. |
| `HPX_ERROR=alloc_tensors_failed` | helia-rt, tflm | `always` | `transport_control` | `protocol` | TFLM AllocateTensors() failed. Value: `arena=<bytes> status=<n> hint=<s>`. Host hint: yes. |
| `HPX_ERROR=psram_init_failed` | helia-aot, helia-rt, tflm | `psram_needed` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram` (helia-aot) | `transport_control` | `protocol` | PSRAM bring-up failed on the target. Host hint: yes. |
| `HPX_ERROR=psram_info_failed` | helia-aot, helia-rt, tflm | `psram_needed` (helia-rt, tflm); `not allocate_arenas and arena_regions with placement == psram` (helia-aot) | `transport_control` | `protocol` | PSRAM came up but its info query failed. Host hint: yes. |
| `HPX_ERROR=bind_arena_failed` | helia-aot | `not allocate_arenas and arena_regions` | `transport_control` | `protocol` | Binding one external arena region to the heliaAOT model failed. Value: `bind_arena_failed:<status>:region=<id>`. Host hint: yes. |
| `HPX_ERROR=const_blob_psram_write_failed` | helia-aot | `not allocate_arenas and arena_regions with blob_filename and placement == psram` | `transport_control` | `protocol` | Writing a constant sidecar blob into PSRAM failed. Value: `const_blob_psram_write_failed:region=<id>`. Host hint: yes. |
| `HPX_ERROR=model_init_failed` | helia-aot | `always` | `transport_control` | `protocol` | heliaAOT model_init() returned a non-zero status. Value: `model_init_failed:<status>`. Host hint: yes. |
| `HPX_ERROR=executorch` | executorch | `always` | `transport_control` | `protocol` | An ExecuTorch runtime call failed; the stage names which one. Value: `stage=<s> error=<n> planned=<n>`. Host hint: yes. Single code for every ExecuTorch failure site. |
| `HPX_ERROR=operator_count_exceeds_capacity` | executorch | `not power_only`; runtime: the capacity was actually exceeded during the pass | `transport_control` | `protocol` | The model has more operators than the per-layer record array holds. Value: `capacity=<n>`. Host hint: yes. The firmware parks immediately after printing this, so NO CSV body follows at all — the pre-#175 claim that rows were merely truncated described a print that is unreachable (hpx_park() precedes print_layers()). |
| `HPX_ERROR=pmu_init_or_selftest_failed` | executorch | `not power_only` | `transport_control` | `protocol` | PMU init or its cycle-counter self-test failed for this pass. Value: `pass=<name>`. Host hint: yes. |
| `HPX_ERROR=stimer_dead` | all | `use_stimer_window and not power_only` (helia-aot, helia-rt, tflm); `busy_loop_probe` (executorch) | `transport_control` | `protocol` | The 32.768 kHz XTAL failed the settle-and-verify probe at hpx_stimer_init(): the STIMER window clock is dead or implausible. Emitted BEFORE the window opens so the failure is attributed to the crystal instead of completing a window into the frozen-clock check (#110). Transport binaries only — the dedicated power binary compiles printf out and reports the same failure through the power terminal's failed envelope (phase stimer_dead). Delivery is guaranteed on RTT (lossless pre-window); on SWO/UART a lost line degrades to the zero-elapsed clean-window warning. Value: `settle_us=<n> last_ticks=<n>`. Host hint: yes. |

## Warning codes

`HPX_WARN=<code> …`. Non-fatal; the run continues.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_WARN=unusual_dtype` | helia-rt, tflm | `always` | `unconsumed` | `diagnostic` | A tensor has a dtype the preflight walk did not expect. Value: `tensor=<n> dtype=<n> name=<s>`. Non-fatal; the run continues. Nothing on the host reads it — only HPX_ERROR lines are scanned. |

## Power terminal record

The dedicated power binary's entire output: a versioned envelope between its own start/end markers, optionally carrying an on-device measurement payload, preceded by monitor diagnostics the envelope parser ignores.

| Token | Scope | Condition | Consumer | Criticality | Notes |
| --- | --- | --- | --- | --- | --- |
| `HPX_POWER_TERMINAL_VERSION` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Envelope version; anything but 1 is refused. Value: `int`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_STATUS` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Whether the power run completed. Value: `ok \| error`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_REQUESTED_COUNT` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Inferences the host asked for. Value: `int`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_COMPLETED_COUNT` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Inferences actually completed inside the gate. Value: `int`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_ELAPSED_US` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Device-measured duration of the gated window. Value: `microseconds`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_FINAL_PHASE` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Last phase the firmware reached (names the failure on error). Value: `string`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_ERROR_CODE` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | Non-zero iff status is error. Value: `int`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_GATE_ASSERTED` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | The firmware raised the GPIO gate. Value: `0 \| 1`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_GATE_LOWERED` | helia-aot, helia-rt, tflm | `power_only` | `power_terminal` | `protocol` | The firmware lowered the gate again — the capture is bounded. Value: `0 \| 1`. Power binary only. Missing or malformed required fields raise PowerError. |
| `HPX_POWER_MEASUREMENT_SOURCE` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Which on-device monitor produced the measurement. Value: `ina228`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_MEASUREMENT_SCOPE` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | What the measurement covers. Value: `fixed_n_inference`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_ENERGY_NJ` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Energy accumulated across the gated window. Value: `nanojoules`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_MEASUREMENT_DURATION_US` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Measurement duration; must equal ELAPSED_US. Value: `microseconds`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_MEASUREMENT_COUNT` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Inferences covered; must equal COMPLETED_COUNT. Value: `int`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_MEASUREMENT_OVERFLOW` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | The accumulator overflowed and the energy is not trustworthy. Value: `0 \| 1`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_CHARGE_NC` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Charge accumulated across the window. Value: `nanocoulombs`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_BUS_VOLTAGE_UV` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Bus voltage sampled during the window. Value: `microvolts`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_CALIBRATION_ID` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: success && g_hpx_ina228_ok && the envelope written so far still fits the record buffer | `power_terminal` | `protocol` | Identity of the shunt/current calibration used. Value: `string`. Power binary only. Optional measurement payload: all of these appear together or not at all, and only for a successful window with valid accumulator reads. |
| `HPX_POWER_INA228_DIAG` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228` | `diagnostic` | `diagnostic` | INA228 register dump printed ahead of the envelope. Value: `0x<diag> CFG=0x<n> ADCCFG=0x<n> SHUNTCAL=<n>`. Power binary only. Deliberately outside the start marker: the envelope parser ignores pre-record lines, so monitor diagnostics can change without widening the wire contract. The host logs them at INFO. |
| `HPX_POWER_INA228_BYSTANDER_FAILED` | helia-aot, helia-rt, tflm | `power_only and power_monitor == ina228`; runtime: not ina228_required and the bystander monitor failed | `diagnostic` | `diagnostic` | A bystander INA228 failed and was dropped; the run continued. Value: `<phase>:<rc>`. Power binary only. Logged as a warning: the external capture is unaffected. The fail-phase global it prints is assigned only in the `not ina228_required` branches of the setup/arm/read sites, so a build where the INA228 is itself the measurement calls hpx_power_terminal_fail() instead and never reaches this line. |

### Power terminal key groups

The envelope's required fields must all be present or the record is rejected. The measurement fields are an all-or-none group, emitted only for a successful window read from an on-target monitor.

- **Required:** `HPX_POWER_COMPLETED_COUNT`, `HPX_POWER_ELAPSED_US`, `HPX_POWER_ERROR_CODE`, `HPX_POWER_FINAL_PHASE`, `HPX_POWER_GATE_ASSERTED`, `HPX_POWER_GATE_LOWERED`, `HPX_POWER_REQUESTED_COUNT`, `HPX_POWER_STATUS`, `HPX_POWER_TERMINAL_VERSION`
- **Optional (all-or-none):** `HPX_POWER_BUS_VOLTAGE_UV`, `HPX_POWER_CALIBRATION_ID`, `HPX_POWER_CHARGE_NC`, `HPX_POWER_ENERGY_NJ`, `HPX_POWER_MEASUREMENT_COUNT`, `HPX_POWER_MEASUREMENT_DURATION_US`, `HPX_POWER_MEASUREMENT_OVERFLOW`, `HPX_POWER_MEASUREMENT_SCOPE`, `HPX_POWER_MEASUREMENT_SOURCE`

## CSV body

The per-layer rows between two iteration sentinels carry no `HPX_` token of their own. Every engine prints the same header shape — `"Layer","Op"`, one quoted column per enabled counter, then `"overflow"` — and differs only in how the Op column identifies a layer. Counter columns fall back to the raw event id (`"0x%04lx"`) when a pass supplied no name, and parts without an Armv8-M PMU print the single cycle counter as `"ARM_PMU_CPU_CYCLES"`.

- **tflm** — rows are `<index>,<tag>,<counters...>,<overflow>`; the tag is TFLM's per-op tag string (or `?`).
- **helia-rt** — Identical to tflm — same template, same profiler class.
- **helia-aot** — rows are `<index>,<OP_TYPE>:<op_id>,<counters...>,<overflow>`; the operator id disambiguates repeated op types.
- **executorch** — rows are `<index>,OPERATOR_CALL|DELEGATE_CALL:c<chain>i<instr>,<counters...>,<overflow>`.

## Heartbeat phases

The complete phase vocabulary — see the table above for which engines emit which, and when: `clean_window_begin`, `init`, `allocate`, `allocated`, `model_init_done`, `infer`, `warmup_done`, `flushing`.

## Documented gaps

These are true of the shipped protocol and recorded rather than silently fixed; changing any of them changes wire bytes.

- Every profile build's `clean_window_begin` heartbeat carries a real duration statement: infer windows announce a measured warm-inference estimate (both window modes), and busy-loop windows announce `window_target_ms` itself as a compile-time constant — the busy loop is calibrated to fill exactly that, and the iteration count drives nothing inside it (#170). The hardcoded `est_ms=0` survives only in dedicated power binaries, where `hpx_printf` compiles to a no-op and the host times the capture from its planned duration — no listener exists, and the power arm is the template's first branch in both window modes, so no power render measures anything pre-window. A runtime `est_ms=0` can still appear on an infer window if the measurement degrades (DWT frozen through every warmup by a debugger-attach transient); the host then reads 0 as 'no estimate' and keeps its flat heartbeat timeout. Byte-stream transports (RTT/SWO/UART, via collect_lines) hold an announced budget as a floor on their inactivity deadline until it expires; USB CDC raises its overall capture deadline instead, and its 300 s per-read line gap is not widened — a silent window longer than that is cut short on USB CDC regardless of the announce. Both derive the budget from window_budget_s, capped at WINDOW_BUDGET_CAP_S. A busy-loop announce carries iters=1 — the window completes exactly one busy pass, and on a lossy transport that drops HPX_CLEAN_INFER_COUNT the iters fallback feeds the gate-duration check, which a planned inference count the window never runs would fail as a duration mismatch. (Per-inference energy is never derived for busy windows — the summary omits it by probe.)
- `HPX_VERSION` is checked against the expected protocol version and then discarded: it never reaches `FirmwareMeta` or `summary.json`.
- `HPX_CONST_BLOB_LOADED region=… size=…` looks like a metadata key but is space-separated, so the generic key/value regex never matches it and it reaches no consumer.
- `HPX_ERROR=` and `HPX_WARN=` lines also satisfy the generic key/value regex, so the parser incidentally stores the payload of the last one under the keys `error` and `warn`. Nothing reads them.
