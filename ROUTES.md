# Legacy routes, before and after (#334)

Every route the MkDocs site publishes today, against what the Astro site does
with it after this port. Generated from `astro-site/src/data/legacy-routes.json`
and `astro-site/src/data/redirects.json`; `check:redirects` asserts the pairing.

- **served**: a real page lives at the same path, so there is no redirect.
- **redirected**: the path forwards to the page that now holds the content.
- **redirected (deferred)**: forwards to a section landing page because the real
  page is not built yet. All 14 belong to #331.

| Legacy route | Route today | How | Owning issue |
| --- | --- | --- | --- |
| `/` | `/helia-profiler/` | served | #320 item 08 (Home) |
| `/architecture/` | `/helia-profiler/guide/concepts/` | redirected | #334 |
| `/architecture/adding-an-engine/` | `/helia-profiler/guide/concepts/adding-an-engine/` | redirected | #334 |
| `/architecture/capture/` | `/helia-profiler/guide/concepts/capture/` | redirected | #334 |
| `/architecture/compatibility-baseline/` | `/helia-profiler/reference/compatibility-baseline/` | redirected | #334 |
| `/architecture/engine-adapters/` | `/helia-profiler/guide/concepts/engine-adapters/` | redirected | #334 |
| `/architecture/field-diagnostics/` | `/helia-profiler/guide/concepts/field-diagnostics/` | redirected | #334 |
| `/architecture/firmware/` | `/helia-profiler/guide/concepts/firmware/` | redirected | #334 |
| `/architecture/pipeline/` | `/helia-profiler/guide/concepts/pipeline/` | redirected | #334 |
| `/examples/` | `/helia-profiler/examples/` | served | #334 |
| `/examples/atomiq110-npu-profiling/` | `/helia-profiler/examples/atomiq110-npu-profiling/` | served | #334 |
| `/examples/basic-profiling/` | `/helia-profiler/examples/basic-profiling/` | served | #334 |
| `/examples/engine-comparison/` | `/helia-profiler/examples/engine-comparison/` | served | #334 |
| `/examples/interactive-python/` | `/helia-profiler/examples/interactive-python/` | served | #334 |
| `/examples/per-layer/` | `/helia-profiler/examples/per-layer/` | served | #334 |
| `/examples/power-profiling/` | `/helia-profiler/examples/power-profiling/` | served | #334 |
| `/examples/tflm-baseline/` | `/helia-profiler/examples/tflm-baseline/` | served | #334 |
| `/examples/toolchain-comparison/` | `/helia-profiler/examples/toolchain-comparison/` | served | #334 |
| `/getting-started/` | `/helia-profiler/getting-started/` | served | #334 |
| `/getting-started/first-profile/` | `/helia-profiler/getting-started/first-profile/` | served | #334 |
| `/getting-started/install/` | `/helia-profiler/getting-started/install/` | served | #334 |
| `/getting-started/quickstart/` | `/helia-profiler/getting-started/quickstart/` | served | #334 |
| `/guide/analysis-comparison/` | `/helia-profiler/guide/analysis-comparison/` | served | #334 |
| `/guide/boards/` | `/helia-profiler/guide/boards/` | served | #334 |
| `/guide/configuration/` | `/helia-profiler/guide/configuration/` | served | #334 |
| `/guide/engines/` | `/helia-profiler/guide/engines/` | served | #334 |
| `/guide/memory/` | `/helia-profiler/guide/memory/` | served | #334 |
| `/guide/model-explorer/` | `/helia-profiler/guide/model-explorer/` | served | #334 |
| `/guide/output/` | `/helia-profiler/guide/output/` | served | #334 |
| `/guide/pmu-counters/` | `/helia-profiler/guide/pmu-counters/` | served | #334 |
| `/guide/power/` | `/helia-profiler/guide/power/` | served | #334 |
| `/guide/toolchains/` | `/helia-profiler/guide/toolchains/` | served | #334 |
| `/guide/transports/` | `/helia-profiler/guide/transports/` | served | #334 |
| `/guides/` | `/helia-profiler/guide/in-depth/` | redirected | #334 |
| `/guides/executorch-ns-kernels/` | `/helia-profiler/guide/in-depth/executorch-ns-kernels/` | redirected | #334 |
| `/guides/memory-placement-tuning/` | `/helia-profiler/guide/in-depth/memory-placement-tuning/` | redirected | #334 |
| `/guides/validating-a-board-setup/` | `/helia-profiler/guide/in-depth/validating-a-board-setup/` | redirected | #334 |
| `/reference/` | `/helia-profiler/reference/` | served | #334 |
| `/reference/analyze/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/api/` | `/helia-profiler/reference/api/helia_profiler/` | redirected | #330 (merged as #333) |
| `/reference/api/config/` | `/helia-profiler/reference/api/helia_profiler/config/` | redirected | #330 (merged as #333) |
| `/reference/api/errors/` | `/helia-profiler/reference/api/helia_profiler/errors/` | redirected | #330 (merged as #333) |
| `/reference/api/platform/` | `/helia-profiler/reference/api/helia_profiler/` | redirected | #330 (merged as #333) |
| `/reference/api/power-results/` | `/helia-profiler/reference/api/helia_profiler/power/` | redirected | #330 (merged as #333) |
| `/reference/api/profile/` | `/helia-profiler/reference/api/helia_profiler/` | redirected | #330 (merged as #333) |
| `/reference/api/results/` | `/helia-profiler/reference/api/helia_profiler/results/` | redirected | #330 (merged as #333) |
| `/reference/api/session/` | `/helia-profiler/reference/api/helia_profiler/` | redirected | #330 (merged as #333) |
| `/reference/boards/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/cache/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/compare/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/configuration/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/doctor/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/engines/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/issue-codes/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/ports/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/power-on/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/probes/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/profile/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/target/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/validate/` | `/helia-profiler/reference/` | redirected (deferred) | #331 |
| `/reference/wire-protocol/` | `/helia-profiler/reference/wire-protocol/` | served | #334 |

61 routes: 27 served, 34 redirected, 14 of those still deferred.

## Path moves this port applied

| From (legacy) | To (site) | Why |
| --- | --- | --- |
| `architecture/*` | `guide/concepts/*` | helia-ui matches a section by one path prefix, and the plan puts these pages in User guide > Concepts. |
| `architecture/compatibility-baseline.md` | `reference/compatibility-baseline.mdx` | The plan puts the compatibility matrix in Reference. |
| `guides/*` | `guide/in-depth/*` | Same prefix rule; the group keeps its own overview until the Phase 3 content issues re-slot it. |
