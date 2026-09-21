# Captured result bundles

Unmodified result bundles from the nightly hardware validation workflow,
kept so the documentation examples can show real output with its
provenance and so `hpx compare` and the result loaders have something to
run against without a board.

| Directory | Source |
|---|---|
| `hardware-validation-2026-09-16/` | run 35078330526 of `.github/workflows/hardware-validation.yml`, 2026-09-16, suite `complete`, hpx 0.1.6 at commit 0714893615b690b340661314d1165af61cd83d66 |

Each case directory holds the four core artifacts the manifest lists
(`summary.json`, `run_metadata.json`, `profile_results.csv`, `nsx.lock`),
`result_manifest.json`, and the heliaAOT extension files where the engine
wrote them. The run's logs and the resolved `config.yml` are not kept;
the resolved configuration is recorded in `run_metadata.json`.
`load_result_manifest(path, verify=True)` passes on every bundle.
