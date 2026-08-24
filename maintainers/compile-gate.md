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

## Tier 2 (pending)

Tier 2 of #187 — compiling a ~12-TU representative matrix with the real
`arm-none-eabi-g++` against a cached dependency workspace's include set,
marked for the bench/nightly (`compile_hw`) — is designed in the issue but
not yet implemented.
