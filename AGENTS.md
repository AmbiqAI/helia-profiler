# heliaPROFILER Agent Guide

For AI agents and automated contributors. `hpx` profiles LiteRT (`.tflite`)
and ExecuTorch (`.pte`) models on Ambiq Apollo boards: it builds temporary NSX
firmware, flashes it, captures per-layer PMU counters and optional power, and
writes a result bundle. It is a profiler, not a build system, SDK exporter, or
application framework. Design rationale and module layout live in
the Concepts section of the docs site (`astro-site/src/content/docs/guide/concepts/`); this file holds only what you cannot derive from the code.

## Commands

```bash
uv sync --locked --all-groups --extra aot --extra analysis
uv run ruff check . && uv run ruff format --check .
uv run ty check src/helia_profiler tests
uv run pytest -q                      # unit suite; hardware/compile_hw markers deselected
pre-commit run --all-files            # identical to the CI pre-commit job
uv --directory <repo-root> run hpx ...   # run the CLI from anywhere
```

Regenerate after changing the source they derive from; CI fails on drift:

```bash
uv run python tools/docs/check_reference.py --check   # committed reference JSON vs the package
uv run python tools/gen_wire_protocol_reference.py  # wire registry → the Reference wire-protocol page
HPX_UPDATE_SNAPSHOTS=1 uv run pytest tests/contracts/test_firmware_render_snapshots.py tests/contracts/test_report_golden.py
```

Software-only capture tests need the device guard installed before HPX is
imported: `uv run python tools/software_only.py pytest <test> -q`. A pytest
marker alone does not guard anything.

## Architectural rules

- **One engine per run.** The user selects `tflm`, `helia-rt`, `helia-aot`, or
  `executorch` explicitly. No multi-engine orchestration, no auto-detection.
- **Explicit over auto-magic.** Arena size, memory placement, and the like
  come from the user or from firmware at runtime with a clear error.
- **`ProfileConfig` is resolved once and frozen.** No mutable global state.
- **Engine isolation.** Each adapter runs in its own subprocess or module
  boundary; engine failures propagate. Never monkey-patch `sys.exit` or
  swallow exceptions from engine tools.
- **NSX is the build backend** (configure → build → flash). Prefer the
  `neuralspotx` Python API, fall back to `subprocess.run([...])` on the `nsx`
  CLI. Never `os.system()` or `shell=True`.
- **Data between stages is frozen dataclasses from `results/`**, never bare
  dicts. The one exception is `LayerResult.counters` (PMU names are dynamic).
- **Cross-platform first:** `pathlib.Path`, argument-list subprocesses,
  `pyserial`, no POSIX-only assumptions.
- **`cli/` stays thin:** parse args, call `api`/`Session`, hand results to
  `console/`. The library never prints; only the console layer does.
- The heliaRT NSX wrapper (`engines/helia_rt/`) is a shim until heliaRT ships
  a native `nsx-module.yaml`; bump `HELIART_VERSION` in `artifacts.py` to
  adopt a release.

## Hardware and probes

Use HPX's non-interactive helpers before raw SEGGER tooling: `hpx probes
list|match`, `hpx ports list`, `hpx target reset`. If raw `JLinkExe` is
unavoidable, script it non-interactively with a timeout and an `exit`, then
add the operation to `target/probe/jlink.py`. The bench nightly and its
runner contract are described in `maintainers/hardware-ci.md`.

## Code and commit style

- Conventional Commits.
- Tests are fast, local, and mock external tools.
- Default to no comment. Add one only when the **why** is non-obvious: a
  hidden constraint, an invariant, or a workaround. When the why is an
  external fact, link it: `# WORKAROUND helia-aot#349: their module checks
  ARM_NN_*`. No third-party version numbers, bench numbers, or review history
  in code or docstrings; those belong in
  `astro-site/src/content/docs/reference/compatibility-baseline.mdx`, the issue, or git history.
  `rg WORKAROUND` is the cleanup pass.
- Every `TODO(...)`/`FIXME(...)`/`HACK(...)` needs an issue or name reference
  (pre-commit enforces it).

## Gotchas

- `uv lock` strips the `# x-release-please-version` marker from the
  `helia-profiler` entry in `uv.lock`. Restore it before committing; the
  `package` CI job requires exactly one.
- Dependabot floors go in `[tool.uv] constraint-dependencies` in
  `pyproject.toml`, one per advisory at the first patched release, then
  `uv lock`. `tests/test_security_advisories.py` checks every floor is honoured
  and fails on floors that no longer apply, so dropping one is a two-place
  edit (constraint + its assertion). A real runtime floor still belongs in
  `[project] dependencies`.
- Compatibility baseline pins must reach the generated
  `module_registry.modules` entry, not just the project entry: NSX resolves a
  module-level registry revision ahead of the project override.
  `prepare_locked_dependencies` verifies resolved commits against the baseline
  and raises `VersionError` on drift. When adding a baseline project, assert
  the pin in the lock/manifest module entry in tests; never golden the
  observed registry shape.
- A firmware template that starts using a new variable or vendor symbol must
  also reach both compile gates: Tier 1 (`tests/contracts/test_render_compile.py`
  + `tests/fixtures/compile_stubs/`) and Tier 2
  (`tests/contracts/test_render_compile_hw.py`, bench only). Tier 1 passing
  does not imply Tier 2 renders.
- A page under `astro-site/src/content/docs/` is a published page; maintainer
  material lives in `maintainers/`.
