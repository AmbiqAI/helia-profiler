# heliaPROFILER — Hardware Validation Suite

A pytest-driven hardware-in-the-loop validation suite that runs canonical
MLPerf Tiny models end-to-end against a real EVB (Apollo510 today; more
boards later) via `hpx profile`, with optional Joulescope power capture.

Invoke via the CLI wrapper (recommended):

```bash
hpx validate                      # full matrix (4 models × 2 engines × 2 power = 16 cases)
hpx validate --list               # preview what would run — no hardware touched
hpx validate --models kws,ic      # subset by model
hpx validate --engines aot        # subset by engine (aliases: rt, aot)
hpx validate --power off          # skip Joulescope runs
hpx validate --power on           # only Joulescope runs
hpx validate -k kws-aot           # pytest keyword filter
hpx validate --junit-xml report.xml --output-dir ./validation_results
```

Or drive pytest directly:

```bash
pytest -m hardware tests/validation/ \
    --mlperf-models kws,ic \
    --mlperf-engines helia-aot \
    --mlperf-power off
```

## Prerequisites

1. **Apollo510 EVB** connected via J-Link (SEGGER)
2. **arm-none-eabi-gcc** toolchain on `PATH`
3. **Joulescope** (JS110 or JS220) — only required if `--power on`/`both`
4. **Git LFS** fetched — the TFLite fixtures are stored via LFS:
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

Each model is crossed with engines `{helia-rt, helia-aot}` and power
`{off, on}` for 16 default cases per board.

## Outputs

All artifacts land under `--output-dir` (default `./validation_results`):

```
validation_results/
├── validation_report.md                # human-readable pass/fail table
├── validation_report.json              # machine-readable full report
└── <case_id>/                          # one subfolder per case
    ├── config.yml                      # generated hpx profile config
    ├── summary.json                    # raw hpx summary
    ├── profile_results.csv             # per-layer PMU CSV
    ├── aot_operator_manifest.json      # AOT cases only
    ├── hpx_stdout.log
    └── hpx_stderr.log
```

`case_id` format: `<board>-<model>-<engine>[-power]`
(e.g. `apollo510_evb-kws-aot-power`).

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
    path: validation_results/
```
