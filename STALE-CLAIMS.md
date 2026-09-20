# Stale-claims inventory (#334)

Collected while porting the legacy pages, at `origin/docs-migration` (`8420154`).
Nothing here was changed by the port: it is a mechanical move, and the prose
fixes belong to the Phase 3 content issues. Patterns searched over `docs/**.md`:
`no longer`, `new in <version>`, `experimental`, `coming soon`, `TODO`/`FIXME`/`HACK`.

Totals: 18 matches, 14 on pages this port moved and 4 on pages it skipped.
No `coming soon` and no `new in 0.2.0` match exists in `docs/` today, so the
plan draft's "ExecuTorch marked New in 0.2.0" no longer describes the tree.

## On pages this port moved (38)

| File:line | Kind | Phrase |
| --- | --- | --- |
| `docs/architecture/compatibility-baseline.md:91` | change narration | long-term landing but is no longer load-bearing. |
| `docs/architecture/compatibility-baseline.md:194` | maturity label | from its experimental FPGA target representative of production silicon, or |
| `docs/examples/atomiq110-npu-profiling.md:1` | maturity label | # Atomiq110 NPU Profiling (Ethos-U85, Experimental) |
| `docs/examples/atomiq110-npu-profiling.md:7` | maturity label | !!! warning "Experimental FPGA target" |
| `docs/examples/index.md:80` | maturity label | -   :material-memory:{ .lg .middle } __Atomiq110 NPU Profiling (Experimental)__ |
| `docs/examples/index.md:84` | maturity label | Profile a Vela-compiled model on the experimental Atomiq110 FPGA target. |
| `docs/guide/boards.md:19` | maturity label | \| `atomiq110_fpga_turbo` **(experimental)** \| atomiq110 \| Cortex-M55 \| Full Armv8-M \| Yes \| No \| Preview \| |
| `docs/guide/boards.md:49` | maturity label | !!! warning "Experimental Atomiq110 support" |
| `docs/guide/boards.md:51` | maturity label | experimental. It is best-effort, is not a release blocker, and may change |
| `docs/guide/engines.md:177` | change narration | `CALL_ONCE` / `VAR_HANDLE` / `ASSIGN_VARIABLE` / `READ_VARIABLE` no longer need |
| `docs/guide/memory.md:241` | change narration | planned arena that fits DTCM only because the other buffers no longer have |
| `docs/guide/output.md:351` | change narration | Because `bss` no longer includes the reservation, an Apollo5 run recorded |
| `docs/guide/transports.md:36` | change narration | !!! note "Transport choice no longer affects power numbers" |
| `docs/guide/transports.md:41` | change narration | no longer biases current/energy results. That transport-dependent power |

14 matches.

## On pages this port skipped (23, owned by #330/#331/#08)

| File:line | Kind | Phrase |
| --- | --- | --- |
| `docs/reference/api/index.md:36` | maturity label | - `experimental` — public and documented, but still evolving before 1.0. Pin an |
| `docs/reference/api/platform.md:5` | maturity label | overlays). This surface is experimental: it is the seed of a future |
| `docs/reference/boards.md:31` | maturity label | !!! warning "Experimental Atomiq110 support" |
| `docs/reference/boards.md:33` | maturity label | experimental. It is best-effort, is not a release blocker, and may change |

4 matches.

## Adjacent, outside the five phrase patterns

Recorded because a reviewer will ask, not because this port touched them:

- `docs/index.md:26` carries an "Alpha" product warning. Home is #320 item 08.
- `docs/guide/boards.md:15-26` gives every board a `Preview` status column, and
  `docs/guide/boards.md:60` says the status is documentation-only and does not
  change target selection. The plan calls for a hardware-validated versus
  compile-only distinction instead; that is the compatibility work, not this port.
- `docs/guide/power.md:837` refers to `pre-v0.2.0 nsx-sensors` behaviour, which
  is a dependency version claim rather than a heliaPROFILER one.
