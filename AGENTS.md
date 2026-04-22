# heliaPROFILER Agent Guide

This file is for AI agents and automated contributors working in
`helia-profiler`. It captures the architectural choices and repo workflows that
should stay stable unless there is a deliberate design change.

## Purpose

`helia-profiler` (`hpx`) is a cross-platform CLI tool that profiles LiteRT
(TFLite) flatbuffer models on Ambiq Apollo hardware. It captures per-layer PMU
counter breakdowns and optional power measurements.

It is **not** a build system, SDK exporter, or application framework. It is a
profiler.

## Architectural Rules

### One Engine Per Run

The user explicitly selects one inference engine (`tflm`, `helia-rt`,
`helia-aot`) per invocation. Do not add multi-engine orchestration.

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
`nsx::heliart` to the firmware build. The pinned version lives in
`engines/helia_rt.py` — bump `HELIART_VERSION` when adopting a new release.
This shim is retired once heliaRT ships a native `nsx-module.yaml`.

### No Export Mode

`hpx` does not generate exportable examples, static libraries, or AmbiqSuite
projects. It generates temporary firmware, profiles, and reports results.

### Cross-Platform First

- `pathlib.Path` for all file paths
- `subprocess.run()` with argument lists
- `pyserial` for serial communication
- No POSIX-only assumptions

## Module Responsibilities

| Module | Responsibility |
| --- | --- |
| `api.py` | `profile()` — public programmatic entry point, returns `ProfileResult` |
| `cli.py` | Thin argparse CLI, delegates to `api.profile()` |
| `config.py` | `ProfileConfig` dataclass, YAML + CLI merge |
| `results.py` | Typed result models (`PmuResult`, `ProfileResult`, `RunMetadata`, etc.) |
| `profiler.py` | Pipeline composition and logging setup |
| `pipeline.py` | `PipelineContext`, `Stage` protocol, `PipelineRunner` |
| `engines/` | One adapter per inference engine; `NsxModuleRef` in `base.py` |
| `firmware/` | NSX app generation from Jinja templates |
| `capture/` | Serial data reader, PMU parser → `PmuResult` |
| `power/` | Power measurement drivers, `PowerResult` in `base.py` |
| `report/` | CSV, JSON, terminal summary, Model Explorer overlays |
| `stages/` | Ordered pipeline stages s01–s08 |
| `platform.py` | SoC families, board registry, capabilities |
| `jlink.py` | SEGGER J-Link helpers (discovery, reset, SWO commands) |
| `nsx.py` | NSX build-system subprocess wrapper |
| `errors.py` | Typed error hierarchy with `hint` field |

### Data Contract

All structured data between pipeline stages uses frozen dataclasses from
`results.py`, never bare `dict[str, Any]`. The sole exception is
`LayerResult.counters: dict[str, float]` — PMU counter names are dynamic.

## Working Rules

- Prefer focused modules. Extract when a file accumulates multiple concerns.
- Keep `cli.py` thin — it parses args and calls `api.profile()`.
- Use `subprocess.run()` with argument lists for all external tool calls.
- Use dataclasses (frozen when possible) for internal models.
- Tests should be fast, local, and mock external tools.
- Use Conventional Commits for all commit messages.
