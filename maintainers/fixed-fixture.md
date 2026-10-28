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
installed package version and hashes of the shipped files listed in
`fixture_closure.txt` with those records.

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

Use `build(plan, fixture, runtime=None)` to consume that prepared cache, or
supply a verified `PreparedUpstreamRuntime` explicitly.
The consumer owns its normal source recipes, build receipts and reporting.
Inspect the actual linked image/map and placement against the target's bounds
before capture; an arena reservation alone does not establish linked fit or a
runtime peak. Close every native batch before serial device operations, using
the caller's existing current-board guard and exclusive lock.

## Public API

The package root, `helia_profiler`, is the public run contract, recorded in
`tests/test_api_stability.py`. Types still named `Fixture*` are public when
exported there; the implementation modules are internal. There is no separate
fixture facade or capability API-version field.

Create a `TypedFixture(model, inputs, outputs)` with complete `FixtureIO`
input bytes and ordered `FixtureOutput(tensor)` declarations. Numerical references
are separate: choose an explicit `Expect` when executing or compare retained
outputs later. Freeze configuration once, then build and retain it:

```python
import helia_profiler as hpx

plan = session.freeze()
compiled = hpx.build(
    plan, fixture, method=method, lifecycle=hpx.Lifecycle.CARRY,
)
compiled.save(build_path, roots=roots)
restored = hpx.load_build(build_path, roots=relocated_roots)
result = hpx.run_build(
    restored, guard=guard, jlink_serial=serial, evidence_dir=raw_directory,
)
```

The caller supplies the session, typed fixture, method, named roots and physical
execution authority. Building never acquires a device. `CompiledBuild.verify()`
checks pinned artifacts, the frozen intent and the binding between plan and
compiled facts. Loading does not build or repair dependencies. Relocation changes
the caller's root map, not the recorded bytes.

Current saved formats are compiled-build schema 3, frozen-run schema 2,
run-result schema 1 and profile summary schema 19. Earlier formats are refused;
read old bundles with the HPX release that produced them. There is no conversion
or archival-reader fallback. These checks establish consistency, not provenance
authenticity, numerical correctness or permission to operate hardware. Consumers
still admit the selected model, full IO, method, state, clocks and image against
their own experiment.

`CompiledBuild.build` exposes `FixtureBuild` facts including the model, complete
inputs/output descriptors, arena capacity, placement, clock profile, CPU/timer
rates, invocation counts and linked files. `kernel_source` and `runtime_source`
come from pinned build evidence; absent source facts remain unknown. The build's
`identity` exposes a `RunIdentity` with canonical runtime/kernel names, lifecycle
and timing facts. Intent, binding, inputs, build and observation hashes use
`hpx.<kind>/<version>:sha256:<hex>`; file hashes remain plain SHA256.

`build()` verifies the model, engine, board, toolchain, placement, arena size,
clock profile, lifecycle counts and available runtime/provider facts against the
frozen plan. `SINGLE` selects one measured invocation and no warmup; `CARRY`
keeps one initialized instance through warmups and measured invocations. Input
restoration does not reset internal model state. See
[Timing windows by route](#timing-windows-by-route) for measurement boundaries.

Interpreter builds use the prepared runtime named by the plan, unless the caller
supplies `PreparedUpstreamRuntime`. A missing cache entry is reported; building a
run does not silently prepare a provider. `capabilities()` reports supported
targets and IO dtypes. `analyze_run_model` and `analyze_run_model_for_engine` expose
model metadata before build; engine failures still propagate.

Run builds take pinned inputs only. The build boundary refuses any
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

`build(plan, fixture, method=..., lifecycle=..., runtime=...)` composes the
platform, engine, memory-plan, firmware-generation and NSX build stages. The
public run path returns a verified compiled build; it has no render-only mode.
Supply SHA256-pinned `FixtureFile` records and complete typed IO descriptors.
Model analysis validates shape, indices, storage types and quantization. Firmware
never resizes or rewrites the model. No model name selects execution.

`FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)` measures a batch including
full input restoration. `INVOKE_ONLY` restores each input outside each measured
invocation and sums the timer intervals; timer quantization applies per call.
Both use `config.profiling.warmup` and `iterations`. The intent identity binds
fixture, configuration, provider and timing scope. A compiled build identity
also binds ELF/image, dependency lock, link map, generated source hashes and
recorded toolchain provenance. Reusing a work directory with different intent fails.

LiteRT links a `PreparedUpstreamRuntime`: a pinned archive, header root
and manifest. Strict typed ingress validates provider URL/revision declarations, declared ABI,
include directories and all header hashes. Archive hashing and regular-archive
magic rejection do not verify member format, ARM attributes or ABI compatibility.
A caller that supplies its own archive must independently audit its
build/source/ABI record; manifest fields are assertions, not independent ABI evidence. Copies recheck hashes and path
containment. Provider source identities remain manifest-declared; retain the
corresponding audited source/build record.

A LiteRT run build that names no runtime uses the archive
`hpx runtimes prepare litert` built from the default litert runtime record. A
station prepares this explicitly before building a run. Prepare
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

A heliaRT run build that names no runtime uses the archive
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

`run_build(compiled, guard=..., jlink_serial=..., evidence_dir=...)` captures
raw evidence using the profiler flash/probe APIs. It derives image pins, output
extents, placement and canonical target from the verified compiled build. The
caller supplies the serial, evidence directory and bounded completion wait
(`settle_seconds`), plus an optional `expected_duration_s` prediction.
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
`analyze_run_model` reads the same declarations from the flatbuffer,
and the build refuses any difference in name, index, order, dtype, shape or
quantization. Every IO tensor must be named in the flatbuffer and have at least
one dimension; unnamed or scalar (rank-0) IO is refused. An INT8/INT16 IO tensor with a
single scale and zero point is read as per-tensor, even when its quantized axis
has extent 1, so declare it with `PerTensorQuantization`.

`capabilities()` declares generic engine/IO support on the fixture
target from the fixture integration's explicit IO declarations. Missing or
unsupported runtime qualification records do not disable fixture execution.
It reports `supported` or `unsupported`, never qualification inherited from
another release. IO dtype does not prove a model's compute precision, operator
support, numerical acceptance or device qualification. Plain INT32 inputs remain
supported index payloads; INT32 outputs remain refused.

Query `runtime.records.qualification(name, version, board=..., clock=...,
precision=...)` for exact release evidence, including its source and basis.
Historical qualification records and the `QUALIFIED` enum remain readable.
Supported but unqualified execution remains available. Internal FLOAT16 tensors,
including weights behind DEQUANTIZE, are recorded model facts, not a blanket
fixture veto. The selected engine and its actual provider decide kernel support;
their failures propagate without conversion or fallback. Public FLOAT16 IO remains
unsupported on LiteRT. `FixtureBuild.capabilities` records public IO dtype support,
not internal compute or kernel qualification.

Firmware restores every input before each warmup and measured call. It checks
each tensor's byte extent, and on TFLM also its type, shape and per-tensor
quantization (per-axis parameters are verified on the host only). It then copies
each output to its own DTCM sink, `deployment_output`, `deployment_output_1`, and
so on. `deployment_checksum` covers all outputs in model order. Total output
bytes may not exceed `FIXTURE_READBACK_BUDGET` (128 KiB), so every output of
every capture is read back in full. The caller can compare each `FixtureCaptureResult.outputs` file against
explicit reference bytes through `Expect` or `compare_outputs`. The run path
derives readback extents from the compiled output descriptors and refuses an
image whose typed sinks differ from them.

`observe_aot_arenas=True` (heliaAOT only) paints every scratch arena with `0xA5`
after model initialization, then scans it after the timed loop, outside the
timed interval. `FixtureBuild.aot_arena_scan` lists `(region id, size)`. The run path carries those sizes into capture; `FixtureCaptureResult.arena_scan` gives
`(touched bytes, high water)` per arena. Both are lower bounds, since a kernel may
write the paint value itself. Persistent and constant arenas are not painted.
Painting touches every scratch byte just before the warmups, so configure at
least one warmup when timing matters. The option joins the intent identity only
when enabled, so existing identities are unchanged.

## Measurement records

Read timing, output files, allocator observations and optional power from
`RunResult.capture`. These are producer observations, not consumer acceptance
verdicts. `section_inventory` and the `SectionInventory`, `ElfSection` and
`LoadSegment` types are exported from the package root for offline ELF inspection.
ELF file size, flat image size, linked sections, reservations and observed peaks
are different facts; do not substitute one for another. The run API does not
expose the retired fixture-specific footprint or sibling-image operator helpers.
Per-layer observations from ordinary profiling do not establish attribution to
a separately compiled run image. Consumers own comparison eligibility and
numerical acceptance under their selected policy.

### Running a compiled build

`run_build(compiled, guard=..., jlink_serial=..., evidence_dir=..., settle_seconds=...,
expect=None, expected_duration_s=None)`, or `compiled.run(...)`, captures once from a
compiled build without rebuilding. It verifies the build first, checks an `Expect` before
the capture (one readable reference of each output's size, outputs the build carries, or
LiteRT installed for a host reference), derives a capture request from the verified build,
acquires under the caller's guard, and returns a `RunResult` holding
the `compiled` build, the raw `capture`, the run's `identity` and the `verification`. A
successful capture whose iterations and warmups differ from the build's raises
`CaptureError`. The identity is derived from the build and capture, so it carries an `observation_hash`, with `run.lifecycle` taken from the build. With an
`Expect`, a successful capture is compared through `compare_fixture_outputs`; without one,
or when the capture failed, `verification` is `None`. `RunResult.succeeded` is true only
for a `success` capture. A failed capture is returned, not raised; errors raised while
capturing propagate. A very short `SINGLE` invocation can hit the one-tick floor described
under [Timing windows by route](#timing-windows-by-route).
`RunResult.save(path, roots=..., build=...)` (`run_record.py`) writes the run as a
`hpx.run-result` v1 and `load_run(path, roots=...)` reads it back as a `RunResultRecord`. Save
refuses a run whose identity or verification does not recompute from its build and capture,
so a saved result always loads. The document pins the saved `build.json` and every
`FixtureFile` the capture references by sha256 and size, writes the reference outputs beside
it as `<stem>.reference_<index>.<publication>.bin`, and records its own location so `api.load_result` can
recover the root without being told. Loading recomputes the identity with the same
`_identity` path `CompiledBuild.run` uses and the comparison with `compare_outputs`, so
neither is trusted from the file. Unknown identity facts compare as unknown whatever their
reason, so rewording a reason does not invalidate saved results; a reworded value does. Both
save and load refuse a power request the build's wiring rules out, as `CompiledBuild.run`
does. Free text such as the capture's error is recorded, not verified. started.json and
receipt.json are not pinned, since the capture does not reference them. `_run_view.py` maps a `RunResultRecord` as `hpx-run-1`.
`examples/run_api_conformance.py` walks freeze, build, save, load and run on a
typed fixture and prints the identity, verification facts and key measurements.
Its plan states what a fixture build requires (ATfE, the `lp` clock, an SRAM
arena of explicit size, MRAM weights), the build works under the host's
`work_dir`, `build()` links the heliaRT or LiteRT runtime `hpx runtimes prepare` cached,
and the saved build names the HPX cache as a second root. Its
`PlaceholderGuard` refuses every capture; a harness passes its own guard.

Shared run admission and identity rules live in `_run_contracts.py`; execution and
saved-run readers use the same count, route and identity enrichment policy. See
[the contributor map](architecture.md#shared-work-and-verification-owners) for
the separate owners of execution authority and retained-evidence verification.

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
  allows; both limits are `fixtures.capture`'s own);
- a power build run without a rail request (its firmware waits for GO);
- a missing `power_guard`, or one without `check()` and `verify_power_device()`;
- a `power_guard` given without a request.

The run path passes the rail request to the internal capture implementation. The guard it receives is `_PowerCaptureGuard`:
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
`Tolerance` and the other run types retain their documented API tiers. `test_run_inputs.py` walks the run API's annotations
and fails on a reachable type the package does not export.
`hpx.capabilities()` returns the supported target and dtype facts.
`analyze_run_model(path)` and `analyze_run_model_for_engine(path, engine)`
(`run_inputs.py`) return the `TypedFixtureModelAnalysis` of
`analyze_typed_fixture_model` and its per-engine form; they take an engine name as well as an `EngineType`, and
raise `ConfigError` where those internal helpers raise `ValueError`.

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
  summary records `"authority": "operator"` and the saved result's path.
  `run_directory.run_from` asks the guard about the build's target before
  `run_build`, and refuses an existing `--evidence-dir` or one outside the run
  directory, so neither touches a device; after the capture it saves
  `result.json` in the evidence directory under the run directory's roots. `--expect
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
- **Fixture** (`fixtures/identity.py`, `fixed_fixture.cc.j2`): engine setup
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
