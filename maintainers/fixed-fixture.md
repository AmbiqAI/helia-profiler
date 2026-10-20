# Fixed-input fixture measurements

## Portable station setup

Use an exact producer revision that the station can obtain. For a published
revision, install its normal Git URL. Before publication, a checksum-pinned Git
bundle or wheel is a staged development handoff, not an obtainable release.
Verify the asset hash against the handoff before cloning, and keep its source revision and status
in the result provenance. Do not substitute a shared checkout or `PYTHONPATH`
for an installation.

For a staged bundle, verify and clone it into an explicit station directory:

```sh
git clone --branch "$HPX_PRODUCER_REF" "$HPX_PRODUCER_BUNDLE" "$HPX_PRODUCER_SOURCE"
git -C "$HPX_PRODUCER_SOURCE" bundle verify "$HPX_PRODUCER_BUNDLE"
```

Bundle verification runs inside the cloned repository; it cannot run in an
empty non-repository station directory. Use absolute source and bundle paths.

Set `HPX_STATION_PYTHON` to the station environment's Python,
`HPX_PRODUCER_REPOSITORY` to the published Git URL or the cloned source's
properly encoded `file:` URI, and the revision and compiler version to the
handoff's exact pins. Install through the normal package manager:

```sh
uv pip install --python "$HPX_STATION_PYTHON" \
  "helia-profiler[aot,analysis] @ git+$HPX_PRODUCER_REPOSITORY@$HPX_PRODUCER_COMMIT" \
  "helia-aot==$HPX_AOT_VERSION"
```

The explicit compiler constraint matters: the producer's supported version
range is not an exact experiment pin. Record installed versions and the normal
VCS `direct_url.json` commit. A wheel instead needs its own asset hash and
source provenance; it does not have Git-installed commit metadata. Keep the
installed public API version and `source_closure().digest` with those records.

Set `HPX_STATION_HPX` to that same environment's installed `hpx` executable
(`bin/hpx` on Linux/macOS, `Scripts/hpx.exe` on Windows). Installing with
`uv pip --python` does not activate that environment or select a bare `hpx`
command on PATH.

Prepare the Micro provider using this explicit executable and a new, explicit
`HPX_CACHE_DIR`, with `ATFE_ROOT` pointing to the station's toolchain:

```sh
"$HPX_STATION_HPX" runtimes prepare litert "$HPX_LITERT_VERSION"
"$HPX_STATION_HPX" runtimes show litert "$HPX_LITERT_VERSION"
```

The version is positional. This command fetches the pinned sources and builds
into the normal cache; no private prepared archive is needed. Keep the reported
archive hash, whether it matches the runtime record, the provider manifest and
header hashes. A match is a reproducibility result, not independent inspection
of every archive member or acceptance of a model's execution. The setup below
lists the host tools and supported platforms.

Use the normal public `FixtureBuildRequest` with `runtime=None` to consume that
prepared cache, or supply a verified `PreparedUpstreamRuntime` explicitly.
The consumer owns its normal source recipes, build receipts and reporting.
Inspect the actual linked image/map and placement against the target's bounds
before capture; an arena reservation alone does not establish linked fit or a
runtime peak. Close every native batch before serial device operations, using
the caller's existing current-board guard and exclusive lock.

## Public API

### API 5.0: measurement descriptors and compiled records

New measurement callers use `TypedFixture(model, inputs, outputs)` with complete
`FixtureIO` input/state bytes and ordered `FixtureOutput(tensor)` declarations.
No expected-output file is needed. A numerical experiment may separately retain
references and thresholds. Legacy `FixedFixture` and all-`FixtureIO` typed outputs
still validate their reference files and retain their previous fixture identities;
mixing referenced and descriptor outputs in one fixture is refused.

The producer owns persistence of a completed `FixtureBuild`:

```python
from helia_profiler.fixture import (
    dump_fixture_build, load_fixture_build, fixture_capture_request, capture_fixture,
)

# roots is an explicit mapping of stable names to station directories.
record = dump_fixture_build(build, roots=roots)
restored = load_fixture_build(record, roots=relocated_roots)
request = fixture_capture_request(
    restored, jlink_serial=serial, evidence_dir=raw_directory, settle_seconds=60,
)
result = capture_fixture(request, guard=guard)
```

`dump_fixture_build` and `load_fixture_build` verify the compiled image, ELF,
generated sources, map, dependency state/lock, complete inputs and any legacy
references. The compiled identity includes method/counts/state, target/clocks,
placement, output descriptors, provider and memory facts. Files use named-root
relative paths and content hashes. Move roots by changing the caller's root map;
do not rewrite or reseal the record. Render-only and incomplete historical
objects cannot be promoted into this compiled record. Retain historical readers
for historical receipts. The record schema is separate from the public API version.

This proves record integrity, not provenance authenticity, numerical correctness
or permission. The consumer must independently pin its selected model, inputs,
specification, producer source, method/state and expected image/record. A rewritten
self-consistent producer record does not establish the intended experiment.
`fixture_capture_request` supplies recorded image/output/placement facts only;
serial, evidence directory, bounded wait and the exact physical guard remain
caller-owned. Timing, operator-instrumented and rail images remain distinct.

`FixtureBuild.inputs`, `output_tensors`, `model`, `arena_capacity`, `clock_profile`,
`cpu_hz` and `timer_hz` carry complete producer facts. Existing `timing_scope`,
`state_policy`, `iterations` and `warmups` retain their meanings. Descriptor-only
builds have `expected=None` and `outputs=()`. Internal reset remains unsupported:
the firmware initializes once and keeps internal state across warmups and samples.
A model with internal state needs no declared policy; its build records
`state_policy=CARRY` because that is how it ran. Variable and resource state,
initializer subgraphs and multiple subgraphs are admitted on heliaAOT, LiteRT and
heliaRT alike; what a model may contain is the engine's decision.

API 5.6 adds `FixtureBuild.kernel_source` (`FixtureKernelSource`) and
`FixtureBuild.runtime_source` (`FixtureRuntimeSource`), so a consumer reads
which kernel library and prepared runtime a compiled build linked without
parsing the dependency state or the prepared-runtime manifest, whose layouts
are internal. Both are derived from those pinned files. A heliaRT or LiteRT
build states both in its manifest: the kernel is `ns-cmsis-nn` or
`arm-cmsis-nn` at the manifest's commit, and the runtime is `helia-rt` or
`tflite-micro` with its commit and archive sha256. A heliaAOT build states
the kernel as the one CMSIS-NN module its dependency state resolved, with the
lock's release tag as `version` when the lock named one, and has no runtime
source. Render-only builds have neither. Unknown facts stay `None`.

`verify_fixture_build` re-derives both from the pinned files and refuses a
record whose fields differ. The runtime `version` is the exception: it names
the runtime record built from both the runtime and kernel commits, when the
producing HPX shipped one, and runtime records change between releases. Both
fields are bound into the compiled identity, written only when known, and
refused in records that declare an earlier API, so records from API 5.5 and
earlier load unchanged with both fields `None`.

API 5.7 adds `fixture_run_identity(build, capture=None)`, which returns a
`RunIdentity`: the runtime and kernel library a build linked, by their shared
names (`CanonicalEngine`, `CanonicalKernel`); its build facts, baseline,
dependency lock, model and input hashes; how it runs; and the HPX release that
produced it. A fact the record does not state is `Unknown` with the reason,
not `None`. `build_hash` covers the flashed image, the dependency lock, the
prepared runtime archive and the model, and never the HPX release, so it
changes only when what runs changes. The lock counts by what it resolved: git
modules by URL and commit, packaged tooling by content hash (generated vendored
modules are left out; the image reflects them), so a
neuralspotx release that changes its packaged build tooling changes it too.
`observation_hash` covers a capture's
measured facts with files reduced to their content hashes. Both come from the
record alone, so a relocated or reloaded record gives the same identity.
`read_prepared_runtime_manifest` returns a prepared runtime's stack, archive
hash and providers after checking only the manifest's own hash. The record
format is unchanged; API 5.7 reads records from 5.0 through 5.6.

API 5.8 adds `RunIdentity.intent_hash`, the frozen plan's intent hash for a
run-API build and `None` otherwise, and six `RunFacts` fields: where a model
instance ends (`instance_boundary`), whether the first invocation of an
instance runs the model's one-time initializer (`first_invoke_initializer`)
and where that invocation falls (`first_invoke_window`), and the clock, rate
and statistic of the timed span (`timing_clock`, `timing_clock_hz`,
`timing_statistic`). Each adapter supplies the instance boundary and the
initializer answer; see "Timing windows by route" below. The new fields
default to `Unknown`, and the record format is unchanged; API 5.8 reads
records from 5.0 through 5.7.

API 6.0 renames the engine `tflm` to `litert`: `EngineType.LITERT` replaces
`EngineType.TFLM`, and `FIXTURE_CAPABILITIES` and every record name the engine
`litert`. The loader still accepts the 5.x API versions, but a record that
names the engine `tflm` no longer loads; read it with the HPX release that
wrote it.

API 7.0 changes the value of `RunIdentity.build_hash`, `observation_hash` and
`intent_hash` from bare hex to semantic hashes, `hpx.<kind>/<version>:sha256:<hex>`
(`_semantic_hash.py`; `helia_profiler.SemanticHash` parses them), and renames
`inputs_sha256` to `inputs_hash`, an `hpx.inputs` semantic hash. The hashed
bytes are unchanged, so the hex part is the same as before. Each remaining
`*_sha256` fact is a file's sha256, and stays bare hex like the record's own
identities. A plan, binding, build or inputs basis refuses a non-finite
float; only an observation writes one as text. The record format is
unchanged; API 7.0 reads records from 5.0 through 6.1.

`helia_profiler.build(plan, fixture, method=..., lifecycle=...)`, also reached
as `FrozenRun.build(fixture, ...)`, compiles a frozen plan for a fixture through
`build_fixed_fixture` and returns a `CompiledBuild` of the plan, the compiled
`FixtureBuild` and the `Lifecycle` it runs. heliaRT and LiteRT link the
prepared runtime the plan names, taken from the HPX cache (`hpx runtimes
prepare`) unless `runtime=` is passed. It refuses a fixture whose model is
not the plan's, asks the engine whether it implements the lifecycle,
verifies the result before returning it, and applies the lifecycle's
invocation counts (`SINGLE` runs one timed invocation
and no warmups; see [Timing windows by route](#timing-windows-by-route) for its
one-tick floor). `CompiledBuild.identity` is the build's `RunIdentity` with
that lifecycle. `save(path, roots=...)` writes one canonical JSON document
(`hpx.compiled-build`, schema version 3) holding the plan's `to_dict()`, the
lifecycle, the binding and the `dump_fixture_build` record as an object;
`load_build(path, roots=...)` (or `CompiledBuild.load`) rebuilds both halves,
resolving files under the caller's roots, so moving the record means passing
new roots. `load` and `save` call `verify()`, and every refusal, including a
malformed or unknown-version file, is a `ConfigError` naming what failed.

The binding, an `hpx.binding` semantic hash, ties a build to the plan it was
compiled from. `build()` hashes the plan's intent hash, the lifecycle, the
timing scope, state policy and power wiring (or none) the build records, its
prepared-runtime manifest hash (or none) and what it observes (the RAM
diagnostic and the scanned heliaAOT arenas). No host path
enters it, so a build relocated to another host keeps its binding.
`verify()` re-checks the build's pinned files and the plan's intent hash,
recomputes the binding, and then compares what the build records with what
the plan states: engine, board, model hash and size, toolchain name, the
compiler version among the plan's version lines, arena and weights
placement, arena size (both are the configured capacity), clock profile,
the lifecycle's invocation counts, and the runtime version and commit and
kernel commit wherever both the plan and the build state one. heliaRT and
heliaAOT compare with the plan's pins; LiteRT compares with the prepared LiteRT
record, because the plan pins the modules an ordinary profile builds from
source, which a fixture build does not link. A plan swapped into a saved file fails on
the binding; a plan paired in memory with a build of other settings fails on
the first fact that differs.

The plan fields outside its intent hash are informational: `hpx_version`,
`model.path`, `baseline.id` (the fingerprint is in the hash) and
`toolchain.unavailable_reason`. `model.size_bytes` is outside it too, so
`verify()` checks it against the built model.

Constructing `FixtureBuildRequest` performs no runtime-cache lookup. When an
interpreter request omits `runtime`, `build_fixture` looks up the already prepared
default archive and binds its manifest into the compiled intent; it does not
download or build a missing provider. Explicit runtime requests retain their
intent identity. The selected adapter stages that archive directly through the
ordinary preparation stage. The existing `build_fixed_fixture(ProfileConfig, ...)`
front door remains supported. This does not implement additional boards, clocks,
working-arena regions or provider-owned registration generation.

Consumers import only the names in `helia_profiler.fixture.__all__`; the other
`fixture_*` modules and `_fixture_build` are internal. `FIXTURE_API_VERSION` is
`(major, minor)`: minor grows with additions, major with removals or changed
semantics, and `tests/contracts/fixture_api_v<major>.json` records the surface.
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
`fixture_capabilities()` reports the supported targets and each engine's IO
dtype status.

API 2.0 removes the fixture's energy path: `FixtureEnergyWindow`, the `energy`
argument of `summarize_fixture_measurements` and the energy fields of
`FixtureMeasurements` (schema 2). The fixture measures latency, outputs and
memory; energy comes from the dedicated power firmware (`hpx profile --power`),
which owns the power floor, the reset policy and the gated window.

API 3.0 admits unquantized signed INT32 inputs, such as token indices, through
the typed fixture path. Inputs retain their exact bytes and TfLite INT32 type.
INT32 outputs remain unsupported. These input payloads are supported but not
device-qualified, and do not introduce an INT32 neural compute precision.
Complete Invoke status and every output byte still gate capture; the 128 KiB
output readback limit, timing methods and role/state restrictions are unchanged.
The API 3.0 bump follows the compatibility guard for additions to the exported
capability constant; existing request fields and dtype statuses are preserved.

Fixture builds take pinned inputs only. `build_fixed_fixture` refuses any
module, engine or CMSIS-NN override the compatibility classifier reports,
plus `SEGGER_RTT_PATH`, `target.segger_rtt_path`, the variables CMake and
the compilers read that change compiled output (`CFLAGS`, `CPATH`,
`CMAKE_BUILD_TYPE` and the rest of `FIXTURE_REFUSED_ENVIRONMENT`), NSX's
check bypasses, and explicit compiler launchers (`auto` and disabled stay
allowed, from config or `HPX_COMPILER_LAUNCHER`).
`tests/contracts/test_fixture_environment.py` classifies every environment
read in this package and in the pinned neuralspotx, so a new one must be
refused or justified. Every app, fixture or profile, compiles with
`-ffile-prefix-map`, so neither the ELF nor the flashed image records the
work directory.

`helia_profiler.fixture.build_fixed_fixture(config, fixture, method=method,
runtime=runtime, compile=True)` composes the existing platform, engine,
memory-plan, firmware-generation and NSX build stages. It never probes or
flashes. `compile=False` prepares and renders without linking.

Supply SHA256-pinned `FixtureFile` records for model/input/reference and
`FixedFixture` tensor declarations. Model analysis verifies dimensions, indices,
INT8 type and per-tensor quantization, then derives the used operator resolver.
The single-IO capability is one static INT8 input and output on Apollo510 EVB
LP, ATfE, SRAM arena and MRAM weights. Unsupported graphs fail
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

LiteRT links a `PreparedUpstreamRuntime`: a pinned archive, header root
and manifest. Strict typed ingress validates provider URL/revision declarations, declared ABI,
include directories and all header hashes. Archive hashing and regular-archive
magic rejection do not verify member format, ARM attributes or ABI compatibility.
A caller that supplies its own archive must independently audit its
build/source/ABI record; manifest fields are assertions, not independent ABI evidence. Copies recheck hashes and path
containment. Provider source identities remain manifest-declared; retain the
corresponding audited source/build record.

A LiteRT `FixtureBuildRequest` that names no runtime uses the archive
`hpx runtimes prepare litert` built from the default litert runtime record. A
station that prepared it while the engine was named `tflm` can move
`<cache>/runtimes/tflm` to `<cache>/runtimes/litert` instead of rebuilding: the
manifest names no engine and the archive digest is unchanged. Prepare
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
Preparing litert is refused on Windows hosts.

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
peak or minimum capacity. Stack/heap peaks are unavailable here.

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
model whose public inputs and outputs have static shapes. Each `FixtureIO` pairs a
`FixtureTensor(name, index, dtype, shape, quantization)` with the pinned bytes of
that tensor: fixed input bytes, or the expected output. Inputs carry a role
(`signal` or `aux`); outputs are `signal`. Dtypes are `int8`, `int16`, `int32`,
`float16` and `float32`. INT8/INT16 tensors need `PerTensorQuantization` or
`PerAxisQuantization`; float tensors carry none. INT32 is input-only and plain:
it carries no quantization. Active affine, custom or unsupported quantization details are
refused; empty metadata and informational min/max do not quantize the input.
`analyze_typed_fixture_model` reads the same declarations from the flatbuffer,
and the build refuses any difference in name, index, order, dtype, shape or
quantization. Every IO tensor must be named in the flatbuffer and have at least
one dimension; unnamed or scalar (rank-0) IO is refused. An INT8/INT16 IO tensor with a
single scale and zero point is read as per-tensor, even when its quantized axis
has extent 1, so declare it with `PerTensorQuantization`. A `FixedFixture` keeps
its single-INT8 rules and renders exactly as before.

`FIXTURE_CAPABILITIES` declares generic engine/IO support on the fixture
target from the fixture integration's explicit IO declarations. Missing or
unsupported runtime qualification records do not disable fixture execution.
It reports `supported` or `unsupported`, never qualification inherited from
another release. IO dtype does not prove a model's compute precision, operator
support, numerical acceptance or device qualification. Plain INT32 inputs remain
supported index payloads; INT32 outputs remain refused.

Query `runtime_records.qualification(name, version, board=..., clock=...,
precision=...)` for exact release evidence, including its source and basis.
Historical qualification records and the `QUALIFIED` enum remain readable.
Supported but unqualified execution remains available. Internal FLOAT16 tensors,
including weights behind DEQUANTIZE, are recorded model facts, not a blanket
fixture veto. The selected engine and its actual provider decide kernel support;
their failures propagate without conversion or fallback. Public FLOAT16 IO remains
unsupported on LiteRT. `FixtureBuild.capabilities` records public IO dtype support,
not internal compute or kernel qualification.

API 4.0 changes the exported capability constant's values, as required by the
existing public-surface version policy. This narrow reporting correction keeps
request and receipt shapes, execution guards and placement support unchanged;
it does not incorporate the separate registry API work. Pinned API 3.x consumers
must explicitly adopt the corrected reporting contract.

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

Build receipts also expose `planned_memory` from the existing memory-plan stage,
with `planned_memory_reason` when unavailable. This is a compiler/configuration
plan, not a linked measurement or allocator observation. If AOT has no complete
producer plan, the capacities-only fallback is not exported as an arena plan.
Unknown runtime or source mappings invalidate the whole AOT plan rather than
silently dropping allocations. Staged constant source and destination remain
separate physical consumers; neither is added to enclosing section totals.

`fixture.observe_operator_timing(build, fixture, profile)` reads per-operator
cycles from a separate `hpx profile` image and records their relation to a fixture
build. These are approximate sibling-image observations, never the fixture's
latency. The immutable facts carry layer cycles and shares, clean-window cycles,
signed agreement and unattributed cycles, recorded CPU frequency, runtime identity
and observed engine version. There is no comparison tolerance or acceptance verdict.

Invalid supplied provenance, overflowing/nonfinite counters and a clean window
that did not invoke the model make observations unavailable (`reason`). A missing
or conflicting model, method, compiler, board, clock or placement binding is an
`attribution_reason`: valid profile cycles remain visible, but cannot be attributed
to that fixture build. PSRAM payload/layout and state policy are not bound by an
ordinary profile. Consumers must preserve these limitations; neither a comparison
threshold nor an exploratory runtime choice overrides them.

Runtime identity is `same`, `different` or `unknown`, comparing recorded engine
source, resolved providers and effective settings after verifying pinned provenance.
Requested refs, package versions and opaque intent hashes are not source proof.
Missing source/settings, wheel installs without a VCS commit, unparsed config-file
digests and unrecorded prepared-archive settings remain unknown. Qualification is
not inferred from any of these facts.

BENCH owns comparison eligibility and layer-sum agreement tolerances. Its policy
retains the observations and records its verdict separately. API 5.0 removes the
private `bind_operator_timing` compatibility wrapper, its acceptance record and
its threshold constants. Import `observe_operator_timing` from the public facade
and apply comparison policy in the consumer. The profiler reports facts and
non-waivable integrity/attribution limitations, not a benchmark acceptance verdict.
Historical raw records and build/capture safety validation are unchanged.

### Running a compiled build

`run_build(compiled, guard=..., jlink_serial=..., evidence_dir=..., settle_seconds=...,
expect=None, expected_duration_s=None)`, or `compiled.run(...)`, captures once from a
compiled build without rebuilding. It verifies the build first, checks an `Expect` before
the capture (one readable reference of each output's size, outputs the build carries, or
LiteRT installed for a host reference), builds the request with `fixture_capture_request`,
captures with `capture_fixture` under the caller's guard, and returns a `RunResult` holding
the `compiled` build, the raw `capture`, the run's `identity` and the `verification`. A
successful capture whose iterations and warmups differ from the build's raises
`CaptureError`. The identity is `fixture_run_identity` of the build and capture, so it
carries an `observation_hash`, with `run.lifecycle` taken from the build. With an
`Expect`, a successful capture is compared through `compare_fixture_outputs`; without one,
or when the capture failed, `verification` is `None`. `RunResult.succeeded` is true only
for a `success` capture. A failed capture is returned, not raised; errors raised while
capturing propagate. A very short `SINGLE` invocation can hit the one-tick floor described
under [Timing windows by route](#timing-windows-by-route).
`examples/run_api_conformance.py` walks freeze, build, save, load and run on a
typed fixture and prints the identity, verification facts and key measurements.
Its plan states what a fixture build requires (ATfE, the `lp` clock, an SRAM
arena of explicit size, MRAM weights), the build works under the host's
`work_dir`, `build()` links the heliaRT or LiteRT runtime `hpx runtimes prepare` cached,
and the saved build names the HPX cache as a second root. Its
`PlaceholderGuard` refuses every capture; a harness passes its own guard.

### Power through the run API

The run API takes exactly the power routes BENCH measures (BENCH #111,
helia-profiler#558). Everything else is refused with its reason before
anything compiles or touches a device.

**Gross MCU-rail energy.** `build(plan, fixture, method=FixtureMethod(
FixtureTimingScope.RESTORE_AND_INVOKE, power_sync=FixturePowerSync(sync, state,
go)), lifecycle=Lifecycle.CARRY)` compiles the protocol-2 power firmware, as
the fixture route does. `_require_power_route` admits it for a heliaAOT or
LiteRT plan (`helia-aot`, `litert`) on the `apollo510_evb` under `CARRY`. A
model without internal state runs under `CARRY` unchanged; one with state
records the `carry` policy. `SINGLE`, heliaRT, ExecuTorch and other boards
(the AP510B fixture integration is timing-only) are refused, and so is a
method without `power_sync` for a plan frozen with power enabled, which
would otherwise build timing-only firmware. The build records its wiring
in `FixtureBuild.power_sync`. `build_identity` and the binding
(`_binding`) both cover the wiring, so `verify()` refuses a build whose
wiring was changed.

`run_build(compiled, ..., power=FixtureRailPowerRequest(...),
power_guard=...)` measures it. `CompiledBuild._check_power` refuses each of
these before the evidence directory is created:

- a rail request on a build without wiring, or with other GPIO pins or
  another completion protocol;
- a rail other than `MCU`;
- a build outside the route (`_require_power_route` runs again, for a hand-made `CompiledBuild`);
- a `settle_seconds` that is not a number above 0 and at most the 60 s a
  rail capture allows (a supply-only capture keeps the 300 s every capture
  allows; both limits are `fixture_capture`'s own);
- a power build run without a rail request (its firmware waits for GO);
- a missing `power_guard`, or one without `check()` and `verify_power_device()`;
- a `power_guard` given without a request.

The request goes to `capture_fixture` as `rail_power`, so the capture is the
fixture route's power path. The guard it receives is `_PowerCaptureGuard`:
the caller's guard keeps the board and probe (`verify_target`), the power
guard is the station's authority over the instrument and rail
(`verify_power_device`), and `check()` asks both, or once when they are the
same object.

`RunResult.energy` is `capture.rail_energy`. A failed capture measures none.
It holds:

- the measured iterations, duration, mean power and current, energy and energy per iteration;
- scope `gross_rail_restore_and_invoke` and subtraction `none`;
- the rail and the instrument serial;
- the hash-pinned raw record `rail-power.json`: the request, the instrument statistics and the packed GPIO frames;
- `completion`: the protocol-2 READY, GO-acknowledgement, gate-rise, gate-fall and DONE sample IDs on the instrument's counter.

`RunResult.power` is the request, with its instrument, rail, channels and
the wiring it was checked against. `RunResult.identity.run.energy` is an
`EnergyFacts`: the method, built from the scope, the rail, the subtraction
and the completion protocol. A run names the requested rail, also when its
capture failed; before a run, the build's identity states the rail as
unknown. Whether energy was measured is the outcome, `RunResult.energy`,
which is `None` after a failed capture. The observation hash covers
the energy record. Each call is one capture: repeats are separate
`run_build` calls into separate evidence directories, never aggregated. The
warmup and iteration counts are the plan's, so BENCH chooses them when it
freezes.

**Supply-only timing.** `power=FixtureBoardPowerRequest(driver, serial,
rail)` powers the board through a configured supply for a timing capture of
a build without power wiring. It measures no energy, needs no sync wiring and
leaves `identity.run.energy` as `None`. It still needs a `power_guard` to
authorize the supply, and is refused on a power build. It is not limited to
the energy route's engines, boards or lifecycles: an AP510B or `SINGLE`
build takes it.

`hpx run` takes no power options: a power run needs a station guard for the
instrument, which the operator guard cannot vouch for.

### Run API names and the command line

The run API's inputs, and every type its signatures, fields and return
values reach, are importable from `helia_profiler` itself, and that is the
documented route: `TypedFixture`, `FixtureIO`, `FixtureTensor` and its
quantization, `FixtureBuild`, `FixtureCaptureResult` with its timing, memory,
power and PSRAM parts, the power requests and `FixturePowerGuard`,
`RunIdentity` and its facts, the prepared runtime records, `Expect`,
`Tolerance` and the rest are the same objects as in `helia_profiler.fixture`
(experimental tier). `test_run_inputs.py` walks the run API's annotations
and fails on a reachable type the package does not export.
`hpx.capabilities()` returns what `fixture_capabilities()` returns.
`analyze_run_model(path)` and `analyze_run_model_for_engine(path, engine)`
(`run_inputs.py`) return the `TypedFixtureModelAnalysis` of
`analyze_typed_fixture_model` and its per-engine form; they take an engine name as well as an `EngineType`, and
raise `ConfigError` where those raise `ValueError`. `helia_profiler.fixture`
keeps working until the cleanup slice removes it.

The command line drives the same flow over a run directory
(`run_directory.py`): `build.json`, the model and one `input_<index>.bin` per
model input under `inputs/`, the firmware under `work/` unless `--work-dir`
names another directory inside it, and one `evidence-<N>` per capture. The
build is saved with the roots `run` (the directory) and `cache`
(`hpx_cache_root()`), so a heliaRT or LiteRT build also needs its prepared
runtime in the loading host's cache; a missing cache file is reported with
the `hpx runtimes prepare` command that restores it.

- `hpx freeze` resolves `-c/--config` and the shared engine, target and
  profiling options through `_build_cli_overrides`, the same mapping
  `hpx profile` uses, plus `--backend` (`engine.backend`), and writes
  `Session.freeze()` with `FrozenRun.save`. It refuses a directory or the
  model itself as `-o`.
- `hpx build PLAN --out DIR` takes only a new or empty `DIR`, so a failed
  rebuild never breaks an earlier build. It checks the model's sha256
  against the plan (the plan's model, or `--model`) and the `--input` files
  against the model, zero bytes when none are given, before it writes any
  file, refuses a `--work-dir` outside `DIR` before compiling, and calls
  `build()` with `--lifecycle` (default `carry`) and `--timing-scope`
  (default `invoke_only`). Its summary lists each input's name, size, pinned
  path and source.
- `hpx validate build DIR` loads, and so verifies, `DIR/build.json`; it and
  `hpx run` also take the path of `build.json`.
  `hpx validate` became a group whose callback still runs the MLPerf suite
  when no subcommand is given; suite options given with `build` are refused
  as a usage error rather than ignored.
- `hpx run DIR --board B --jlink-serial S` captures under an
  `OperatorGuard(board, jlink_serial)` (`run_guard.py`): the operator vouches
  for the physical board, as `hpx profile` assumes. Its `verify_target`
  refuses a `--board` other than the build's board, or another serial, and
  `check()` allows every step; HPX cannot check the physical board. The run
  summary records `"authority": "operator"`. `run_directory.run_from` asks
  the guard about the build's target before `run_build`, and refuses an
  existing `--evidence-dir`, so neither touches a device. `--expect
  host-litert|none` (default `host-litert`), `--tolerance` (`max_abs_error`,
  default 0) and repeated `--reference` files (instead of `--expect`) map
  onto `Expect`; a CLI build carries no expected outputs, so `Expect.from_build`
  has no flag. `--settle-seconds` takes the capture's bound, more than 0 and
  at most 300. A non-finite `max_abs_error` is written as `null`. Exit
  status is 1 for a refusal or a failed capture and 3 when the outputs do
  not match.

Each command prints its summary as JSON on stdout and reports an error on
stderr with exit status 1: an `HpxError`, and a `ValueError` or `OSError`,
never a traceback.

### Timing windows by route

Each route states its window in `RunIdentity.run`. The firmware decides the
facts; the identity builders copy them.

- **Ordinary profile** (`profile_identity.py`, `_main_base.cc.j2`): the clean
  window runs after at least one warmup (`clean_warmup = max(1, warmup)` in
  `firmware/context.py`) and reports the mean per invocation
  (`HPX_CLEAN_INFER_AVG_*`, `timing_statistic="mean"`). Apollo5 times it
  with one STIMER bracket; other families sum DWT cycles per invocation;
  ExecuTorch sums the runtime's execute-only cycles, so its per-call load
  is outside the window. The heliaAOT clean window restores its inputs
  inside the span. Per-layer counters are separate and aggregated by
  `profiling.aggregation`.
- **Fixture** (`fixture_identity.py`, `fixed_fixture.cc.j2`): engine setup
  and warmups run before the first timed STIMER read. `invoke_only`
  sums one STIMER bracket per invocation; `restore_and_invoke` takes one
  bracket around the timed loop, input copies included. `deployment_timing`
  reports the total ticks (`timing_statistic="total"`) at `timer_hz`.
- **Run API** (`compiled_build.py`): the fixture firmware with the build's
  lifecycle counts. `CARRY` keeps the plan's iterations and warmups;
  `SINGLE` times one invocation with no warmup, so `first_invoke_window` is
  `measured`. `RunResult.identity` also states the plan's `intent_hash`.

`first_invoke_initializer` comes from each adapter. TFLM's and heliaRT's
`MicroInterpreter` runs a `CALL_ONCE` subgraph inside the first `Invoke()`
(`kernels/call_once.cc`), so it is the build's `source_initializer_present`.
heliaAOT runs the initializer's constant assignments in the operator init
hooks `model_init()` calls, so it is `False`. ExecuTorch reloads its program
before every execute, its `instance_boundary` is `invocation`, and the
answer is `Unknown`.

The fixture firmware rejects a timed span of zero ticks as `FixtureStage.TIMING_BOUND`.
One STIMER tick is about 30.5 µs, so a `SINGLE` invocation shorter than that
can fail; `CARRY` with enough iterations does not.

### Model inspection and engine admission

LiteRT fixture preparation uses `EngineAdapter.prepare_fixture_model`. Each
adapter owns its integration limits and resolver selection. The shared LiteRT
reader validates the container and records graph facts without selecting an
engine; public IO validation is applied separately, and the only graph-level
fixture constraint is static public IO. The parsed model is reused for cost analysis.

The adapter's `FixtureModelPreparation` protocol describes common analysis,
byte extents and state metadata. LiteRT analysis results retain their existing
resolver and subgraph fields for compatibility; those fields are not required
by the adapter protocol. A future `.pte` fixture integration can supply its own
concrete preparation result. ExecuTorch fixture preparation is currently
unsupported; ordinary ExecuTorch profiling is unchanged.
