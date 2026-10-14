# Full-fixture rail energy

Fixture API 5.1 adds an explicit GPIO-gated observation while retaining API 5.0 timing-only records. Build with `FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE, state_policy=..., power_sync=FixturePowerSync(gate_pin, ready_pin, go_pin))`. The method pins are compiled facts in the build identity and portable record. `ProfileConfig.power.enabled` and automatic board power remain unavailable to fixture builds: compilation never opens an instrument.

Create the normal request with `fixture_capture_request(build, ...)`, then use `dataclasses.replace(request, rail_power=FixtureRailPowerRequest(...))` to select the measured rail, instrument serial, GPIO reference voltage and instrument channels. The compiled pins must match. The original guard remains responsible for target identity and ongoing ownership; power also requires `verify_power_device(serial=..., rail=...)`. The station must supply this authority from trusted rail/assignment evidence.

The firmware initializes once, executes the selected warmups, signals READY, waits for GO, and gates the complete measured restore-plus-Invoke loop. Existing carry policy and complete output/status readback remain intact. A power terminal binds the selected wiring; a failed invocation cannot yield accepted energy. GPIO modules and linkage use the shared firmware render context.

One capture records one gated window through the maintained Joulescope driver. `FixtureCaptureResult.rail_energy` and `FixtureMeasurements.rail_energy` contain the instrument serial, rail, actual measured iterations, seconds, watts, amperes, joules and joules per iteration, plus a hash-pinned raw record. Scope is gross rail restore-and-invoke, subtraction is none. The raw record preserves instrument statistics (actual current/voltage/power and integrals/time metadata), aligned GPIO observations and existing integration diagnostics. These statistics are not a full-rate waveform. Driver integrity, gate edges, trace completeness, successful target count and exact image readback are required. Energy never comes from target latency.

API 5.2 adds `FixturePowerSync.protocol_version` (default 2) and
`rail_energy.completion`, a `FixtureBatchCompletion` containing
protocol version 2, the sensor counter rate and READY, GO-acknowledgement,
gate-rise, gate-fall and DONE sample IDs, bound to the same raw artifact.
The record loader retains legacy 5.1 wiring as protocol 1 without changing its
identity; a new build or rail capture refuses that legacy protocol before any
operation. New captures require protocol 2 and this completion evidence. A historical `completion=None` does not
claim protocol-v2 completion and must not be upgraded by inferring it from timing.

Protocol 2 configures idle GPIO levels after system initialization and before
model initialization or warmups. State goes high for READY, low after GO, then
high for DONE only after the successful terminal is published. The measured
restore-and-invoke gate and loop are unchanged; DONE is outside that gate.
A missed GO returns a power-handshake failure instead of free-running.
The shared driver retains both packed input streams. The same counter-based
transaction resolver controls stopping, gate integration and typed completion.
Reset pin activity and abandoned READY pulses with no measured gate remain in
the raw prefix; they are not themselves measurements. A fresh idle-to-READY
transition, acknowledgement, one unbroken gate and covered DONE must identify
exactly one complete transaction. Acknowledged gate fragmentation, multiple
eligible transactions, transitions after DONE, stream gaps or missing coverage
refuse energy. Snapshot polling is only a control-plane hint: delayed stream
callbacks cannot establish completion before their evidence arrives. No host
command counter is manufactured from callback arrival time. No forecast,
last-gate preference or independent clock tolerance establishes completion.

Power readback never resumes a halted target or waits for a later completion
after sampling has stopped. Protocol-v1 images cannot supply protocol-v2
evidence: rebuild instrumented firmware and retain the original image and result
identities. Timing-only completion polling is unchanged.

Experiment counts, repeat aggregation, comparison thresholds, calibration acceptance and rail coverage belong to the caller. Instrument GPIO reference voltage is not a substitute for measured rail voltage. No battery or whole-application energy claim follows from a rail measurement.

The runtime catalog also admits non-default AOT 0.29.0 from its exact release commit. Its empty qualification list makes no target qualification claim. The published compiler requires CORE 7.44.2; an explicitly loaded task compatibility baseline records that tuple as development overrides when it differs from the unchanged package default. Package range admission, generated firmware fit, physical execution and numerical correctness remain distinct.

Trace acceptance reconstructs the original packed UINT1 GPIO frames, including the valid sample count, little-endian bit order, channel, decimation mode/increment and clock metadata. A bounded recorder preserves those bytes; malformed, dropped or overflowed evidence cannot produce accepted energy. The JS220/JS320 adapter establishes the shared native sensor counter from the instrument stream contract, and checks the current level-preserving mode before enabling streams. It does not infer valid padding or a clock origin from frame arrival times.

Statistics must be counter-contiguous at the same native rate and cover both decoded gate-edge brackets. Whole packet integrals are selected by a half-open midpoint rule; no partial integral is invented. The raw evidence records chosen counters, packet indices/count, integrated duration, gate duration and a conservative boundary-duration uncertainty (intersected edge-packet spans plus two gate sample periods). Fitted UTC-map changes and boundary discontinuities remain separate diagnostics rather than rescaling sample duration or hiding missing samples. Host-arrival-only axes remain unavailable for fixture energy acceptance. These checks do not establish calibration, task numerical correctness, or equivalence between a whole reset/boot capture's current and the measured inference gate.

The existing capture request `settle_seconds` bounds startup READY and measured
completion separately. READY follows initialization and all requested warmups;
the host does not shorten its caller-selected bound to a fixed startup ceiling.
The capture validator still caps each bound at 60 seconds, and ongoing ownership,
cancellation and measured-window integrity checks remain required. A READY timeout
does not by itself distinguish initialization failure from slow startup.

API 5.3 adds optional `FixtureCaptureRequest.board_power` for timing through a
configured supply. `FixtureBoardPowerRequest(driver, serial, rail)` identifies
that supply; the caller adds it with `dataclasses.replace` after the usual capture
factory. The station must authorize the selected serial/rail via
`verify_power_device`, including ongoing ownership checks. The shared power setup
runs before target attachment and flashing, and failure prevents either operation.
With no supply and no rail measurement, timing never resolves or opens a power
driver. Rail measurement uses its already-selected instrument/rail for power
setup; an explicit supply must match it. This does not add measurement to timing
or change the build identity. Compilation remains instrument-free.
