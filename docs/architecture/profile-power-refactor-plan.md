# Profile and Power Pipeline Refactor Plan

## Goals

Keep profiling and power measurement as distinct firmware products while giving
them consistent host-side orchestration, checkpoints, error boundaries, and
terminal reporting.

The design must:

- keep `hpx_profiler` and `hpx_profiler_power` separate;
- show useful progress before both firmware targets have built and run;
- preserve a linear, inspectable pipeline;
- pass typed, immutable outputs between major steps;
- keep power measurement free of transport/debug-domain overhead;
- enable diagnostics only after the measured GPIO window closes;
- recover useful status when GPIO observation fails;
- add narrow extension points without introducing a general event framework.

## Non-goals

- One firmware binary with profile and power modes.
- Multi-engine orchestration.
- Parallel profile and power execution.
- A persistent workflow engine, dependency graph, or plugin event bus.
- Making post-run telemetry a prerequisite for accepting a valid GPIO-gated
  measurement.
- Hiding build, flash, reset, or capture operations behind automatic retries.

## Runtime products

### Profile firmware

`hpx_profiler` owns PMU instrumentation and the selected capture transport. It
may consume substantially more code and RAM than power firmware. Its contract
is:

1. initialize platform, transport, model, and PMU support;
2. emit `HPX_START` and startup metadata;
3. run clean timing and instrumented passes;
4. emit structured PMU results and `HPX_END`;
5. park until reset.

### Power firmware

`hpx_profiler_power` owns the minimal clean inference loop. It excludes PMU
storage, profile CSV machinery, and transport initialization from the measured
phase. Its contract is:

1. initialize only the platform, model, timer, and GPIO synchronization needed
   for measurement;
2. announce READY through GPIO and wait for GO when lockstep is enabled;
3. raise GATE, execute the compiled fixed `N`, and lower GATE;
4. after GATE is low, initialize the configured diagnostic transport;
5. emit one terminal record and park.

Every recoverable power-firmware exit must converge on step 4. Error paths must
force GATE low before enabling diagnostic/debug circuitry.

## Host orchestration model

Retain `PipelineRunner` as the sequential executor and `PipelineContext` as the
run-scoped carrier. Reduce the context's role as an unstructured state bag by
grouping major outputs into two immutable records.

```python
@dataclass(frozen=True)
class ProfileRun:
    firmware: FirmwareArtifact
    deployment: DeploymentRecord | None = None
    result: PmuResult | None = None


@dataclass(frozen=True)
class PowerRun:
    plan: PowerRunPlan
    firmware: FirmwareArtifact | None = None
    deployment: DeploymentRecord | None = None
    observation: PowerObservation | None = None
    terminal: PowerTerminalRecord | None = None
```

`PipelineContext` continues to carry resolved platform, engine, model, and
memory information, plus `profile_run` and `power_run`. Existing path/result
fields can remain mirrored during migration and then be removed after callers
move to the grouped records.

Do not put methods that perform I/O on these records. They are immutable data
passed between stages, reports, tests, and future API entry points.

## Typed contracts

Add only the contracts needed at major boundaries.

### DeploymentRecord

Records what was actually flashed rather than inferring deployment from a path.

```python
@dataclass(frozen=True)
class DeploymentRecord:
    firmware: FirmwareArtifact
    target_id: str
    deployed_at: str
```

### RunCheckpoint

A small value emitted when a durable or user-meaningful milestone is reached.
This is not an event bus.

```python
@dataclass(frozen=True)
class RunCheckpoint:
    phase: Literal["setup", "profile", "power", "report"]
    name: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)
```

`PipelineRunner` accepts one optional `CheckpointSink` callable. `HpxConsole`
implements the default sink. Tests can collect checkpoints in a list. Stages
emit checkpoints only for useful outcomes, not every internal operation.

The initial implementation uses the equivalent `ProgressUpdate` /
`ProgressSink` names because the same typed value represents transient status
and durable checkpoints. Rendering remains a console concern:

- default: current phase, stage, stage position, high-signal status, and ETA;
- `-v`: stage timings plus durable checkpoints and outcomes;
- `-vv`: diagnostic details emitted with `min_verbosity=2` through the same
  hook.

Stages must not format Rich markup or invent elapsed percentages. They may
report real units (iterations, bytes, passes) and ETA only when backed by known
work or measured timing.

Initial checkpoints:

- platform/model resolved;
- engine prepared;
- memory placement resolved;
- profile firmware built, with section sizes;
- profile firmware deployed;
- PMU capture complete, with layer count and clean latency;
- power run planned, with fixed `N` and expected runtime;
- power firmware incrementally rebuilt;
- power firmware deployed;
- power GATE observed or GPIO fallback selected;
- terminal firmware status received;
- report paths written.

At default verbosity, checkpoints replace the inaccurate fixed-count progress
bar and provide useful status before the second firmware is built. Verbose mode
keeps stage timing lines and adds checkpoint details.

### PowerObservation

Separates instrument observation from firmware status.

```python
@dataclass(frozen=True)
class PowerObservation:
    mode: Literal["gpio_gated", "free_form"]
    result: PowerResult
    gate_rise_observed: bool
    gate_fall_observed: bool
    deadline_s: float
    integrity: Literal["valid", "degraded", "invalid"]
```

A missing GPIO edge does not immediately raise. The capture backend continues
sampling until the execution deadline and returns a free-form observation when
possible.

### PowerTerminalRecord

Represents the post-GATE diagnostic protocol.

```python
@dataclass(frozen=True)
class PowerTerminalRecord:
  version: int
    status: Literal["ok", "error"]
    requested_count: int
    completed_count: int
    elapsed_us: int | None
    final_phase: str
    error_code: int
    gate_asserted: bool
    gate_lowered: bool
```

Use a versioned, line-oriented protocol with explicit start/end markers. Keep
parsing separate from RTT/SWO/UART readers so the same record works across
transports.

## Target stage flow

```text
setup
  preflight
  ensure_board_powered
  resolve_platform
  resolve_jlink_probe
  prepare_engine
  analyze_model
  plan_memory

profile
  render_profile_firmware
  build_profile_firmware
  verify_profile_placement
  deploy_profile_firmware
  capture_profile

power (optional)
  plan_power_run
  render_power_firmware
  build_power_firmware
  verify_power_placement
  deploy_power_firmware
  capture_power_observation
  collect_power_terminal
  reconcile_power_run

report
  generate_report
```

Rendering remains distinct from building. The initial profile build must not
compile the power target. After PMU capture determines `N`, the power source is
rendered and only `hpx_profiler_power` is built. This avoids doing hidden work
before the user receives profile status and makes each artifact's provenance
clear.

`verify_power_placement` is separate because power-only linking and late
transport support can change memory use independently of profile firmware.

## Power diagnostic loop

### Firmware state machine

```text
BOOT
  -> MODEL_INIT
  -> READY
  -> WAIT_GO
  -> GATE_HIGH
  -> RUN_FIXED_N
  -> GATE_LOW
  -> DIAGNOSTIC_INIT
  -> TERMINAL_REPORT
  -> PARK
```

Recoverable failures transition to a common finalizer:

```text
FAILURE
  -> FORCE_GATE_LOW
  -> DIAGNOSTIC_INIT
  -> TERMINAL_REPORT(status=error, phase, completed_N, code)
  -> PARK
```

The finalizer must be idempotent. It must never raise GATE and must not enable
transport before GATE is low.

### Host state machine

The host arms continuous power capture before reset, observes READY/GATE when
available, and computes a deadline from the immutable plan:

```text
planned_runtime = N * reference_inference_us
execution_deadline = boot_allowance + 2 * planned_runtime + fixed_margin
```

The exact multiplier and margins remain policy constants covered by tests, not
firmware values.

Outcomes:

| GPIO observation | Terminal record | Result |
| --- | --- | --- |
| Complete gate | Success | Accept gated result; terminal record corroborates `N`. |
| Complete gate | Error/missing | Preserve samples but fail reconciliation with explicit firmware-status error. |
| Missing edge | Success | Return free-form result with degraded integrity and GPIO diagnostic. |
| Missing edge | Error | Report firmware phase/error and retain diagnostic samples. |
| Missing edge | Missing at deadline | Treat target as stuck/catastrophic or diagnostic transport unavailable. |

The report must never present free-form energy as equivalent to a valid gated
per-inference result. It may expose total trace statistics and clearly marked
degraded estimates.

## Error boundaries

Each stage raises one typed error for its ownership boundary and attaches
structured context where useful:

- engine preparation: `EngineError`;
- rendering: `FirmwareError`;
- build/deployment: `BuildError`;
- profile protocol/capture: `CaptureError`;
- instrument observation and terminal reconciliation: `PowerError`;
- output generation: `ReportError`.

Add `phase`, `artifact_role`, and optional diagnostic details to errors without
creating a second exception hierarchy. The runner records the current stage and
last checkpoint before propagating the error to the CLI.

A failure should report:

- what completed successfully;
- which artifact was active;
- the last host checkpoint;
- the last firmware phase when available;
- the immediate corrective hint.

## Extension touchpoints

Keep extension points narrow and typed:

- `CheckpointSink`: observe user-meaningful host milestones;
- `PowerTerminalReader`: read lines after GATE is low;
- `PowerTerminalParser`: parse the transport-independent terminal record;
- existing engine adapters: produce engine artifacts;
- existing power drivers: collect samples and GPIO observations.

Do not expose arbitrary before/after-stage hooks. Add a new touchpoint only
when two concrete implementations need it.

## Delivery slices

### Slice 1: Honest artifacts and early progress

- Stop building the power target in `BuildFirmwareStage`.
- Rename/clarify profile render/build/deploy stages.
- Introduce `ProfileRun`, `PowerRun`, and `DeploymentRecord` while mirroring
  legacy context fields.
- Add progress sink support and milestone console output. The typed hook,
  dynamic stage totals, phase display, verbosity filtering, and ETA rendering
  are implemented; artifact grouping and profile-only build separation remain.
- Keep existing capture behavior unchanged.

Status: progress hooks, phase/ETA rendering, profile-only initial builds,
`EXCLUDE_FROM_ALL` power-target declaration, stale-output rejection, and
artifact-only deployment boundaries are implemented. Immutable `ProfileRun`,
`PowerRun`, and `DeploymentRecord` state is now authoritative for runtime
orchestration, with strict role/provenance/order checks and stale-state
invalidation. Legacy result/artifact fields remain mirrored for reports and
the public API until Slice 4. RT KWS and AOT AD power hardware smoke tests pass.

Acceptance:

- non-power runs build only `hpx_profiler`;
- power runs show profile results before power rendering/build begins;
- existing software and profile hardware tests pass.

### Slice 2: Power observation contract

- Introduce `PowerObservation`.
- Split target preparation, instrument observation, and reconciliation inside
  the current power stage modules.
- On missing GPIO, continue to the plan-derived deadline and return free-form
  diagnostic capture rather than raising immediately.
- Preserve current valid-gate integrity checks unchanged.

Status: `PowerObservation` is implemented and authoritative in `PowerRun`.
Joulescope missing-rise/missing-fall cases retain whole-trace statistics as
explicitly degraded free-form observations when packets are available; empty
captures and duration-integrity mismatches remain hard failures. Reports,
console output, primary JSON, and hardware validation distinguish degraded
diagnostic data and suppress per-inference/TOPS efficiency metrics. Post-run
firmware terminal reconciliation remains Slice 3.

Acceptance:

- existing valid GPIO captures are numerically unchanged;
- missing-rise, missing-fall, short-pulse, and deadline tests have explicit
  outcomes;
- degraded results cannot produce unqualified per-inference energy.

### Slice 3: Deferred firmware diagnostics

- Add the common power-firmware finalizer to both interpreter and AOT templates.
- Initialize the selected diagnostic transport only after GATE low.
- Add the versioned terminal protocol and parser.
- Add `collect_power_terminal` and `reconcile_power_run` stages.

Status: versioned `PowerTerminalRecord` parsing, late RTT initialization,
fresh retained-buffer reset, atomic terminal writes, non-reset J-Link attach,
target-specific RTT symbol resolution, explicit `collect_power_terminal`
reconciliation, report/console serialization, and common recoverable-error
finalization are implemented for dedicated RTT power firmware. UART, SWO, and
USB terminal collection remain skipped until equivalent post-GATE transports
are implemented. System-initialization failures before GPIO setup remain
catastrophic and are covered by the host deadline rather than terminal status.

The terminal path now uses a separate `PowerTerminalTransport` abstraction
rather than reusing profile capture transports, because terminal collection
must never reset the target. RTT, UART, SWO, and USB CDC adapters share one
`collect(ctx, timeout_s) -> PowerTerminalEnvelope` signature. All four are
hardware-validated on AP510 with matching fixed-N execution and energy.

`PowerTerminalEnvelope` can also carry an `OnDevicePowerSummary` for future
firmware-side monitors such as INA228. The payload uses integer base units and
requires source, fixed-N measurement scope, matching duration/count, overflow
state, and optional calibration/configuration identity. Internal mode skips
host-instrument capture and obtains its compatible `PowerResult` from this
terminal payload; external mode can retain both host observation and a
secondary on-device aggregate without conflating their provenance.
The shared types, parser, pipeline branch, and report conversion are complete;
the built-in `ondevice` driver remains rejected during planning until a real
firmware monitor producer emits the required envelope. A future INA228 adapter
must implement fixed-N monitor reset/start/stop, duration/count agreement,
overflow handling, and calibration identity before enabling that capability.

Acceptance:

- power during GATE remains statistically unchanged versus the transport-free
  baseline;
- success reports requested/completed `N` and elapsed time;
- injected model-init and mid-loop failures lower GATE and report phase/code;
- disconnected GPIO with successful firmware returns degraded free-form status;
- a stuck firmware run fails at the calculated deadline.

### Slice 4: Remove migration fields and update reports

- Move reports and public API consumers to grouped run records.
- Remove mirrored firmware/result path fields from `PipelineContext`.
- Include checkpoints, deployment provenance, observation mode, terminal status,
  and integrity classification in JSON output.
- Update architecture and user power documentation.

Acceptance:

- no report schema silently changes; version new fields where needed;
- all existing CLI and API tests pass;
- profile-only and power-enabled hardware smoke matrices pass.

## Validation strategy

Every slice uses the cheapest local checks first, followed by full software
validation. Hardware checks scale with risk:

- profile-only RT and AOT smoke after Slice 1;
- AP510 + JS320 valid/missing-GPIO cases after Slice 2;
- RT and AOT terminal success/failure injection after Slice 3;
- the existing KWS and AD power matrix after Slice 4.

Power acceptance compares current, gate duration, and energy per inference to a
stored transport-free baseline. Late diagnostic initialization is acceptable
only if it occurs after GATE low and does not alter measured-window statistics.
