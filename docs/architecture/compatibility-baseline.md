# Compatibility baseline

HPX owns the exact compatibility baseline in
`src/helia_profiler/data/compatibility-baseline-v1.json`. It is loaded and
validated during configuration resolution, then carried through the frozen
configuration and result provenance. The baseline is not an NSX lockfile.
Stage 5 combines its identity and canonical hash with the NSX registry hash,
target, engine, overrides, and relevant build inputs to select an isolated
dependency workspace.

The current baseline is `hpx-neuralspotx-0.8.1-2026-09`:

| Identity | Qualified reference |
| --- | --- |
| `neuralspotx` package | `0.8.1`, wheel SHA-256 `7aac6f1b…9094`, tag peeled to `2dbe12a2…901f` |
| `nsx-ambiq-sdk` | `v5.2.24`, peeled commit `a9f4ec25…1132` |
| `nsx-pmu-armv8m` | `5725c065…c88` |
| `nsx-tflite-micro` | `7afcf2b4…333` |
| `arm-cmsis-nn` | `6d21a6f8…f7c` |
| `ns-cmsis-nn` | `aaeb145a…30c` (`v7.32.0`, hpx-declared — see below) |
| `nsx-executorch` | `27eee513…b1ed` |
| `nsx-sensors` | `c219a2bc…3e25` (`v0.3.0`, peeled) |
| heliaRT | `1.20.0`, commit `edb3a25f…440` (min supported `1.16.0` — from `HELIART_MIN_VERSION` in code, not a baseline-JSON field) |
| heliaAOT | `min_version=0.20.0`, `max_version_exclusive=0.21.0` |
| tflm | governed entirely by the `nsx-tflite-micro` / `arm-cmsis-nn` module refs above |
| executorch | `0.1.0`, module ref `27eee513…b1ed` (a checkout's `version.txt` is verified against the baseline) |

This revision moves neuralSPOT-X `0.7.17 → 0.8.1` for the Apollo4 Blue Lite
board descriptor fix (AmbiqAI/neuralspotx#250, closing hpx#263): 0.7.17's
`apollo4l_evb` and `apollo4l_blue_evb` descriptors cleared `NSX_SYSTEM_SOURCE`,
so the startup library was built without the CMSIS system file and every
profiler firmware for those boards failed to link with `SystemCoreClock`
undefined. Only the tool row moves: 0.8.1's packaged registry resolves each
baseline project at the same tag as 0.7.17's (nsx-ambiq-sdk `v5.2.24`,
nsx-pmu-armv8m `v0.2.0`, nsx-tflite-micro and arm-cmsis-nn `v0.1.0`,
nsx-sensors `v0.3.0`, and still `ns-cmsis-nn v7.29.2` under the hpx-declared
`v7.32.0`), each re-peeled to the commit already recorded. Two 0.8.x changes
are worth knowing: the Python floor rose to 3.11 (HPX already requires it),
and the packaged board and tooling modules changed content hash
(neuralspotx#247), so an `nsx.lock` produced under 0.7.17 cannot be
`nsx sync --frozen`'d against 0.8.1 — the dependency workspace is keyed on the
baseline fingerprint and registry hash, so a promoted run resolves a fresh
lock rather than reusing a stale one. With this baseline `apollo4l_blue_evb`
joins the nightly hardware-validation default board list.

heliaRT 1.20.0, heliaAOT 0.20.0 and `ns-cmsis-nn v7.32.0` (issue #279) move
together because they must: v7.32.0 consolidated the float switches onto
`ARM_NN_ENABLE_F32/F16` and aborts the configure when a retired
`NSX_CMSIS_NN_ENABLE_*` name is defined at all, while heliaRT 1.19.0 aborts when
that same retired name is *missing*. No single spelling satisfies both, so the
core and both engines are one atomic promotion. heliaRT 1.20.0 reads the
capability from the resolved ns-cmsis-nn target through its own query rather
than from a cache variable, and it accepts a float16-input `DEQUANTIZE`, which
is the LiteRT converter's standard FP16 model shape (helia-rt#255, closing
hpx#251). heliaAOT 0.20.0 pins the same core and now rejects float on the six
operators ns-cmsis-nn ships integer kernels for, naming the operator and dtype
instead of failing at link time (helia-aot#396).

The previous revision is recorded below. heliaRT 1.19.0 and heliaAOT 0.19.0
(issue #246) are the releases that add FP16
and FP32 kernels. That revision promoted both HPX-owned engine pins and moved
one neuralSPOT-X ref — `ns-cmsis-nn`, to v7.31.0 (below); every other NSX ref
is unchanged: heliaRT `1.17.0 → 1.19.0` (`038a0c44…a83`),
heliaAOT `[0.18.0, 0.19.0) → [0.19.0, 0.20.0)`, with `ai-edge-litert` relocked to
2.2.0 (floor unchanged at `>= 2.1.6`; the `_tflite_reader` schema constants
re-verified unchanged against it). `HELIART_MIN_VERSION` stays `1.16.0`
— 1.17 → 1.19 is additive from HPX's perspective.

**The core moves with the engines, and hpx now declares it.** heliaRT links a
*consumer-provided* `ns-cmsis-nn` (heliaCORE); it does not vendor one. The
previously qualified `v7.29.2` is the core heliaRT 1.18.0 (withdrawn) shipped
"with its float defects as known issues", and heliaAOT 0.19.0 refuses it
outright: every generated module carries an unconditional
`#error "CMSIS-NN version too old; need at least v7.31.0"` whose rationale
names v7.29.x/v7.30.0 as defective for int8 as well (helia-aot#356). So this
revision qualifies `ns-cmsis-nn v7.31.0` and — because neuralSPOT-X's
packaged registry (0.7.17, and still 0.8.1) resolves v7.29.2 — hpx
**declares** `nsx-cmsis-nn` at that ref on both engines' source routes
instead of inheriting the registry's choice.
The module thereby moves from the registry-governed tier to the hpx-declared
tier (next section); neuralSPOT-X itself is unchanged. heliaRT 1.19.0 accepts
any `>= v7.28.0`, so both engines now build against one core with no override,
and a run stamps `qualified`. A neuralSPOT-X registry bump remains the tidy
long-term landing but is no longer load-bearing.

**Integration change.** The float kernels are opt-in and must be requested
*before* the ns-cmsis-nn module is added, since an `option()` default cannot be
overridden afterwards. `engines/cmsis_nn.py::cmsis_nn_cmake_vars` derives
the switches from the model: `ARM_NN_ENABLE_F32` when it computes in float at
all, and `ARM_NN_ENABLE_F16` additionally when it carries FLOAT16 tensors —
computed or dequantized weights — on a Cortex-M55, since ns-cmsis-nn below
v7.30.0 ICEs on GCC 14 for its fp16 sources (PR 118460). An integer-only model
links neither: measured on the int8 KWS DS-CNN, dropping the forced fp32
kernels returns 32.6 KB of MRAM (311,180 B → 277,764 B on heliaRT) with cycles
unchanged at 2.06 M, which recovers the growth 1.19.0's unconditional
requirement introduced. heliaAOT is unaffected either way (151,180 B), since it
generates only the kernels its graph uses. A model that cannot be read enables
fp32 rather than profiling float work on the reference path unannounced.
`ARM_NN_ENABLE_*` is the only spelling the core accepts; emitting a retired
`NSX_CMSIS_NN_ENABLE_*` name is a configure error, and a regression test asserts
no board or model combination emits one (#279).

**Behavior deltas in 1.19.0 that a float baseline must expect** (all toward
correctness): NaN now propagates through float `ADD`/`MUL` instead of being
clamped; `QUANTIZE`/`TRANSPOSE_CONV`/`SVDF` kernel failures surface as
`kTfLiteError` instead of `kTfLiteOk` over unwritten output; float16
`UNIDIRECTIONAL_SEQUENCE_LSTM` output shifts ~2.3e-2.

**Verified.** Host: the full suite passes under helia-aot 0.19.0 + litert 2.2.0
with no other change, and the int8/uint8 Softmax preflight boundaries (#147)
did not move. heliaAOT emits native `arm_*_f32` kernels for an FP32 graph and
native `arm_*_f16` kernels for an all-`FLOAT16` graph on all three M55
targets; its float coverage is partial (`MEAN` and a tier of shape ops remain
int8/int16-only — helia-aot#345). Bench (Apollo510 EVB, Arm GNU 15.2.Rel1,
96 MHz, core `v7.31.0`, `tests/fixtures/kws_float_fp32.tflite` and its
all-`FLOAT16` cast, 43,520 MACs): heliaRT and heliaAOT agree within 0.3 % —
FP32 ≈ 1.17 M and FP16 ≈ 1.08 M clean cycles on both, with identical layer
profiles, so the single-input-channel first `CONV_2D` (93 % of the run) and
the slower-in-f16 `FULLY_CONNECTED` are heliaCORE kernel properties, not
either runtime's. Every plan-vs-measured memory region matched. **int8 is
unchanged in speed but larger:** the qualified int8 KWS DS-CNN measured 2.07 M
cycles on 1.17.0 and 1.19.0 alike (−0.02 % on the same core, run noise) while
`.text` grew 277,420 B → 308,868 B (+31.4 KB) with `.data`/`.bss`/arena
unchanged — consistent with the fp32 kernel paths 1.19.0 requires riding into
an int8 image (compiling the fp16 kernels too, as an earlier run did, added
another 35 KB; hence the model gating). Moving the core v7.29.2 → v7.31.0 on
1.19.0 changed that int8 run by −0.4 % cycles and +2.3 KB `.text`. With the
core declared at v7.31.0, `hpx validate` passes kws/vww/ic/ad on both engines
(8/8; heliaAOT was 0/4 on v7.29.2), every float run above reproduces its
numbers with no override and stamps `qualified`, and the converter's
weights-only FP16 form now builds and runs on heliaAOT (1,230,144 cycles);
heliaRT still rejects that form (helia-rt#255). ExecuTorch's `ns` provider,
which shares the module helper, also builds and runs kws on v7.31.0
(2,135,764 clean cycles, `qualified`). Two of the five
power windows were INVALID by design: the JS320 GPI-stream gate edges
disagreed with the firmware window clock and host poll edges (which agree to
<5 ms) by a random −54 / +81 / +38 / −1 / +5 ms — #249.

heliaRT 1.17.0 (issue #89) is a build-system and docs release from HPX's
perspective: the prebuilt distribution's exported-symbol surface, header
set, and core/toolchain/variant library matrix are identical to 1.16.0
(all 18 archives verified symbol/member/`.text`-identical in review).
NB the CMake changes land squarely in **HPX's default build path** — the
registry flow is a source build (`engines/helia_rt/adapter.py`), and the
changes land in the backend-target function it enters — but each is
behavior-neutral for HPX, verified rather than assumed. The six changes:
(1) the recording allocators and (2) the test/mock/fake helpers drop out
of the default source set (both now opt-in profiles; HPX uses
`MicroAllocator::Create` and none of the helpers); (3) the removed
`NS_CMSIS_NN` define branches nothing (zero preprocessor uses in the
entire tree — ns-cmsis-nn self-defines it in `arm_nn_types.h`); (4) the
newly-unconditional `ethos_u/ethosu.cc` TU is an inert stub without the
driver; (5) the new explicit `-fno-exceptions` (CXX, PRIVATE) is a
strict subset of what the NSX board flags already impose on all three
toolchains; (6) a private ns-cmsis-nn include-dir was added on the link
branch, upstreaming the header-prefix shim helia-rt's NSX module already
carried. Empirically, the full 1.16→1.17 source-axis A/B showed a 0 B
`.text` delta, and single-run cycle deltas of −41 and −531 (−0.002% /
−0.026%; KWS DS-CNN, two independent apollo510_evb benches, gcc) — at
or near run noise, with no claim of a real kernel effect. `hpx
compare` surfaces such a promotion via the `engine_version` comparability
dimension (#193): the measured `run_metadata.engine.version` renders as an
`Engine version` row in the compare Config table and an informative
`dimension.engine_version_differs` warning when the two sides differ
(absent for artifacts predating the dimension, and for tflm/executorch
runs, which record no resolved version).
The Ethos-U kernel support is outside what HPX consumes **today**; the
in-flight atomiq110 work (PR #98) will opt into it via
`NSX_HELIA_RT_ENABLE_ETHOSU` and requires a helia-rt newer than 1.17.0
for the flag mapping. Minimum supported version stays 1.16.0 (HPX relies
on nothing 1.17-only).

neuralSPOT-X 0.7.17 fixes the J-Link flash-verification false negative that
aborted idempotent re-flashes of an unchanged image, and enforces
`ExitOnError 1` in generated flash recipes (AmbiqAI/neuralspotx#220). Its
packaged registry resolves `ns-cmsis-nn` at `v7.29.2`; that promotion
advanced the qualified ref in lockstep, and the 2026-09 revision then moved
`nsx-cmsis-nn` into the hpx-declared tier at `v7.31.0`.

Baseline refs relate to the packaged registry in two tiers. Modules HPX
itself declares in generated apps (the SDK monorepo, PMU, heliaRT,
nsx-sensors and, since the 2026-09 revision, `nsx-cmsis-nn`) carry
manifest pins at the baseline refs, and those pins defeat the packaged
registry (concretely: a module whose `NsxModuleRef.ref` is set is rendered
into the app's module-registry override, which NSX locking honours over its
packaged default) — `nsx-sensors` stays at its audited `v0.3.0` pin
(0.7.17's registry default was older; 0.8.1's matches it), and
`nsx-cmsis-nn` builds at `v7.31.0`
where the registry would resolve `v7.29.2`. The stock-TFLM engine's
declared modules (`nsx-tflite-micro`, `arm-cmsis-nn`) sit in the
registry-governed tier: hpx renders informational manifest revisions
for them, but NSX locking reads only the registry's module revision. For
this whole tier the post-lock validation refuses to build when the
resolved commit disagrees with this baseline, so a promotion must advance
exactly these refs together with the tool — never "adopt the registry"
for the pinned tier.
`nsx-nanopb` (promoted in 0.7.16) is outside HPX's qualified module graph.
The neuralSPOT-X tag is verified and stored as its peeled commit. The
`nsx-tflite-micro` and `arm-cmsis-nn` rows still pin the same qualified
`v0.1.0` tags but are now recorded as their peeled commits — earlier
baselines recorded the annotated tag-object ids, which a resolved NSX lock
can never match. All other project, module, and engine refs (SDK, PMU,
heliaRT, nsx-sensors) are unchanged.

Every baseline project and module ref is immutable by policy: only a full
40-character Git object ID is accepted. Newly promoted annotated tags are
verified and peeled before being recorded. Each engine entry is typed: a pinned
`version`/`ref`
(heliaRT), a `min_version`/`max_version_exclusive` semver range (heliaAOT),
or `governed_by_modules: true` when an engine has no version of its own and
is fully qualified by its NSX module refs (stock TFLM).

heliaRT's `version`/`ref` in the baseline mirror the canonical
`HELIART_VERSION`/`HELIART_RELEASE_TAG` constants in
`engines/helia_rt/artifacts.py` (see `AGENTS.md`) for reporting purposes
only; a test asserts they never drift apart. Runtime resolution of the
default heliaRT source continues to read those constants directly — the
baseline does not drive ordinary heliaRT version/ref resolution.

heliaAOT is different: it is a separately pip-installed package, so HPX
cannot select its version — it can only validate the one already installed.
`engines/helia_aot/compile._check_helia_aot_version()` reads the baseline's
`min_version`/`max_version_exclusive` policy for `helia-aot` (falling back to
local `HELIAAOT_MIN_VERSION`/`HELIAAOT_MAX_VERSION_EXCLUSIVE` constants only
when no resolved baseline is available) and raises a clear `EngineError` if
the installed package is outside that qualified range.

## Qualification states

Each resolved run reports one state:

- `qualified`: baseline defaults are used.
- `qualified-with-engine-override`: an explicit engine source/version
  override is present (`engine.config.{dist_path,source_path,source}`,
  `engine.config_path`, or one of the `HELIART_DIST_PATH` /
  `HELIART_SOURCE_PATH` environment variables), but no baseline module is
  replaced. Ordinary engine knobs (e.g. `engine.backend`,
  `engine.config.variant`) do not affect qualification.
- `development-overrides`: an NSX module is replaced, either through any
  `build.nsx_modules` entry NSX itself resolves (whether or not the baseline
  pins that module; entries naming an engine-owned module are ignored with a
  warning and do not count) or through a CMSIS-NN provider selector
  (`engine.config.cmsis_nn_path`, `engine.config.cmsis_nn_ref`, or the
  `CMSIS_NN_PATH` environment variable). Overrides are classified by the
  dependency they replace, not by the config key that carried them: the
  selector is recorded under the provider module it replaces (`nsx-cmsis-nn`
  for the helia engines and ExecuTorch's `ns` provider, `arm-cmsis-nn` for
  ExecuTorch's `arm` provider), so the `cmsis_nn_ref` form and the
  `build.nsx_modules` form of the same ref stamp identically. Empty selector
  values are not overrides. The dependency provenance's `overrides` list uses
  the same module-scoped record for the selector that took effect.

Explicit local paths, branches, and SHAs remain supported. They are never
silently replaced by the baseline; the state and override names are recorded
in `run_metadata.json`, `summary.json`, `result_manifest.json`, and terminal
output.

The baseline exposes a canonical SHA-256 fingerprint. Ordinary profiles never
refresh a structurally compatible lock: they reuse its exact bytes and run
`nsx sync --frozen`. A missing or incompatible lock is resolved without the
NSX update flag. Only `hpx profile --update-dependencies` (or
`build.update_dependencies: true`) deliberately refreshes refs. The exact
resulting `nsx.lock` and typed resolution provenance are copied into every
completed result bundle.
