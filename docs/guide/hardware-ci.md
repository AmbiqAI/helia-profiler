# Hardware Validation Artifacts

`hpx validate` is the local-first entry point for hardware profiling suites.
Run it from a developer machine with boards attached first; a later
self-hosted GitHub Actions runner should execute the same command and upload
the same output directory.

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

## Future CI usage

A self-hosted GitHub Actions workflow should run the same `hpx validate`
command after installing toolchains, fetching LFS fixtures, and confirming the
runner has board, J-Link, and optional Joulescope access. The workflow should
upload the whole validation output directory as an artifact; baseline
comparison and dashboards should consume `validation_manifest.json` rather
than infer paths.
