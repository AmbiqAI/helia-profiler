# Fixed-input fixture measurements

## Public API

Consumers import only the names in `helia_profiler.fixture.__all__`; the other
`fixture_*` modules and `_fixture_build` are internal. `FIXTURE_API_VERSION` is
`(major, minor)`: minor grows with additions, major with removals or changed
semantics, and `tests/contracts/fixture_api_v1.json` records the surface.
`source_closure()` hashes every shipped file named in `fixture_closure.txt`
(everything except the example models) and returns one digest; a consumer
recomputes it from the installed files instead of trusting the call.

API 1.1 adds a typed build path. `FixtureBuildRequest` names the build inputs
a caller chooses (fixture, method, engine and backend, arena size,
iterations, warmup, `FixturePlacement`, target, prepared runtime,
`HeliaAotOptions`, arena observation) plus the work directory, and
`build_fixture(request)` builds it. Its `intent_identity` hashes those inputs
with files as content hashes and no paths, so the same request built in two
directories has one identity. The installed profiler and engine are outside
the request: the source closure pins the profiler, and `engine_source`
records the engine.
`build_fixed_fixture(config, ...)` is unchanged. `FixtureBuild.aot_outputs`
pins heliaAOT's `<prefix>_plan.json` and `<prefix>_report.json` when the
installed heliaAOT writes them (the build first drops any an earlier build
left in the work directory), and `FixtureBuild.engine_source` records the
engine package version and, for a VCS install, its commit.
`fixture_capabilities()` reports the qualified targets and each engine's IO
dtype status.

API 1.2 adds an MCU-rail energy window: `FixtureBuildRequest.energy_gate`,
`FixtureEnergyCapture` on `FixtureCaptureRequest.energy`, and the result's
`power` and `gate_seconds`. `FixtureBuild.energy_gate` records the build's choice.
See the energy paragraph under Measurement records.

Fixture builds take pinned inputs only. `build_fixed_fixture` refuses any
module, engine or CMSIS-NN override the compatibility classifier reports,
plus `SEGGER_RTT_PATH`, `target.segger_rtt_path`, the variables CMake and
the compilers read that change compiled output (`CFLAGS`, `CPATH`,
`CMAKE_BUILD_TYPE` and the rest of `FIXTURE_REFUSED_ENVIRONMENT`), NSX's
check bypasses, and explicit compiler launchers (`auto` and disabled stay
allowed, from config or `HPX_COMPILER_LAUNCHER`).
`tests/contracts/test_fixture_environment.py` classifies every environment
read in this package and in the pinned neuralspotx, so a new one must be
refused or justified. Fixture apps
compile with `-ffile-prefix-map`, so the ELF does not record the work
directory.

`helia_profiler.fixture.build_fixed_fixture(config, fixture, method=method,
runtime=runtime, compile=True)` composes the existing platform, engine,
memory-plan, firmware-generation and NSX build stages. It never probes or
flashes. `compile=False` prepares and renders without linking.

Supply SHA256-pinned `FixtureFile` records for model/input/reference and
`FixedFixture` tensor declarations. Model analysis verifies dimensions, indices,
INT8 type and per-tensor quantization, then derives the used operator resolver.
The supported capability is one static, stateless INT8 input and output on
Apollo510 EVB LP, ATfE, SRAM arena and MRAM weights. Unsupported graphs fail
before engine preparation. Advisory batch wildcards are accepted only when
the stored shape has batch one; firmware never resizes or rewrites the model.
No model names or fixed shapes select execution.

`FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)` measures a batch including
full input restoration. `INVOKE_ONLY` restores each input outside each measured
invocation and sums the timer intervals; timer quantization applies per call.
Both use `config.profiling.warmup` and `iterations`. The intent identity binds
fixture, configuration, provider and timing scope. A compiled build identity
also binds ELF/image, dependency lock, link map, generated source hashes and
recorded toolchain provenance. Render-only results have no build identity. Reusing a work directory with
different intent fails. Historical measurements retain their original methods.

TFLM links a `PreparedUpstreamRuntime`: a pinned archive, header root
and manifest. Strict typed ingress validates provider URL/revision declarations, declared ABI,
include directories and all header hashes. Archive hashing and regular-archive
magic rejection do not verify member format, ARM attributes or ABI compatibility.
A caller that supplies its own archive must independently audit its
build/source/ABI record; manifest fields are assertions, not independent ABI evidence. Copies recheck hashes and path
containment. Provider source identities remain manifest-declared; retain the
corresponding audited source/build record.

A TFLM `FixtureBuildRequest` that names no runtime uses the archive
`hpx runtimes prepare tflm` built from the default tflm runtime record. Prepare
downloads the tflite-micro source at the record's commit and runs TFLM's own
make dry run for `cortex_m_generic`/`cortex-m55` with CMSIS-NN kernels. The dry
run fetches TFLM's pinned third-party sources and lists the translation units.
Prepare refuses a tree whose CMSIS-NN download pin is not the record's `kernels`
commit. It compiles each unit from the tree with ATfE (`ATFE_ROOT`), replacing
TFLM's `-O*` and `-ffp-mode` flags with `-O3 -ffast-math -fshort-enums
-DNDEBUG` and the newlib configuration. Sources stay relative, so the archive
embeds no host path and two prepares give the same bytes. The objects are
archived in source-list order. Make and every compile see only `PATH`, `HOME`,
`TMPDIR` and the proxy and CA-certificate variables, so the caller's environment
cannot change the selection or the flags. The make dry run runs in its own
session: when it times out or is interrupted it is stopped with every process it
started, TERM first so TFLM's download scripts remove their temporary files, then
KILL. Compiles stay in hpx's process group, so an interrupt reaches them directly,
and a compile that times out is killed. The schema-1 manifest pins the `.h` closure
of `tensorflow/`, `signal/` and `third_party/`. Preparing needs bash, GNU make 3.82 or
later (TFLM's Makefile refuses older ones, such as macOS's 3.81) and
the tools TFLM's download scripts call (among them wget, curl, unzip, patch, md5sum
and python3). Prepare checks only for make and bash; a missing script tool fails
the dry run with TFLM's own message. The record's archive was built with GNU make
4.3. GNU make 3.82 through 4.2 do not sort wildcard results, so their source order
may differ; the archive then differs from the record, which prepare reports.
Preparing TFLM is refused on Windows hosts.

heliaRT (`engine.type: helia-rt`, backend `helia`) also links a
`PreparedUpstreamRuntime`, whose manifest uses schema 2:

- `stack: helia-rt`, with providers `helia-rt` and `ns-cmsis-nn` (AmbiqAI URLs, full revisions);
- the same ABI record;
- `build.kernel_dir: helia`;
- `build.consumer_defines`, which must include `TF_LITE_STATIC_MEMORY`.

A heliaRT `FixtureBuildRequest` that names no runtime uses the archive
`hpx runtimes prepare helia-rt` built from the default helia-rt runtime record.
Prepare takes the Cortex-M55 ATfE release-with-logs library and the `.h` closure
of `tensorflow/`, `third_party/` and `signal/` from the helia-rt release the
record pins. It refuses a release whose `MANIFEST.txt` names another commit. It
writes the manifest's providers from the record: the helia-rt commit is checked
against the release, and the ns-cmsis-nn commit is the one that helia-rt
release builds with, which a test keeps equal to the baseline's ns-cmsis-nn pin.
The ABI and defines are those of the Cortex-M55 ATfE library prepare selects. An
archive whose bytes differ from the record's sha256 is reported, not refused.

The archive is staged as the local module `hpx-heliart-runtime` and aliased to
`nsx::helia_rt`. Its declared defines apply to every consumer; schema 1 keeps the
fixed upstream defines. It replaces the registry `nsx-helia-rt` and
`nsx-cmsis-nn` modules. The compiled build must prove that the dependency
lock and link map name that archive, and no other module whose name carries
`helia-rt`, `tflite-micro` or `cmsis-nn`. The engine provenance records the
prepared archive's heliaRT revision as its version and `prepared` as its variant.
The link-map check reads every directory of each linked archive or object below
the firmware app directory, and a toolchain library from its first module
directory down, so the names of directories above the app do not count.
The fixture resolver registers only the model's own operators. Ordinary heliaRT
profile firmware also registers QUANTIZE and DEQUANTIZE, because heliaRT can
need them while preparing a model. A heliaRT fixture that needs them fails with a
nonzero status before the timed loop instead of producing a wrong result.
Each engine accepts only its own stack. The rendered firmware is the TFLM
fixture: heliaRT keeps the TFLM API. AOT accepts no runtime override and
uses the ordinary AOT adapter and its internally allocated generated module.
External-arena fixture mode is rejected until separately qualified. Ordinary profiler
configuration and provider defaults are unchanged.

`FixtureBuild` pins generated sources, ELF, flat image, dependency lock and map;
its runtime manifest is absent for AOT. A compiled build refuses a flat image over
`fixture_image.MAX_IMAGE` (2 MiB) or outside the MRAM application region, the same
limits capture applies, so no plan can pin an image capture would refuse. The cap is
a policy bound well inside MRAM: every capture flashes the whole image and reads it
all back to verify it, which costs roughly 11–12 s per MB per capture on the
Apollo510 EVB. A prepared result is not a successful
build or a numerical validation result.

`helia_profiler.fixture_capture.capture_fixture(request, guard=guard)` captures
raw evidence using existing profiler flash/probe APIs. `FixtureCaptureRequest`
binds ELF/image pins, device/serial (`AP510NFA-CBR` for the supported board), output extent, load address, evidence
directory, maximum completion wait (`settle_seconds`), timing scope and optional
TFLM arena capacity.
Pass `target=build.target`: the typed canonical board/device/load-origin record.
Other devices, target declarations, custom target overlays and relocated
application origins are rejected before device operations.
The caller's `FixtureCaptureGuard.check(require_free=..., remaining_s=...)`
must enforce exclusive ownership and its bounded deadline. The mandatory
`guard.verify_target(target=..., jlink_serial=...)` must reject unless the caller
has current independent physical board-to-serial verification and ownership.
The profiler verifies supported declarations and probe enumeration; CPUID only
establishes core type, not physical board identity. A no-op verifier does not
satisfy this caller contract. Reset boots the canonical application origin;
alternate image origins and boot-selection modes are not supported. Image extents,
symbols, full readback, exact poison writes and stable halted terminal reads are
checked. After reset the host stays detached for one second, or for
`expected_duration_s` × 1.25 when the caller predicts the run (both bounded by
`settle_seconds`), so a correct prediction leaves the timed loop probe-free. It then attaches,
resumes the core if the attach left it halted (recorded as
`resumed_after_attach`), then reads the running target's status sink without
halting it until the status leaves its
poison and running sentinels or `settle_seconds` elapses. A timeout names the
stage reached (not started, before tensor allocation, warmups, timed loop; the
intermediate stages need the TFLM memory sink) and a nonzero status names the
failing firmware stage from the shared `fixture_stage.FixtureStage`
vocabulary that also renders the firmware return codes. `completion.json` records
the detached time and whether the first poll already saw completion. Each attempt preserves started, identity, completion,
binary terminal and final receipt artifacts; existing attempt directories are
never overwritten.

Transport success establishes completion, checksum and supported clock/timer
metadata; it does **not** establish numerical acceptance. The caller binds the
request to its build receipt, compares every output byte under its policy and
checks observed iteration/warmup counts against the requested method. The
seven-word timing terminal includes firmware-reported scope, which must match
the request. Failed attempts retain available raw bytes.

The optional versioned 32-byte memory terminal reports normal TFLM allocator
use after I/O access, warmups and measured calls, outside the timed interval.
AOT planned regions are not allocator observations. Neither value is a transient
peak or minimum capacity. Stack/heap peaks are unavailable here; energy is the
optional gated capture described below.

The pre-link plan includes fixture output, status, checksum, timing, timer state,
and the TFLM memory terminal in the default data region, plus the fixed input in
MRAM. Tensor extents come from validated model metadata. The normal arena and
boot-stack reservations remain; inactive PMU and transport buffers are excluded.
These are source-level reservations, not whole-ELF totals: optimization may
remove timer state, while linker alignment and other runtime objects are resolved
by the linked-image memory report.

The existing host compile gate includes representative TCN/KWS and typed fixtures
for all three fixture engines and both timing scopes. The existing real-toolchain
gate includes every engine and model, using its normal qualified
dependency-workspace requirements.

## Typed multi-tensor fixtures

`TypedFixture(model, inputs, outputs)` covers every input and output of a
static, stateless, single-subgraph model. Each `FixtureIO` pairs a
`FixtureTensor(name, index, dtype, shape, quantization)` with the pinned bytes of
that tensor: fixed input bytes, or the expected output. Inputs carry a role
(`signal` or `aux`); outputs are `signal`. Dtypes are `int8`, `int16`, `float16`
and `float32`. Integer tensors need `PerTensorQuantization` or
`PerAxisQuantization`; float tensors carry none.
`analyze_typed_fixture_model` reads the same declarations from the flatbuffer,
and the build refuses any difference in name, index, order, dtype, shape or
quantization. Every IO tensor must be named in the flatbuffer and have at least
one dimension; unnamed or scalar (rank-0) IO is refused. An IO tensor with a
single scale and zero point is read as per-tensor, even when its quantized axis
has extent 1, so declare it with `PerTensorQuantization`. A `FixedFixture` keeps
its single-INT8 rules and renders exactly as before.

`FIXTURE_CAPABILITIES` is the producer's declaration per engine and IO dtype
(`FixtureDType`), read from the runtime records on the fixture target (int8
is `a8w8`, int16 `a16w8`, float16 `fp16`, float32 `fp32`). An entry is
`qualified` when any of the engine's versions has a device pass; the records
say which. Otherwise it is what the engine's default record says. No heliaRT
entry is qualified until a device pass. The table uses these statuses:

- `qualified` means an exact device pass;
- `supported` means it builds but has no device pass yet;
- `unsupported` is refused.

The upstream TFLM runtime also refuses any model with a FLOAT16 tensor, including
weights behind DEQUANTIZE. `FixtureBuild.capabilities` records the status of
every IO dtype the build uses. A device pass so far covers one input and one
output with per-tensor quantization. A typed fixture with more tensors, or with
per-axis IO quantization (checked on the host only), reports `supported`, not
`qualified`.

Firmware restores every input before each warmup and measured call. It checks
each tensor's byte extent, and on TFLM also its type, shape and per-tensor
quantization (per-axis parameters are verified on the host only). It then copies
each output to its own DTCM sink, `deployment_output`, `deployment_output_1`, and
so on. `deployment_checksum` covers all outputs in model order. Total output
bytes may not exceed `FIXTURE_READBACK_BUDGET` (128 KiB), so every output of
every capture is read back in full. The caller compares each
`FixtureCaptureResult.outputs` file against `FixtureBuild.outputs`. For typed
builds, pass `extra_output_sizes` (every output after the first) to
`FixtureCaptureRequest`. Capture refuses an image whose typed sinks differ from
the request.

`observe_aot_arenas=True` (heliaAOT only) paints every scratch arena with `0xA5`
after model initialization, then scans it after the timed loop, outside the
timed interval. `FixtureBuild.aot_arena_scan` lists `(region id, size)`. Pass
the sizes as `arena_scan_sizes`; `FixtureCaptureResult.arena_scan` then gives
`(touched bytes, high water)` per arena. Both are lower bounds, since a kernel may
write the paint value itself. Persistent and constant arenas are not painted.
Painting touches every scratch byte just before the warmups, so configure at
least one warmup when timing matters. The option joins the intent identity only
when enabled, so existing identities are unchanged.

## Measurement records

`fixture_metrics.inspect_fixture_footprint` reads pinned ELF/image/map artifacts
through the existing host tool probes. It performs no build or device operation.
ELF file length, flat image length and PT_LOAD bytes are separate: debug metadata
and layout gaps make them different. Linked RAM includes stack reservation;
non-stack RAM includes arenas, terminals and any RAM-resident code. Heap
reservation is separate. Do not add these component rows to their parent totals.
Partial, unattributed or straddling section inventories yield null with a reason.

ATfE LLD `.text` input contributions give retained text bytes including literal
pools, not instruction-only bytes. Nested symbol rows and overlapping aliases are
not added twice. The pinned map and actual symbol inventory support attribution;
selected `arm_`/`helia_` text symbols show linked membership, not execution or an
exhaustive kernel count. Pure model-weight, runtime and kernel totals remain null
when no exhaustive partition is available. Stack/heap peaks are not instrumented.

`fixture_observation.summarize_fixture_measurements` binds the footprint map,
ELF/image and successful raw capture identity to the compiled build receipt. It
checks the captured iteration/warmup counts and timing scope. It reports current
allocator snapshots by phase, never a transient peak or tested minimum. Numerical
acceptance remains with the consumer. Both stale and wrong maps are rejected.

Optional `FixtureEnergyWindow` is an internal producer-verified association of an
existing `PowerResult` with the exact compiled image and completed firmware count.
Construct it only after image/terminal validation through the existing gated
capture protocol and independent electrical setup/ownership checks. The summary
accepts one complete valid GPIO window and a matching finite firmware interval;
free-running/degraded captures cannot become per-inference energy. Poll-based
edges require bounded uncertainty. Energy covers the stated powered domain with
no idle subtraction. Missing captures have null values and `not_captured`, not zero.

A `FixtureBuildRequest` with `energy_gate` renders firmware that drives the board's
gate pin (the profiler's sync pin: one wire, no lock-step) high for exactly the timed
loop, and records the gate's own STIMER span in `deployment_gate_ticks`. Without it
the firmware is byte-identical to a build that predates the option. A
`FixtureCaptureRequest` with `energy` arms the named Joulescope (`instrument_serial`)
before it resets the target, so the window cannot be missed, and keeps the probe
detached until the window has closed; read-back and terminal checks then run as
usual. The timed loop must last at least the instrument's one-second minimum gate:
`expected_window_s` below it is refused before the device is touched, so size the
iterations to reach it, and predict it from a latency run: the instrument plans its
accepted gate from that prediction. `settle_seconds` must hold boot, warm-up and the
window, by the same rule the profile path's free-running gated capture uses
(`gate_fall_wait_s` without lock-step); a shorter settle is refused up front. Warm-up
is the predicted run, `expected_duration_s`, less the window; without a prediction
it counts as none, and a predicted run shorter than the window is refused. A capture
whose window the instrument did not gate fails, and so does a gate shorter than the
firmware's timed span. A firmware failure before the gate also leaves no window; the
capture still reads the firmware's status and reports the failed stage. The gate covers the
whole timed loop, including the input restores that `invoke_only` latency
excludes, so energy per inference always counts one restore and one invoke. The
result carries the `PowerResult` and `gate_seconds`. The caller binds them into a
`FixtureEnergyWindow` with the build identity, `completed_calls` equal to the build's
iterations and `firmware_duration_s` equal to `gate_seconds`.

Build receipts also expose `planned_memory` from the existing memory-plan stage,
with `planned_memory_reason` when unavailable. This is a compiler/configuration
plan, not a linked measurement or allocator observation. If AOT has no complete
producer plan, the capacities-only fallback is not exported as an arena plan.
Unknown runtime or source mappings invalidate the whole AOT plan rather than
silently dropping allocations. Staged constant source and destination remain
separate physical consumers; neither is added to enclosing section totals.

`fixture_operator_timing.bind_operator_timing(build, fixture, profile)` attributes
per-layer cycles from a separate `hpx profile` run of the same model to a fixture
build. The fixture image has no per-operator hooks, so these are approximate
shares from a PMU-instrumented sibling image, never the fixture's latency. The
record is null with a reason unless the fixture is the one the build was made
from and the model hash, engine, TFLM `cmsis_nn` or heliaRT `helia` backend, compiler version, board,
LP 96 MHz clock and SRAM/MRAM placement match, the core clock measured on the
device (when the run recorded one) is within 5 % of 96 MHz, every layer has finite cycles, no
counter overflowed, the clean window ran inferences, and the per-layer sum agrees
with the clean-window cycles within 1 % (2 % below 2 ms). The prepared runtime of a TFLM or heliaRT
fixture is not selectable by `hpx profile`; `allow_runtime_difference=True` accepts the
baseline stack and labels the record. The fixture build records no engine
version or AOT code-generation options, so neither is compared; the record
carries the profile's engine version.
