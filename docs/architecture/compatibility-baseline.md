# Compatibility baseline

HPX owns the exact compatibility baseline in
`src/helia_profiler/data/compatibility-baseline-v1.json`. It is loaded and
validated during configuration resolution, then carried through the frozen
configuration and result provenance. The baseline is not an NSX lockfile.
Stage 5 combines its identity and canonical hash with the NSX registry hash,
target, engine, overrides, and relevant build inputs to select an isolated
dependency workspace.

The current baseline is `hpx-neuralspotx-0.7.17-2026-09`:

| Identity | Qualified reference |
| --- | --- |
| `neuralspotx` package | `0.7.17`, wheel SHA-256 `1289cd67…fbdb`, tag peeled to `8b5a7fa9…b44f` |
| `nsx-ambiq-sdk` | `v5.2.24`, peeled commit `a9f4ec25…1132` |
| `nsx-pmu-armv8m` | `5725c065…c88` |
| `nsx-tflite-micro` | `7afcf2b4…333` |
| `arm-cmsis-nn` | `6d21a6f8…f7c` |
| `ns-cmsis-nn` | `9884d5fc…c63` (`v7.31.0`, hpx-declared — see below) |
| `nsx-executorch` | `27eee513…b1ed` |
| `nsx-sensors` | `c219a2bc…3e25` (`v0.3.0`, peeled) |
| heliaRT | `1.19.0`, commit `038a0c44…a83` (min supported `1.16.0` — from `HELIART_MIN_VERSION` in code, not a baseline-JSON field) |
| heliaAOT | `min_version=0.19.0`, `max_version_exclusive=0.20.0` |
| tflm | governed entirely by the `nsx-tflite-micro` / `arm-cmsis-nn` module refs above |
| executorch | `0.1.0`, module ref `27eee513…b1ed` (a checkout's `version.txt` is verified against the baseline) |

heliaRT 1.19.0 and heliaAOT 0.19.0 (issue #246) are the releases that add FP16
and FP32 kernels. This revision promotes both HPX-owned engine pins and moves
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
revision qualifies `ns-cmsis-nn v7.31.0` and — because neuralSPOT-X 0.7.17's
registry still resolves v7.29.2 — hpx **declares** `nsx-cmsis-nn` at that ref
on both engines' source routes instead of inheriting the registry's choice.
The module thereby moves from the registry-governed tier to the hpx-declared
tier (next section); neuralSPOT-X itself is unchanged. heliaRT 1.19.0 accepts
any `>= v7.28.0`, so both engines now build against one core with no override,
and a run stamps `qualified`. A neuralSPOT-X registry bump remains the tidy
long-term landing but is no longer load-bearing.

**Integration change.** heliaRT 1.19.0's `helia` backend refuses to configure
unless ns-cmsis-nn's fp32 kernels are enabled — an `option()` default that must
be overridden *before* the module is added — and heliaAOT's generated module
checks the same requirement under the exported define's name (helia-aot#349).
`engines/cmsis_nn.py::cmsis_nn_cmake_vars` sets `NSX_CMSIS_NN_ENABLE_F32` /
`ARM_NN_ENABLE_F32` for every source build of either engine, and the `F16`
pair only when the model carries FLOAT16 tensors — computed or dequantized
weights — on a Cortex-M55: ns-cmsis-nn
below v7.30.0 ICEs on GCC 14 for its fp16 sources (PR 118460), so int8 and
fp32 builds must not compile them. heliaRT 1.19.0 accepts ns-cmsis-nn
`>= v7.28.0`; heliaAOT 0.19.0 requires `>= v7.31.0`; the baseline pins v7.31.0.

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
packaged default) — `nsx-sensors` stays at its audited `v0.3.0` pin even though
0.7.17's registry default is older, and `nsx-cmsis-nn` builds at `v7.31.0`
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
  override is present (`engine.config.{dist_path,source_path,source,
  cmsis_nn_path,cmsis_nn_ref}`, `engine.config_path`, or one of the `HELIART_DIST_PATH`
  / `HELIART_SOURCE_PATH` / `CMSIS_NN_PATH` environment variables), but no
  NSX project override is present. Ordinary engine knobs (e.g.
  `engine.backend`, `engine.config.variant`) do not affect qualification.
- `development-overrides`: one or more `build.nsx_modules` project/module
  overrides are present.

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
