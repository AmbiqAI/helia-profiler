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

Run the smoke suite against a connected board:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb \
  --power off \
  --output-dir results/local-validation
```

The smoke suite uses RTT. For local development, put a SEGGER RTT checkout at
`./segger-rtt`:

```bash
git clone https://github.com/SEGGERMicro/RTT.git segger-rtt
```

hpx first honors `SEGGER_RTT_PATH`; if it is unset, it also checks ignored
local checkouts such as `./segger-rtt`, `./RTT`, `~/src/segger-rtt`, and
`~/src/RTT`.

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
- `validation` options such as suite, selected axes, timeout, and output dir
- `summary` pass/fail/skip counts
- `cases` with identity, status, headline metrics, and artifact paths

Git metadata is best-effort. Missing git, source archives, or non-repository
directories do not fail validation report generation.

## Manual GitHub Actions workflow

The repository includes a manually triggered `Hardware Validation` workflow.
It runs on self-hosted runners labeled:

```text
self-hosted
hpx-hardware
```

Use this label for a machine that has HPX-compatible hardware attached. For
the first bench, label the local Mac runner with `hpx-hardware` and attach the
Apollo510 EVB. When an Apollo330 board is available, select its board ID in the
workflow input; no new workflow is required.

The workflow exposes only the core validation inputs:

- `suite`: `smoke`, `models-rt`, or `models-aot`
- `boards`: comma-separated board IDs, default `apollo510_evb`
- `power`: `off`, `on`, or `both`
- `jlink_serials`: optional comma-separated `board=serial` entries
- `repeat`: repeat count per selected case
- `timeout`: per-case timeout in seconds

Default inputs run the same smoke shape as the local command:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb \
  --power off \
  --output-dir results/validation \
  --junit-xml results/validation/junit.xml
```

Before the real run, the workflow installs test dependencies, fetches Git LFS
fixtures, runs `hpx doctor`, and previews the selected cases with
`hpx validate --list`. The validation output directory is uploaded with
`actions/upload-artifact` even if the hardware run fails, so logs and partial
case artifacts are still available for debugging.

The runner must already provide:

- supported EVB access for the selected `boards` input
- SEGGER J-Link access, including `JLinkExe` and `pylink-square`
- ARM toolchain, CMake, Ninja, and NSX on `PATH`
- SEGGER RTT sources through `SEGGER_RTT_PATH` or a discovered checkout
- Git LFS support for model fixtures
- optional Joulescope access and wiring when `power` is `on` or `both`

Use explicit `jlink_serials` on runners with more than one probe attached, for
example:

```text
apollo510_evb=1160002204
```

The workflow serializes runs by the selected board string so two manual jobs do
not intentionally target the same board selection at once. Baseline comparison,
threshold enforcement, and dashboards should consume `validation_manifest.json`
later rather than infer paths from the artifact layout.
