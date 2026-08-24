# Compatibility baseline

HPX owns the exact compatibility baseline in
`src/helia_profiler/data/compatibility-baseline-v1.json`. It is loaded and
validated during configuration resolution, then carried through the frozen
configuration and result provenance. The baseline is not an NSX lockfile.
Stage 5 combines its identity and canonical hash with the NSX registry hash,
target, engine, overrides, and relevant build inputs to select an isolated
dependency workspace.

The current baseline is `hpx-neuralspotx-0.7.17-2026-08`:

| Identity | Qualified reference |
| --- | --- |
| `neuralspotx` package | `0.7.17`, wheel SHA-256 `1289cd67…fbdb`, tag peeled to `8b5a7fa9…b44f` |
| `nsx-ambiq-sdk` | `v5.2.24`, peeled commit `a9f4ec25…1132` |
| `nsx-pmu-armv8m` | `5725c065…c88` |
| `nsx-tflite-micro` | `7afcf2b4…333` |
| `arm-cmsis-nn` | `6d21a6f8…f7c` |
| `ns-cmsis-nn` | `63172642…d7f` (`v7.29.2`) |
| `nsx-sensors` | `c219a2bc…3e25` (`v0.3.0`, peeled) |
| heliaRT | `1.17.0`, commit `ff6233ba…8df` |
| heliaAOT | `min_version=0.18.0`, `max_version_exclusive=0.19.0` |
| tflm | governed entirely by the `nsx-tflite-micro` / `arm-cmsis-nn` module refs above |

heliaRT 1.17.0 (issue #89) is a build-system and docs release from HPX's
perspective: the prebuilt distribution's exported-symbol surface, header
set, and core/toolchain/variant library matrix are identical to 1.16.0.
The new opt-in CMake source profiles (recording/test helpers), the opt-in
Ethos-U kernel support, and the removed `NS_CMSIS_NN` compile define all
sit outside the features HPX consumes, so the minimum supported version
stays 1.16.0.

neuralSPOT-X 0.7.17 fixes the J-Link flash-verification false negative that
aborted idempotent re-flashes of an unchanged image, and enforces
`ExitOnError 1` in generated flash recipes (AmbiqAI/neuralspotx#220). Its
packaged registry resolves `ns-cmsis-nn` at `v7.29.2`, and this promotion
advances that qualified ref in lockstep.

Baseline refs relate to the packaged registry in two tiers. Modules HPX
itself declares in generated apps (the SDK monorepo, PMU, heliaRT,
nsx-sensors) carry manifest pins at the baseline refs, and those pins
defeat the packaged registry — `nsx-sensors` stays at its audited `v0.3.0`
pin even though 0.7.17's registry default is older. Modules that arrive
only transitively (`nsx-cmsis-nn`, pulled by registry-backed heliaRT) have
no manifest pin and follow the packaged registry. The stock-TFLM engine's
declared modules (`nsx-tflite-micro`, `arm-cmsis-nn`) sit in this
registry-governed tier too: hpx renders informational manifest revisions
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
  cmsis_nn_path}`, `engine.config_path`, or one of the `HELIART_DIST_PATH`
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
