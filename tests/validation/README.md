# heliaPROFILER — Hardware Validation Suite

A pytest-driven hardware-in-the-loop validation suite that runs canonical
MLPerf Tiny models end-to-end against real EVBs via `hpx profile`.
The default validation path focuses on reliability across engines,
toolchains, host interfaces, and memory placements; Joulescope power capture
is an explicit opt-in axis.

Invoke via the CLI wrapper (recommended):

```bash
hpx validate                      # Apollo510 reliability matrix, power off
hpx validate --list               # preview what would run — no hardware touched
hpx validate --models kws,ic      # subset by model
hpx validate --engines aot        # subset by engine (aliases: rt, aot)
hpx validate --power off          # skip Joulescope runs
hpx validate --power on           # only Joulescope runs
hpx validate --boards apollo3p_evb,apollo4p_blue_kxr_evb,apollo510_evb \
    --models kws --engines rt --toolchains gcc --interfaces rtt --memories auto \
    --power off --repeat 2        # require two passing iterations per selected case
hpx validate -k kws-aot           # pytest keyword filter
hpx validate --junit-xml report.xml --output-dir ./results/validation
```

Local Mac smoke check with a connected board:

```bash
uv run hpx validate --list --suite smoke --boards apollo510_evb
uv run hpx validate \
    --suite smoke \
    --boards apollo510_evb \
    --power off \
    --output-dir results/local-validation
```

Or drive pytest directly:

```bash
pytest -m hardware tests/validation/ \
    --mlperf-models kws,ic \
    --mlperf-engines helia-aot \
    --mlperf-power off
```

## Prerequisites

1. Supported Ambiq EVB connected via J-Link (SEGGER)
2. **arm-none-eabi-gcc** toolchain on `PATH`
3. Additional selected toolchains on `PATH` (`armclang`/ACfE, ATfE) when requested
4. **Joulescope** (JS110, JS220, or JS320) — only required if `--power on`/`both`
5. **Git LFS** fetched — the TFLite fixtures are stored via LFS:
   ```bash
   git lfs pull
   ```
   The suite will `pytest.skip` a case cleanly if its fixture is missing.

## Matrix

| Category | Model                          | Arena | File                                   |
|----------|--------------------------------|-------|----------------------------------------|
| KWS      | DS-CNN int8                    | 128KB | `kws/kws_ref_model.tflite`             |
| VWW      | MobileNetV1 96×96 int8         | 512KB | `vww/vww_96_int8.tflite`               |
| IC       | ResNet (CIFAR-10) int8         | 256KB | `ic/ic_resnet_int8.tflite`             |
| AD       | DeepAutoEncoder ToyADMX int8   | 128KB | `ad/ad01_int8.tflite`                  |

Each selected model is crossed with engines `{helia-rt, helia-aot}`, selected
toolchains, board-supported interfaces, and board-supported memory placement
presets. Power is `off` by default for PR reliability validation; use
`--power on` or `--power both` only when validating Joulescope capture.

## Outputs

All artifacts land under `--output-dir` (default `./results/validation`):

```
results/validation/
├── validation_manifest.json            # portable bundle index for CI/dashboard consumers
├── validation_report.md                # human-readable pass/fail table
├── validation_report.json              # machine-readable full report
└── <case_id>/                          # one subfolder per case
    ├── config.yml                      # generated hpx profile config
    ├── work/                           # generated firmware/build state for this case
    ├── summary.json                    # raw hpx summary
    ├── profile_results.csv             # per-layer PMU CSV
    ├── run_metadata.json               # config, git/model/toolchain/platform metadata
    ├── aot_operator_manifest.json      # AOT cases only
    ├── hpx_profile.log                 # full child command/stdout/stderr
    ├── hpx_stdout.log
    └── hpx_stderr.log
```

`case_id` format: `<board>-<model>-<engine>-<toolchain>-<interface>-<memory>[-power][-runNN]`
(e.g. `apollo510_evb-kws-aot-arm-none-eabi-gcc-rtt-auto-run02`).

`validation_manifest.json` contains schema version, generation time, hpx
version, best-effort git metadata, selected validation options, summary counts,
and per-case relative artifact paths. Relative paths let the same bundle work
locally, in downloaded GitHub Actions artifacts, and in future dashboard
publishing.

## Assertions per case

- `summary.json` produced and parseable
- `layers >= 1`
- `total_cycles > 0`
- AOT cases: `aot_operator_manifest.json` present with ≥1 op
- Power cases: non-zero energy captured

## Extending

### Adding a board

Edit [`src/helia_profiler/validation/matrix.py`](../../src/helia_profiler/validation/matrix.py),
add an entry to the `BOARDS` dict. Hardware case selection uses
`--mlperf-boards`/`--boards` so no downstream code changes required.

### Adding a model

Drop the `.tflite` under `tests/fixtures/mlperf_tiny/<category>/` (tracked
via `.gitattributes` LFS rule) and add an entry to `MODELS`.

### Adding a new test type

Add a new parametrised test function in `tests/validation/` that consumes
the same `case` fixture (already parametrised by `pytest_generate_tests`
in `conftest.py`). Results automatically aggregate into the same report.

## CI (future)

The suite is designed to run unchanged under a self-hosted GHA runner
with the hardware attached:

```yaml
- run: git lfs pull
- run: hpx validate --power off --junit-xml junit.xml
- uses: actions/upload-artifact@v4
  with:
    name: validation-results
    path: results/validation/
```
