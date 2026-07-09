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
Apollo510 EVB and Apollo330mP EVB. The workflow default board input runs the
same smoke model on both boards:

```text
apollo510_evb,apollo330mP_evb
```

The workflow exposes the validation axes as manual inputs. Leave an optional
axis empty to use the selected suite's defaults; set it explicitly to override
only that axis.

- `suite`: `smoke`, `models-rt`, or `models-aot`
- `boards`: comma-separated board IDs, default `apollo510_evb,apollo330mP_evb`
- `models`: optional comma-separated model IDs such as `kws` or `kws,vww`
- `engines`: optional comma-separated engines such as `helia-rt` or `helia-aot`
- `toolchains`: optional comma-separated toolchains such as
  `arm-none-eabi-gcc,armclang,atfe`
- `atfe_root`: optional ATfE install directory; when empty, the workflow uses
  a GitHub variable named `ATFE_ROOT` if present and otherwise leaves the
  runner's existing environment untouched
- `transports`: optional comma-separated transports such as `rtt`, `uart`, `swo`,
  or `usb_cdc`
- `memories`: optional comma-separated placement presets such as `auto`, `tcm`,
  `sram`, `mram`, or `psram`
- `power`: `off`, `on`, or `both`
- `jlink_serials`: optional comma-separated `board=serial` entries, default
  `apollo510_evb=801000001,apollo330mP_evb=801000002`
- `repeat`: repeat count per selected case
- `timeout`: per-case timeout in seconds

Default inputs run the same smoke shape as the local command:

```bash
uv run hpx validate \
  --suite smoke \
  --boards apollo510_evb,apollo330mP_evb \
  --power off \
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

To compare runtime engines on the same smoke model, keep `suite=smoke` and set:

```text
engines=helia-rt,helia-aot
```

You can combine axes when needed, for example `engines=helia-rt,helia-aot` and
`toolchains=arm-none-eabi-gcc,armclang`, but preview with `hpx validate --list`
first so the manual run size is explicit.

Before the real run, the workflow installs test dependencies, fetches Git LFS
fixtures, fetches SEGGER RTT sources into the workflow workspace, runs
`hpx doctor`, and previews the selected cases with `hpx validate --list`. The
validation output directory is uploaded with `actions/upload-artifact` even if
the hardware run fails, so logs and partial case artifacts are still available
for debugging. The upload excludes per-case `work/` directories to avoid
storing generated NSX build trees in every run artifact.

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
