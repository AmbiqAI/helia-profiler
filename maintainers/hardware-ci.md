# Hardware Validation Artifacts

`hpx validate` is the local-first entry point for hardware profiling suites.
Run it from a developer machine with boards attached first. The manual
`Hardware Validation` GitHub Actions workflow runs the same command on a
self-hosted runner and uploads the same output directory.

## Local smoke run

Preview the selected cases without touching hardware:

```bash
uv run hpx validate --list --suite smoke --boards apollo510_evb
```

Preview the two-board smoke run used by the hardware validation workflow:

```bash
uv run hpx validate --list \
  --suite smoke \
  --boards apollo510_evb,apollo330mP_evb \
  --power off \
  --jlink-serials apollo510_evb=801000001,apollo330mP_evb=801000002
```

Run the smoke suite against a connected board:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb \
  --power off \
  --output-dir results/local-validation
```

Run the same KWS smoke model on Apollo510 and Apollo330mP with explicit probe
pinning:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb,apollo330mP_evb \
  --power off \
  --jlink-serials apollo510_evb=801000001,apollo330mP_evb=801000002 \
  --output-dir results/local-validation-dual
```

The smoke suite uses RTT. No local SEGGER RTT checkout is required: hpx
resolves RTT sources from `target.segger_rtt_path` in config, then the
`SEGGER_RTT_PATH` environment variable, and otherwise falls back to the
bundled copy in `src/helia_profiler/vendor/segger_rtt/`. Set the config key
or environment variable only to override the bundled version.

Optional Joulescope capture uses the same artifact layout:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb \
  --power on \
  --output-dir results/local-validation-power
```

## Output layout

The output root contains one session-level report set and one directory per
case:

```text
results/local-validation/
├── validation_manifest.json
├── validation_report.json
├── validation_report.md
└── <case_id>/
    ├── config.yml
    ├── work/
    ├── summary.json
    ├── run_metadata.json
    ├── profile_results.csv
    ├── hpx_profile.log
    ├── hpx_stdout.log
    └── hpx_stderr.log
```

`work/` is intentionally inside the case directory. It prevents build-state
collisions when matrix cases run concurrently later, and it keeps generated
firmware artifacts next to the profile results for local debugging.

GitHub Actions uploads the same validation result files but excludes
`<case_id>/work/` from the downloadable artifact. The self-hosted runner
workspace still retains `work/` after the run; the uploaded artifact keeps the
reports, configs, logs, summaries, metadata, and CSV results without carrying
the generated firmware build tree.

## Manifest contract

`validation_manifest.json` is the machine-readable bundle index. It is
portable: artifact paths are relative to the validation output root, so the
same file works on a local Mac, in a downloaded GitHub Actions artifact, or in
a future static dashboard.

The initial schema includes:

- `schema_version`
- `generated_at`
- `hpx_version`
- `repo.sha`, `repo.branch`, and `repo.dirty` when available
- `run.origin` (`nightly`, `manual`, `local`, or generic `ci`) plus GitHub run metadata
- `validation` options such as suite, selected axes, timeout, and output dir
- `summary` pass/fail/skip counts
- `cases` with identity, status, headline metrics, resource usage, and artifact paths

Schema v3 adds a per-case `resources` object to `validation_manifest.json` and
`validation_report.json` for dashboard ingestion: `binary_sections` contains
text/data/BSS/total sizes (BSS excludes linker-reserved regions; see the
output guide), `runtime_memory` contains firmware-reported arena and
tensor details, and `memory_plan` contains engine-agnostic region capacities,
planned usage, and named consumers (through schema v5 it also carried
used/free bytes and overflow state; see the v6 note below). The existing flat
binary and arena metrics remain available for backward-compatible
comparisons.

Schema v4 adds the powered-validation dashboard contract. Powered cases expose
`avg_power_mw`, `energy_per_inference_uj`, `inferences_per_joule`, and capture
duration alongside the existing energy/current metrics. The complete
`summary.json.power` object is also copied to each case as `power_metrics`, so
new gate-integrity and distribution fields remain available without another
aggregate-schema change. The manifest links the detailed CSV at
`<case>/detailed/power_summary.csv` and records whether it is available.

Schema v6 (#133) splits planned from measured memory. Per-case
`resources.memory_plan` is now the pre-build decision record only — its
`free`, `overflow`, and `has_overflow` keys are gone, mirroring
`summary.json` schema v3 — and `resources.memory_regions` is added: the
measured per-region occupancy from the linked ELF (used/reserved/free
against the link family's app extent, `load_image` flash bytes, and
`unattributed` sections outside every verified window). Dashboards reading
`memory_plan.regions[].free` must move to `memory_regions.regions[].free`;
the block is absent for custom SoCs, non-default linker profiles, or when
the section inventory is unavailable. #133 Phase 3 adds a third, additive
per-case key within schema v6: `resources.memory_reconciliation` (per-
consumer plan-vs-measured verdicts and per-region deltas), absent whenever
the symbol table is.

Each case also carries cross-machine provenance when available: model SHA-256,
HPX version, compiler name/version, firmware-reported `system_clock_hz`, and
the summary/metadata schema versions. These values are not folded into case
identity because doing so would prevent useful pairing; comparison and
regression profiles surface differences and can require exact matches.

Git metadata is best-effort. Missing git, source archives, or non-repository
directories do not fail validation report generation.

Scheduled GitHub Actions runs record `run.origin: nightly`; user-triggered
`workflow_dispatch` runs record `run.origin: manual`. The accompanying
`run.github` object contains the event name, repository, numeric run ID and
attempt, and a direct run URL. Dashboards can therefore separate nightly and
ad hoc validation without inferring intent from timestamps or branch names.

## Dependency source pinning

The `ns_cmsis_nn_ref` workflow input is blank by default. In that normal mode,
the workflow uses the immutable `ns-cmsis-nn` commit from HPX's checked-in
qualified compatibility baseline. This prevents the repository's moving
default branch from silently selecting sources that disagree with HPX's
qualification metadata. When supplied, the input accepts either a branch name
or a full 40-character commit SHA; release version/tag names such as `v7.26.0`
are not accepted. Every run records the requested source and its
`resolved_commit` in the validation manifest's `sources.ns-cmsis-nn` metadata.
The action passes that resolved commit explicitly to
`hpx validate --ns-cmsis-nn-ref`; each heliaRT and heliaAOT case writes it into
`engine.config.cmsis_nn_ref`, and the generated NSX manifest and `nsx.lock`
therefore pin the source actually compiled.

## Cross-machine release sweep

Before transferring a bundle or comparing another machine's results:

1. Commit or record the exact HPX revision and use a clean worktree.
2. Run `hpx doctor`, `hpx probes match`, and `hpx ports list` on that bench.
3. Pin every board to a J-Link serial and every power run to an instrument
   serial when multiple instruments are attached.
4. Preview the matrix with `hpx validate --list` and save that output.
5. Use `--repeat 3` or more for release performance/power sweeps.
6. Retain `validation_manifest.json`, reports, logs, and all per-case result
  manifests. Generated `work/` trees are useful for local diagnosis but are
  not required for portable comparison.

For AP3/AP4/AP5 coverage, run separate board-appropriate matrices rather than
forcing unsupported transport/memory combinations into one command. Include
at least one non-power smoke per board family, then power runs with both JS110
and JS320 where bench wiring supports them. JS320 digital synchronization
requires valid target I/O reference wiring; a missing reference can otherwise
look like a READY/GATE timeout.

## Manual GitHub Actions workflow

The repository includes a manually triggered `Hardware Validation` workflow.
It runs on self-hosted runners labeled:

```text
self-hosted
hpx-hardware
```

The checked-in bench defaults select the Apollo510 and Apollo330mP EVBs, enable
power capture only for Apollo510, and pin both J-Link serials plus the Apollo510
Joulescope serial. Override those workflow inputs when moving the workflow to a
different physical bench.

Use this label for a machine that has HPX-compatible hardware attached. For
the first bench, label the local Mac runner with `hpx-hardware` and attach the
Apollo510 EVB plus its inline JS320. The workflow default board input is:

```text
apollo510_evb,apollo330mP_evb
```

The workflow exposes the validation axes as manual inputs. Leave an optional
axis empty to use the selected suite's defaults; set it explicitly to override
only that axis.

- `suite`: `smoke`, `models-rt`, `models-aot`, or `complete`
- `boards`: comma-separated board IDs, default `apollo510_evb,apollo330mP_evb`
- `models`: optional comma-separated model IDs such as `kws` or `kws,vww`
- `engines`: optional comma-separated engines such as `helia-rt` or `helia-aot`
- `executorch_backends`: ExecuTorch CMSIS-NN provider selection — `both`
  (default), `arm`, or `ns`
- `ns_cmsis_nn_ref`: optional `ns-cmsis-nn` branch or full commit SHA.
  When empty, the workflow checks out HPX's qualified baseline commit. The
  requested ref and resolved commit are saved in
  `ns-cmsis-nn-revision.txt` with the validation artifacts.
- `toolchains`: optional comma-separated toolchains such as
  `arm-none-eabi-gcc,armclang,atfe`
- `atfe_root`: optional ATfE install directory; when empty, the workflow uses
  a GitHub variable named `ATFE_ROOT` if present and otherwise leaves the
  runner's existing environment untouched
- `transports`: optional comma-separated transports such as `rtt`, `uart`, `swo`,
  or `usb_cdc`
- `memories`: optional comma-separated placement presets such as `auto`, `tcm`,
  `sram`, `mram`, or `psram`
- `power`: `off`, `on`, or `both`; default `on`
- `power_boards`: boards allowed to use power capture, default `apollo510_evb`;
  other selected boards always run unpowered
- `jlink_serials`: optional comma-separated `board=serial` entries, default
  `apollo510_evb=1160003180,apollo330mP_evb=1160003409`
- `power_serials`: optional comma-separated `board=Joulescope-serial` entries,
  default `apollo510_evb=H8MS`
- `repeat`: repeat count per selected case
- `timeout`: per-case timeout in seconds

Default inputs run the same smoke shape as the local command:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb,apollo330mP_evb \
  --power on \
  --power-boards apollo510_evb \
  --jlink-serials apollo510_evb=1160003180,apollo330mP_evb=1160003409 \
  --power-serials apollo510_evb=H8MS \
  --output-dir results/validation \
  --junit-xml results/validation/junit.xml
```

To run a one-model toolchain regression on both attached boards, keep
`suite=smoke` and set only the toolchain axis:

```text
toolchains=arm-none-eabi-gcc,armclang,atfe
```

The equivalent local command is:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb,apollo330mP_evb \
  --power off \
  --toolchains arm-none-eabi-gcc,armclang,atfe \
  --jlink-serials apollo510_evb=801000001,apollo330mP_evb=801000002 \
  --output-dir results/local-validation-toolchains
```

That expands to six cases: one KWS heliaRT smoke case for each
`board × toolchain` combination.

For the broader heliaRT model regression, select `suite=models-rt` and leave the
optional axes empty. That suite runs all four MLPerf Tiny models with
`helia-rt`, `arm-none-eabi-gcc,atfe`, `rtt`, and `auto` memory on both default
boards:

```bash
uv run hpx validate --list \
  --suite models-rt \
  --power off \
  --jlink-serials apollo510_evb=801000001,apollo330mP_evb=801000002
```

This expands to 16 cases: `4 models × 2 boards × 2 toolchains`.

For the equivalent heliaAOT model regression, select `suite=models-aot` and
leave the optional axes empty. It uses the same model, board, toolchain,
transport, and memory axes as `models-rt`, but runs `helia-aot`.

For the full hardware regression, select `suite=complete`. It combines
heliaRT/ns-cmsis-nn, heliaAOT/ns-cmsis-nn, the stock TFLM ARM CMSIS-NN
baseline, and both ExecuTorch provider variants into one sweep. Run
`uv run hpx validate --list --suite complete` for the exact current case
count and axes.

To compare runtime engines on the same smoke model, keep `suite=smoke` and set:

```text
engines=helia-rt,helia-aot,tflm,executorch
executorch_backends=both
```

Set `executorch_backends=arm` or `executorch_backends=ns` to run only one
ExecuTorch provider. You can combine other axes as needed, but preview with
`hpx validate --list` first so the manual run size is explicit.

Before the real run, the workflow installs validation dependencies, including
the profiler's `aot` extra for `helia-aot` suites, fetches Git LFS fixtures,
fetches SEGGER RTT sources into the workflow workspace, runs `hpx doctor`, and
previews the selected cases with `hpx validate --list`. The validation output
directory is uploaded with `actions/upload-artifact` even if the hardware run
fails, so logs and partial case artifacts are still available for debugging.
The upload excludes per-case `work/` directories to avoid storing generated NSX
build trees in every run artifact.

The runner must already provide:

- supported EVB access for the selected `boards` input
- SEGGER J-Link access, including `JLinkExe` and `pylink-square`
- SEGGER custom device files for Apollo330mP, including `Apollo330P_510L`
- ARM toolchain, CMake, Ninja, and NSX on `PATH`
- ATfE plus `ATFE_ROOT` when selected toolchains include `atfe`
- Git LFS support for model fixtures
- optional Joulescope access and wiring when `power` is `on` or `both`

ATfE runs require `ATFE_ROOT` to point at the Arm Toolchain for Embedded install
directory. Configure it as the workflow `atfe_root` input for a manual run, as
a GitHub repository/environment variable named `ATFE_ROOT`, or in the
self-hosted runner's service environment. The workflow only exports an override
when the input or GitHub variable is non-empty; otherwise HPX sees the runner's
native environment. If `ATFE_ROOT` is missing, ATfE cases fail during HPX
preflight before firmware generation.

Use explicit `jlink_serials` on runners with more than one probe attached, or
override the default mapping when moving the workflow to a different
self-hosted runner:

```text
apollo510_evb=801000001,apollo330mP_evb=801000002
```

The workflow serializes runs by the selected board string so two manual jobs do
not intentionally target the same board selection at once. Baseline comparison,
threshold enforcement, and dashboards should consume `validation_manifest.json`
later rather than infer paths from the artifact layout.
