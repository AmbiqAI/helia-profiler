# Fixed-input fixture measurements

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

TFLM requires explicit `PreparedUpstreamRuntime`: a pinned archive, header root
and manifest. Strict typed ingress validates provider URL/revision declarations, declared ABI,
include directories and all header hashes. Archive hashing and regular-archive
magic rejection do not verify member format, ARM attributes or ABI compatibility.
The caller must independently audit the provider archive build/source/ABI record
before supplying it; manifest fields are assertions, not independent ABI evidence. Copies recheck hashes and path
containment. Provider source identities remain manifest-declared; retain the
corresponding audited source/build record. AOT accepts no upstream override and
uses the ordinary AOT adapter and its internally allocated generated module.
External-arena fixture mode is rejected until separately qualified. Ordinary profiler
configuration and provider defaults are unchanged.

`FixtureBuild` pins generated sources, ELF, flat image, dependency lock and map;
its runtime manifest is absent for AOT. A prepared result is not a successful
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
checked. After reset the host stays detached for up to one second, then reads
the running target's status sink without halting it until the status leaves its
poison and running sentinels or `settle_seconds` elapses. A timeout names the
stage reached (not started, before tensor allocation, warmups, timed loop; the
intermediate stages need the TFLM memory sink) and a nonzero status names the
failing firmware stage. Each attempt preserves started, identity, completion,
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
peak or minimum capacity. Stack/heap peaks and energy are unavailable here.

The pre-link plan includes fixture output, status, checksum, timing, timer state,
and the TFLM memory terminal in the default data region, plus the fixed input in
MRAM. Tensor extents come from validated model metadata. The normal arena and
boot-stack reservations remain; inactive PMU and transport buffers are excluded.
These are source-level reservations, not whole-ELF totals: optimization may
remove timer state, while linker alignment and other runtime objects are resolved
by the linked-image memory report.

The existing host compile gate includes representative TCN/KWS fixtures for both
engines and timing scopes. The existing real-toolchain gate includes both engines
and models, using its normal qualified dependency-workspace requirements.

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
This normalization layer does not add a gated firmware mode or acquire an instrument.

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
from and the model hash, engine, TFLM `cmsis_nn` backend, compiler version, board,
LP 96 MHz clock and SRAM/MRAM placement match, every layer has finite cycles, no
counter overflowed, the clean window ran inferences, and the per-layer sum agrees
with the clean-window cycles within 1 % (2 % below 2 ms). A TFLM fixture's prepared upstream runtime is
not selectable by `hpx profile`; `allow_runtime_difference=True` accepts the
baseline stack and labels the record. The fixture build records no engine
version or AOT code-generation options, so neither is compared; the record
carries the profile's engine version.
