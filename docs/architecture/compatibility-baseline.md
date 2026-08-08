# Compatibility baseline

HPX owns the exact compatibility baseline in
`src/helia_profiler/data/compatibility-baseline-v1.json`. It is loaded and
validated during configuration resolution, then carried through the frozen
configuration and result provenance. The baseline is not an NSX lockfile.
Stage 5 combines its identity and canonical hash with the NSX registry hash,
target, engine, overrides, and relevant build inputs to select an isolated
dependency workspace.

The current baseline is `hpx-neuralspotx-0.7.14-2026-08`:

| Identity | Qualified reference |
| --- | --- |
| `neuralspotx` package | `0.7.14`, wheel SHA-256 `11634550…5ede`, tag peeled to `25d8d944…e406` |
| `nsx-ambiq-sdk` | `v5.2.24`, peeled commit `a9f4ec25…1132` |
| `nsx-pmu-armv8m` | `5725c065…c88` |
| `nsx-tflite-micro` | `2f02cc93…aea` |
| `arm-cmsis-nn` | `62967ecf…471` |
| `ns-cmsis-nn` | `2bb81953…a5e` (`v7.26.0`) |
| heliaRT | `1.16.0`, commit `c1b97f4a…f62` |
| heliaAOT | `min_version=0.18.0`, `max_version_exclusive=0.19.0` |
| tflm | governed entirely by the `nsx-tflite-micro` / `arm-cmsis-nn` module refs above |

neuralSPOT-X 0.7.14 promotes the helia-dsp, TileIO, Physiokit, and Sensors
registry entries to published semantic tags. Those projects are not part of
HPX's qualified profiling module graph, so their promotions do not add fields
or refs to this baseline. The neuralSPOT-X tag is verified and stored as its
peeled commit; all existing SDK, PMU, TFLM, CMSIS-NN, and engine refs remain
unchanged by this focused promotion.

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
