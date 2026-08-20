# heliaPROFILER Agent Guide

This file is for AI agents and automated contributors working in
`helia-profiler`. It captures the architectural choices and repo workflows that
should stay stable unless there is a deliberate design change.

## Purpose

`helia-profiler` (`hpx`) is a cross-platform CLI tool that profiles LiteRT
(TFLite) flatbuffer models and ExecuTorch (`.pte`) programs on Ambiq Apollo
hardware. It captures per-layer PMU counter breakdowns and optional power
measurements.

It is **not** a build system, SDK exporter, or application framework. It is a
profiler.

## Architectural Rules

### One Engine Per Run

The user explicitly selects one inference engine (`tflm`, `helia-rt`,
`helia-aot`, `executorch`) per invocation. Do not add multi-engine
orchestration.

### Explicit Over Auto-Magic

Prefer clear user-specified configuration over brittle auto-detection. If
something needs to be known (arena size, memory placement), the user provides
it or the firmware reports it at runtime with a clear error.

### Immutable Config

The `ProfileConfig` is resolved once at startup and frozen. No field should be
mutated during execution. Do not add mutable global state.

### Engine Isolation

Each engine adapter runs in its own subprocess or module boundary. If an
engine tool fails, the error propagates naturally. Do not monkey-patch
`sys.exit` or swallow exceptions from engine tools.

### NSX as Build Backend

Firmware is built using the NSX pipeline (configure → build → flash). Prefer
the `neuralspotx` Python API when available. Fall back to `subprocess.run()`
calling the `nsx` CLI. Never use `os.system()` or `shell=True`.

### heliaRT NSX Wrapper

The `HeliaRTAdapter` generates a temporary NSX module wrapper (nsx-module.yaml
+ CMakeLists.txt) so that heliaRT prebuilt static libraries appear as
`nsx::helia_rt` to the firmware build. The pinned version lives in
`engines/helia_rt/artifacts.py` — bump `HELIART_VERSION` when adopting a new release.
This shim is retired once heliaRT ships a native `nsx-module.yaml`.

### No Export Mode

`hpx` does not generate exportable examples, static libraries, or AmbiqSuite
projects. It generates temporary firmware, profiles, and reports results.

### Cross-Platform First

- `pathlib.Path` for all file paths
- `subprocess.run()` with argument lists
- `pyserial` for serial communication
- No POSIX-only assumptions

### HPX CLI Before Raw Debug Tools

Run HPX through the project environment, preferably from any directory as:

```bash
uv --directory <repo-root> run hpx ...
```

For probe and target diagnostics, prefer HPX's non-interactive helpers before
reaching for raw SEGGER Commander sessions:

+ `hpx probes list [--board <board>] [--json]`
+ `hpx probes match --board <board> [--jlink-serial <serial>]`
+ `hpx ports list [--all] [--json]`
+ `hpx target reset --board <board> [--jlink-serial <serial>] [--kind debug|swpoi]`

Avoid raw `JLinkExe` in agent workflows unless HPX lacks the needed operation.
If raw `JLinkExe` is unavoidable, use a non-interactive script that ends with
`exit`, set a timeout, and prefer adding a wrapper in `target/probe/jlink.py` afterward.

## Module Responsibilities

| Module | Responsibility |
| --- | --- |
| `api.py` | `profile()` — public programmatic entry point, returns `ProfileResult` |
| `cli/` | Typer command package (`app.py` + one module per command); delegates to `api.profile()` and the console layer |
| `config.py` | `ProfileConfig` dataclass, YAML + CLI merge |
| `compatibility.py` | Typed HPX compatibility baseline (`data/compatibility-baseline-v1.json`) — qualified NSX project/module/engine refs and override classification |
| `results/` | Typed result models, workflow artifacts, and bundle manifests |
| `evaluation/` | Model analysis, verified comparison, validity, and regression profiles |
| `profiler.py` | Pipeline composition and logging setup |
| `pipeline.py` | `PipelineContext`, `Stage` protocol, `PipelineRunner` |
| `engines/` | One adapter per inference engine; `EngineAdapter` protocol and `EngineArtifacts` in `base.py` (`NsxModuleRef` lives in `results/models.py`) |
| `firmware/` | NSX app generation from Jinja templates |
| `capture/` | Capture orchestration, PMU parser → `PmuResult`, target readiness, power terminal records (transports themselves live in `transport/`) |
| `power/` | Power measurement drivers, `PowerResult` in `base.py` |
| `report/` | CSV, JSON, run summary, result manifest, Model Explorer overlays |
| `console/` | All Rich rendering — progress, tables, results, comparisons. The library never prints; the CLI does |
| `stages/` | One module per pipeline stage; `profiler.build_default_pipeline()` owns the order |
| `platform/` | SoC families, board registry, capabilities, and custom overlays |
| `transport/rtt.py` | RTT capture lifecycle; direct control-block access and low-level test patch points live in `rtt_control.py` |
| `target/probe/jlink.py` | SEGGER J-Link helpers (discovery, reset, SWO commands) |
| `nsx.py` | NSX build-system subprocess wrapper |
| `doctor.py` | Host toolchain/version checks (`hpx doctor`) — never raises, informational only |
| `redact.py` | Deterministic redaction of paths, URL credentials/tokens, secret assignments, and device serials for diagnostics output |
| `support_bundle.py` | `hpx doctor --bundle` field-diagnostics collector and deterministic archive writer/verifier |
| `errors.py` | Typed error hierarchy with `hint` field |
| `session.py` | Immutable, branchable `Session` API for notebooks and scripts (backs `docs/reference/api/session.md`) |
| `validation/` | `hpx validate` hardware-in-the-loop harness — case matrix, runner, report, and portable bundle |
| `dependencies.py` | Locked-dependency preparation (`prepare_locked_dependencies`) for reproducible firmware builds |

### Data Contract

All structured data between pipeline stages uses frozen dataclasses from
the `results/` package, never bare `dict[str, Any]`. The main exception is
`LayerResult.counters: dict[str, float]` — PMU counter names are dynamic.

## Working Rules

- Prefer focused modules. Extract when a file accumulates multiple concerns.
- Keep the `cli/` modules thin — they parse args, call `api`/`Session`, and
  hand results to `console/` for rendering. No profiling logic in commands.
- Use `subprocess.run()` with argument lists for all external tool calls.
- Use dataclasses (frozen when possible) for internal models.
- Tests should be fast, local, and mock external tools.
- Use Conventional Commits for all commit messages.

## Dependency Security Floors

Dependabot alerts on `uv.lock`, and alerts often land on transitive packages
HPX never names directly. Hold the fix in `[tool.uv] constraint-dependencies`
in `pyproject.toml` — one entry per advisory, set to the first patched release,
commented with the advisory and the path that reaches it — then re-run
`uv lock`. Constraints only shape this repo's resolution; they do not leak into
downstream consumers' resolves, so a real runtime floor still belongs in
`[project] dependencies`.

`tests/test_security_advisories.py` asserts `uv.lock` honours every declared
floor — across every per-marker resolution fork, not just the first entry for a
name — and fails on floors that no longer apply, so stale entries get dropped
rather than accumulating. Dropping a floor is a two-place edit: remove the
constraint and its assertion in `test_security_floors_are_declared`.

`uv lock` strips the `# x-release-please-version` marker from the
`helia-profiler` entry in `uv.lock`. Restore it before committing — the
`package` CI job requires exactly one marker.

## Compatibility Baseline Pins

NSX resolves a module-level registry revision ahead of an app's
project-level override — including the *packaged* registry's module
defaults. A baseline pin therefore must reach the generated
`module_registry.modules` entry, not just the project entry
(`_render_module_registry` handles this for every app module owned by a
pinned project). `prepare_locked_dependencies` independently verifies the
lock's resolved commits against the baseline and raises `VersionError` on
drift, so a resolver disagreement fails loudly instead of shipping
unqualified sources under a QUALIFIED claim. When adding a baseline
project, assert the pin in the *lock/manifest module entry* in tests —
never encode the currently-observed registry shape as a golden.
