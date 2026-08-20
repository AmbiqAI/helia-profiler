# ExecuTorch MLPerf Tiny exporters

The per-model scripts (`ad.py`, `ic.py`, `kws.py`, `vww.py`) reproduce the
deterministic random-weight PTE fixtures under `tests/fixtures/mlperf_tiny/`.
They require the Python dependencies from the ExecuTorch checkout pinned by
`tests/fixtures/mlperf_tiny/executorch_models.json`.

`export_nsx_aot.py` reuses the same deterministic model builders but lowers
through the nsx-executorch AOT package instead, once per kernel provider
(`arm` and `ns`) — see its module docstring for details.

Set `NSX_EXECUTORCH_ROOT` to the qualified `nsx-executorch` checkout or set
`EXECUTORCH_ROOT` directly to its `external/executorch` directory.

The checked-in PTE files are consumed directly by hardware validation. GitHub
Actions does not regenerate them.
