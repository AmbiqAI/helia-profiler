# Analysis and Run Comparison

Profiling answers what happened on a board. Two related workflows help before
and after that measurement:

- `hpx analyze` inspects a model on the host without hardware.
- `hpx compare` compares two completed, compatible profile result bundles.

## Analyze a model without hardware

Install the analysis dependencies once:

```bash
pip install 'helia-profiler[analysis]'
```

Then inspect the raw LiteRT graph:

```bash
hpx analyze model.tflite
hpx analyze model.tflite --format json --output analysis.json
```

The report includes per-operator MACs, parameter counts, and tensor sizes.
`--engine helia-rt` evaluates the original graph as the interpreter sees it.
With the AOT extra installed, `--engine helia-aot` compiles and analyzes the
transformed graph; add `--compare` to show original and transformed graphs
side by side:

```bash
pip install 'helia-profiler[aot]'
hpx analyze model.tflite --engine helia-aot --compare
```

This is useful for checking operator coverage and graph transforms before
starting a firmware build. See the [`hpx analyze` reference](../reference/analyze.md)
for every option.

## Compare two profile runs

Keep each run in a separate output directory:

```bash
hpx profile model.tflite --engine helia-rt \
  --output-dir results/rt
hpx profile model.tflite --engine helia-aot \
  --output-dir results/aot
hpx compare results/rt results/aot \
  --output-dir results/rt-vs-aot
```

HPX verifies declared result-manifest paths, sizes, and SHA-256 digests before
reading a bundle. It then applies typed comparability rules:

- invalid results or different model hashes block the comparison;
- different layer topology suppresses only per-layer deltas;
- incompatible power scope, mode, firmware, monitor presence, lock-step,
  integrity, **clean-window probe**, or (same platform only) the measured
  **firmware code fingerprint** suppresses only power deltas;
- a different **link family** suppresses only the per-region
  measured-memory rows (see below);
- engine (type and measured runtime version), toolchain, board, clock,
  transport, and placement differences remain visible as informative
  dimensions. The engine version is the *measured* identity
  (`run_metadata.engine.version`, e.g. a heliaRT promotion) — runs recorded
  before the dimension existed, and tflm/executorch runs (no resolved
  version), are skipped like any other absent dimension.

The clean-window probe is what ran *inside* the measured window. A
`busy_loop` window measures a calibrated CPU spin rather than a model
inference, so comparing one against an `infer` window reports the difference
between two different physical quantities — `hpx compare` omits power deltas
and says which probe each side used.

Two things it deliberately does not do. It is recorded only for runs that
actually measured power, so comparing a plain profiling run against a
power-instrumented one is unaffected. And it says nothing about the board, so
two SoCs running the same probe stay power-comparable, as the dimension list
above intends.

Baselines recorded before this dimension existed carry no value and are
skipped, so stored comparisons do not flip to failing.

**Power firmware fingerprint.** Every power run also records a code hash of
the measured target's rendered C sources — the main source plus the PMU
profiler translation unit compiled into the same binary
(`summary.power.firmware_code_fingerprint`; comments and whitespace are
normalized away, so documentation-only firmware changes leave it
untouched — rendered build configuration and external module sources are
deliberately outside the hash). When two runs on the *same board, SoC, and
firmware mode* carry different fingerprints, the measured binaries ran
different code, and hpx cannot vouch that their power numbers answer the same
question — power deltas are omitted and the comparison says so. This closes
the failure where a firmware-semantics fix produced a +678% "regression"
against a stored baseline that every other dimension called fully comparable.
The fingerprint is consulted **only** on a matching platform: cross-board
comparisons keep the behavior documented above, and baselines predating the
fingerprint are skipped like any other absent dimension.

**Link family and per-region memory.** Every run records which linker family
built its firmware (`run_metadata.platform.link_family`: `gnu` for gcc and
ATfE, `armlink` for armclang), and `hpx compare` shows the measured
per-region `used`/`free` figures (`memory_regions.DTCM.used`, `.free`, and so
on for ITCM/SRAM/MRAM) as run-level rows — the "does it fit, how much
headroom" question, A/B'd. Those figures are only the same quantity within a
family: GNU ld counts the floating stack inside the app extent while
armlink's fixed reservations sit outside it, so a cross-family compare would
show a large, meaningless delta. When the families differ, the per-region
rows are omitted and the comparison says why; binary section sizes (`text`,
`data`, `bss`) remain comparable across toolchains. A region present on one
side only (ITCM exists on the Apollo5 family alone) renders with a dash rather
than being hidden — that is an SoC-axis change worth seeing. Runs recorded
before the link family existed are skipped like any other absent dimension,
and `Link family` appears in the Config table so a comparison profile can
require it to match.

The terminal highlights totals and the largest layer deltas.
`--output-dir` also writes `compare_summary.json` and `layer_diff.csv`.

## Add a regression policy

A versioned comparison profile turns selected metrics into a deterministic
pass, warning, or failure:

```json title="regression-profile.json"
{
  "schema": "hpx.comparison-profile",
  "schema_version": 1,
  "name": "apollo510-runtime",
  "required_dimensions": ["board", "cpu_clock"],
  "metrics": {
    "total_cycles": {
      "direction": "smaller",
      "unit": "cycles",
      "max_regression_pct": 3.0
    },
    "power.energy_per_inference_j": {
      "direction": "smaller",
      "unit": "J",
      "max_regression_pct": 5.0,
      "missing": "warn"
    }
  }
}
```

```bash
hpx compare results/baseline results/change \
  --profile regression-profile.json \
  --output-dir results/regression
```

Profiles support `smaller`, `larger`, and `equal` directions, percentage and
absolute tolerances, required configuration dimensions, and explicit policy
for missing metrics. A failed regression returns a non-zero exit status. See
the [`hpx compare` reference](../reference/compare.md) for the full contract.
