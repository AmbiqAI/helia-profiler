# ExecuTorch MLPerf Tiny exporters

These scripts reproduce the deterministic random-weight PTE fixtures under
`tests/fixtures/mlperf_tiny/`. They require the Python dependencies from the
ExecuTorch checkout pinned by `tests/fixtures/mlperf_tiny/executorch_models.json`.

Set `NSX_EXECUTORCH_ROOT` to the qualified `nsx-executorch` checkout or set
`EXECUTORCH_ROOT` directly to its `external/executorch` directory.

The checked-in PTE files are consumed directly by hardware validation. GitHub
Actions does not regenerate them.
