# Hardware Validation Suite

A pytest-driven hardware-in-the-loop suite that runs canonical MLPerf Tiny
models end-to-end against real EVBs via `hpx profile`. It is the recommended
way to prove a bench works before trusting any numbers off it.

**User-facing documentation lives in the docs site — not here:**

- [`hpx validate` reference](../../docs/reference/validate.md) — every flag,
  the preset suites, custom model registries, and the manifest contract.
- [Validating a Board Setup](../../docs/guides/validating-a-board-setup.md) —
  the bring-up walkthrough.
- [`maintainers/hardware-ci.md`](../../maintainers/hardware-ci.md) — running
  the same suite under the self-hosted GitHub Actions runner.

This file covers only what a contributor editing the suite needs.

## Running it

Always prefer the CLI wrapper — it owns matrix selection and reporting:

```bash
hpx validate --list                # preview cases, no hardware touched
hpx validate --suite smoke         # fastest useful real run
```

Driving pytest directly is supported but bypasses report aggregation:

```bash
pytest -m hardware tests/validation/ \
    --mlperf-models kws,ic \
    --mlperf-engines helia-aot \
    --mlperf-power off
```

Hardware cases are deselected by default (`addopts = -m 'not hardware'`).

## Fixtures

TFLite fixtures under `tests/fixtures/mlperf_tiny/` are stored via Git LFS —
run `git lfs pull` before a hardware run. A case `pytest.skip`s cleanly if its
fixture is missing. The ExecuTorch `.pte` fixtures alongside them (also LFS)
are lowered by hand from the `.pt2` ExportedProgram fixtures in the same
directories; see
[`tools/export/executorch_mlperf_tiny/`](../../tools/export/executorch_mlperf_tiny/README.md).

`hpx validate --list` is the authoritative model/engine/board matrix. It is
generated from [`src/helia_profiler/validation/matrix.py`](../../src/helia_profiler/validation/matrix.py),
so nothing here needs to restate it.

## Assertions per case

- `summary.json` produced and parseable
- `layers >= 1`
- `total_cycles > 0`
- heliaAOT cases: `aot_operator_manifest.json` present with at least one op
- Powered cases: non-zero energy captured

## Extending

**A board or model** — add an entry to `BOARDS` or `MODELS` in
`validation/matrix.py`. Model fixtures go under
`tests/fixtures/mlperf_tiny/<category>/` (the `.gitattributes` LFS rule covers
`.tflite`). No downstream code changes are needed; selection flows through
`--boards`/`--models`.

**A new test type** — add a parametrised test function in this directory that
consumes the same `case` fixture, already parametrised by
`pytest_generate_tests` in `conftest.py`. Results aggregate into the same
report automatically.
