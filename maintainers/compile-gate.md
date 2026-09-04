# Rendered-firmware compile gate (#187 Tier 1)

`tests/contracts/test_render_compile.py` syntax-checks every rendered
firmware translation unit with host GNU `g++ -fsyntax-only -std=gnu++17
-Wall -Werror -Wformat` against the hpx-owned stub include tree in
`tests/fixtures/compile_stubs/`. It exists because the render snapshots pin
bytes, not compilability: an undeclared identifier in one render arm, a
printf format/argument mismatch, or an orphaned variable all render fine and
used to surface only at a bench build (#171 round 2 is the canonical case).

## What it compiles

- The full snapshot scenario matrix from
  `test_firmware_render_snapshots.py` (every SoC x transport x engine,
  power_only and busy_loop variants), verified by set-equality against the
  committed snapshot keys.
- The wire census matrix from `test_wire_protocol._MATRIX` (every
  condition-variant override set: PSRAM placements, heliaAOT external
  arenas and const blobs, Apollo3 burst, trace/auto-window/power-sync/hb-ms,
  INA228).
- `hpx_pmu_profiler.cc` per SoC — the second TU of every TFLM/heliaRT app.
- One full-resolver TU: the complete `firmware/op_resolver.py`
  `_ALL_REGISTRATIONS` plan (mode="all"), against a stub resolver that
  declares each `Add*` method explicitly, so a renamed registration fails
  the gate.

Scenarios are deduplicated by rendered text (~150 enumerated cases → ~90
unique TUs); the whole module runs in about a second.

## Stub maintenance rule

A template that starts using a new vendor symbol fails the gate until
`tests/fixtures/compile_stubs/` declares it — loud by construction, and the
stub diff rides the template PR, the same discipline as the wire census.
Stubs are declarations only (no vendor code, no license surface); where the
real NSX/CMSIS headers are available (`~/.cache/nsx/modules/...`), stub
signatures mirror them. Two deliberate host-width deviations exist for the
LP64 host (`Model::version()` as `unsigned long`, `nsx_psram_info_t
.base_address` as `uintptr_t`) — both are commented at the declaration.

`AM_PART_*` comes from the compile line (as production's toolchain supplies
it), which is what drives the part gates in the stubs: Apollo3-only burst
API, Apollo510-only `am_hal_debug_*`, no `am_hal_pwrctrl_sram_config` /
`_control` on Apollo3, and the per-part `NSX_CACHE_HAS_*` capability values.

`hpx_printf` gets its `format(printf, ...)` attribute from a force-included
per-case prelude (the template's own definition has none), which is what
arms `-Wformat` on the profiler's output path.

## Known render bugs (expected failures)

`_EXPECTED_RENDER_BUGS` in the test module is a strict ledger of template
defects the census sweep found (unused `kArenaPsramOffset` on
weights-only-PSRAM renders, `psram_info` undeclared in the ExecuTorch PSRAM
metadata block, unused-but-set `hpx_arena_psram_offset_N` for a blob-less
PSRAM arena region). Each listed case must keep failing; once the template
is fixed the gate errors until the entry is removed.

## Scope

The gate needs a real GNU g++ and probes for one (`g++`, `g++-14`,
`g++-13`), rejecting Apple clang (`--version` contains "clang") and MinGW
(`-dumpmachine` contains "mingw") — their `-Wall`/format models differ and
the module skips there. So: Linux/GNU hosts run it in the normal suite;
macOS and Windows skip. A clang lane with its own flag set is possible
future work.

## Tier 2 — real-toolchain ground truth (`compile_hw`)

`tests/contracts/test_render_compile_hw.py` compiles a 12-row matrix of the
most template-diverse renders (engine × template family × power/busy, over
apollo510 + apollo330P) with the **real** `arm-none-eabi-g++` against a
**real** cached dependency workspace's include set — real vendor HAL
headers, real `-mcpu`, NSX's own `-Wall` upgraded to `-Werror`. About
1.5 s per TU.

Run it on the bench (or any machine with warm workspaces):

```bash
uv run pytest -m compile_hw tests/contracts/test_render_compile_hw.py
```

How it works, and the rules it must keep:

- **The command comes from the workspace, the code from the checkout.**
  DEFINES/INCLUDES/FLAGS are parsed from the workspace's `build.ninja`
  per-TU stanza and the compiler from `rules.ninja` (ninja
  `${LAUNCHER}`-style prefixes stripped) — no configure step, no guessing
  from PATH. The TUs themselves are rendered from the current checkout;
  the workspace's `src/` is never trusted.
- **Read-only on the cache.** The test shares the bench's warm workspace
  cache (`HPX_CACHE_DIR`); it must never write under it. Scratch TUs go to
  pytest's `tmp_path`. `-fsyntax-only` means no objects and no
  launcher/ccache interplay.
- **Vendored headers are `-isystem`.** Workspace `modules/` include dirs
  are demoted so diagnostics located *inside* vendor headers (e.g. the
  `AM_SHARED_RW` redefinition between `nsx_mem.h` and `am_hal_global.h`,
  present in every production build) don't gate our rendered code, which
  compiles at full `-Werror`.
- **Legs skip with a named reason** when their workspace is absent, has no
  fingerprinted (post-#212) layout, records no baseline fingerprint, was
  built against a different compatibility baseline than the checkout
  (candidates are tried newest-first, so a stale branch's newer workspace
  cannot shadow a matching older one), or, for a power-only leg, was
  configured without the `hpx_profiler_power` target — a bench without a
  power instrument never builds it, so on most boards the power legs are
  expected to skip rather than fail — the test never builds a workspace
  (a cold sync is minutes of network+compile; wrong cost profile for a
  compile gate). Warm a leg by running any profile/validate with that
  (board, toolchain, engine) combo. **A partial run is visible**: skipped
  legs surface as a pytest warning every run, and
  `HPX_COMPILE_HW_REQUIRE_ALL=1` (any value except `0`/`false`/`no`/`off`
  arms it) turns any partial run — **including a zero-leg run from a
  wiped or mispointed cache** — into a failure: the setting for a bench
  whose full matrix should be warm.
- **The expected-bugs ledger is strict both ways**, same as Tier 1: an
  entry keeps its case red-with-reason, and a case that starts compiling
  fails the suite until the entry is removed.
- **Matrix drift is loud**: `test_matrix_covers_every_engine_family` binds
  the rows to the render engine enumeration, so a new engine (e.g. an
  atomiq110 NPU backend) fails the gate until it gets a Tier-2 leg or a
  recorded reason.

Toolchain path and `--version` are recorded in failure output, not enforced — a
compiler upgrade changing warning behavior is exactly what the nightly
should surface, with the ledger absorbing deliberate acceptances.
