# Contributor architecture map

Start with the published [Concepts](../astro-site/src/content/docs/guide/concepts/index.mdx)
for subsystem responsibilities and [Pipeline](../astro-site/src/content/docs/guide/concepts/pipeline.mdx)
for the profile stage list. This map locates the retained-run path beside it.
Paths below are relative to `src/helia_profiler/`; tests are relative to the repo.

## Entry points and boundaries

| Operation | Owner and handoff | Host/device boundary |
| --- | --- | --- |
| `profile(config)`, `Session.profile()` | `api.py` → `profiler.run_profile` → `build_default_pipeline` → `PipelineRunner`; returns `ProfileResult` and writes reports | The full path resolves the probe, builds, flashes and captures, including optional power stages. |
| `Session.freeze()` | `session.py` resolves configuration; `frozen_run.freeze_config` records model/local-input hashes, resolved settings, runtime and environment identity in `FrozenRun` | Host-only planning and local file inspection; no firmware build or device access. |
| `build(plan, fixture, ...)`, `FrozenRun.build()` | `compiled_build.py` → `_fixture_build.build_fixed_fixture` → shared stages; verifies and returns `CompiledBuild` | Host compilation, with explicit fixture bytes and method. LiteRT/heliaRT consume a prepared runtime supplied by the caller or already in the cache; preparing that runtime is a separate operation. No flashing or capture. |
| `run_build(compiled, ...)`, `CompiledBuild.run()` | `compiled_build.py` checks the build and request, then `fixture_capture.capture_fixture`; returns `RunResult` | Device access begins here, with the caller's guard and explicit probe serial. Executes the retained build without rebuilding; optional power uses its capture route. |
| Save/load | `FrozenRun.save/load` owns plan JSON; `CompiledBuild.save/load` owns build JSON and delegates pinned files to `_fixture_record.py`; `RunResult.save` and `load_run` delegate to `run_record.py` | Local file I/O. Build/run records resolve paths through caller-provided roots and verify retained evidence. `Session.save/load` stores reusable intent, a different document from a frozen plan. |
| `api.load_result`, `api.observations` | `records.py` dispatches native profile/run loading; `_result_view.py` and `_run_view.py` map loaded facts into portable observations | Offline reading and mapping. Observations do not perform a new capture or establish verification merely by conforming to a schema. |

The public Python surface is the package root's `__all__`, pinned by
`tests/test_api_stability.py`. Internal `fixture_*` and `_fixture_*` module names
remain implementation details; root-exported `Fixture*` types are public.

## Shared work and verification owners

Both build paths use `PipelineContext` and `PipelineRunner` in `pipeline.py`.
The retained build composes `ResolvePlatformStage`, `PrepareEngineStage`,
`PlanMemoryStage`, `GenerateFirmwareStage` and `BuildFirmwareStage`, with its
own identity and fixture-binding stages. It does not run the full profile
pipeline. Put a change common to both builds in the owning shared stage,
engine adapter or firmware module, and check both callers.

| Concern | Owner | Focused tests |
| --- | --- | --- |
| Intent resolution and frozen plan | `session.py`, `config/`, `frozen_run.py` | `tests/test_session.py`, `tests/test_frozen_run.py` |
| Engine preparation and ownership | `engines/__init__.py` dispatches; `litert.py`, `executorch.py`, `helia_rt/`, `helia_aot/` own adapters; `base.py`, `cmsis_nn.py`, `ethos_u.py`, `semver.py` are shared | `tests/test_package_layout.py`, `tests/contracts/test_engine_artifact_types.py`, engine-specific tests |
| Build binding and retained files | `compiled_build.py` checks plan/build agreement; `_fixture_record.py` verifies pinned build evidence; `fixture_runtime.py` verifies prepared-runtime declarations and link proof | `tests/test_compiled_build.py`, `tests/test_compiled_build_runtime.py`, `tests/unit/test_fixture_record.py` |
| Semantic identity | `identity.py` defines facts; `frozen_run.py` owns intent identity; `fixture_identity.py` derives build/capture identity; `profile_identity.py` derives profile identity; `_identity_hash.py` shares hash rules | `tests/test_identity.py`, `tests/unit/test_fixture_identity.py`, `tests/test_profile_identity.py` |
| Output comparison | `expect.py` owns `Expect`, references, tolerances and `Verification`; `compiled_build.py` applies the requested comparison after a successful capture | `tests/test_expect.py`, `tests/test_compiled_build.py` |
| Shared run rules | `_run_contracts.py` owns capture-count agreement, power admission (including build-time route checks) and lifecycle/intent/rail identity enrichment. `compiled_build.py` and `run_record.py` supply existing fixture values; the rules do not depend on those callers. | `tests/test_run_power.py`, `tests/test_run_record.py`, `tests/test_package_layout.py` |
| Saved run evidence | `run_record.py` reloads the build, checks pinned capture files, recomputes identity and output comparison; `records.py` verifies profile manifests | `tests/test_run_record.py`, `tests/test_result_view_contract.py` |
| Gated Joulescope resources | `power/joulescope/_gated_resources.py` private `_GatedCaptureResources` owns stream attempts, the poller thread and processing admission/drain; `capture_gated` releases the device in its outer finally and retains callback evidence, READY synchronization, hook error precedence and post-drain effect ordering | `tests/test_gated_capture_lifecycle.py` (real threads), `tests/test_power.py` (deterministic numerical timelines), `tests/test_fullrate_recorder.py` |
| Gated Joulescope control | `power/joulescope/_gated_state.py` owns poller-only gate history, pulse classification and completion guard timing; orchestration performs callback effects and retained-counter validation, then reads stable control evidence after drain. READY retains its condition-protected latest-sample semantics. | `tests/test_gated_capture_control.py`, `tests/test_power.py`, `tests/unit/test_fixture_completion.py` |
| Gated Joulescope measurement | `power/joulescope/_gated_selection.py` selects retained windows and shares completion validation; `_gated_result.py` composes numerical helpers, classifies outcomes and assembles results. Orchestration retains debug output, elapsed-time reads and full-rate recorder calls. | `tests/test_gated_reduction.py`, `tests/test_gated_measurement_analysis.py`, `tests/test_power.py` |
| Portable observations | `_result_view.py`, `_run_view.py` map verified native records and preserve measurement scope | `tests/test_result_view_contract.py` |

Execution retains guard/settling checks and translates count failures to `CaptureError`.
Persistence retains file verification, retained-energy/completion checks, identity-document
comparison and `RunResultError` classification. Sharing admission rules does not make
these caller-specific checks interchangeable. Public build and result definitions
remain in `compiled_build.py`.

A successful capture and numerical acceptance are separate facts. Build/file
verification establishes agreement with retained bytes; `Expect` supplies the
numerical reference and tolerance. Keep these responsibilities distinct when
changing a loader or observation mapping.

For station setup, fixture construction, evidence retention, runtime preparation
and capture contracts, use [fixed-fixture.md](fixed-fixture.md). Power-specific
contracts are in [fixture-energy.md](fixture-energy.md). Use the device guard
through `tools/software_only.py` before importing HPX in software-only capture
tests; the pytest marker alone does not install it. Template changes also need
the compile gates described in [compile-gate.md](compile-gate.md).

The gated capture owner starts streams before the poller and the start hook.
Its stop closes admission to processing, drains work already admitted, joins
with the existing bound, then disables attempted controls and unsubscribes attempted subscriptions. Application
callbacks execute without the admission lock held, so a GO publication can wait
for a vendor callback on another thread. A callback already in flight must
return before teardown finishes. Control-enable attempts are distinct from
subscription attempts; a failed subscribe never authorizes disabling its untouched
control. Raw teardown likewise follows actual startup and control-enable attempts,
not the cross-check request alone. Partial setup reaches stream cleanup; device
release is protected immediately after open and remains after reduction.
A blocked vendor read can outlive the bounded join, but its result cannot invoke
application callbacks or mutate evidence after shutdown. See
[capture-timeline.md](capture-timeline.md) for the distinction between host
ownership, physical timing and vendor-call cancellation.
