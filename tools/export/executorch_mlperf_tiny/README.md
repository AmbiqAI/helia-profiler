# ExecuTorch MLPerf Tiny exporters

The `.pte` fixtures under `tests/fixtures/mlperf_tiny/` are produced in two
stages, with an INT8 `.pt2` ExportedProgram as the interface between them:

1. **`make_pt2.py`** instantiates the canonical MLPerf Tiny PyTorch models
   from the pinned ExecuTorch checkout
   (`executorch.examples.models.mlperf_tiny`) with a deterministic seed,
   applies PT2E static INT8 quantization (`CortexMQuantizer` with
   deterministic synthetic calibration), and saves each quantized program as
   a `.pt2` fixture (Git LFS) beside its TFLite counterpart. The `.pt2` is
   therefore datatype-equivalent to the INT8 `.tflite` reference model next
   to it: int8 weights/activations with a float32 method boundary. No
   architecture is re-authored in this repository. Upstream ships no trained
   checkpoints, so weights are deterministic random initialization — hence
   the `_random` file names. A `.pt2` with the same contract but trained
   weights can be dropped in without touching anything else.
2. **`export_pte.py`** loads a `.pt2` (refusing unquantized programs) and
   only lowers it, through helia-torch: `nsx_cortex_m.export()` with
   `kernel_provider="arm"` takes its pre-quantized path (no re-quantization,
   straight to kernel matching) with `int8_io=True`, so the serialized
   method is int8-in/int8-out like the TFLite references — non-delegate
   Cortex-M/CMSIS-NN operators with the boundary quantize/dequantize
   removed (the I/O scales/zero-points are recorded in the metadata and
   `executorch_models.json`). A float model handed to the same entry
   point would be PT2E-quantized there instead. I/O shapes are read from
   the ExportedProgram. It writes the `.pte` plus a `<model>_metadata.json`
   (untracked; the tracked record is
   `tests/fixtures/mlperf_tiny/executorch_models.json`).

`export_nsx_aot.py` runs the same INT8 `.pt2` fixtures through
`nsx_cortex_m.export()` once per kernel provider (`arm` and `ns`),
including the `<pte>.json` sidecar manifests — see its module docstring for
details. Shared bits (the model table, calibration, `.pt2` loading) live in
`common.py`.

The pre-quantized path in `nsx_cortex_m.export()` (skip quantization when
the ExportedProgram already carries PT2E quantize/dequantize ops) lives in
the nsx-executorch repo; the pin recorded in `executorch_models.json` must
include that support.

All stages require the Python dependencies from the ExecuTorch checkout
pinned by `tests/fixtures/mlperf_tiny/executorch_models.json`. Set
`NSX_EXECUTORCH_ROOT` to the qualified `nsx-executorch` checkout or set
`EXECUTORCH_ROOT` directly to its `external/executorch` directory. PTE
inspection needs `flatc` on `PATH` (any nsx-executorch build tree has one
under `.../executorch/third-party/flatc_ep/bin`).

```bash
python tools/export/executorch_mlperf_tiny/make_pt2.py
python tools/export/executorch_mlperf_tiny/export_pte.py --all
```

After regenerating, update the hashes and sizes in
`tests/fixtures/mlperf_tiny/executorch_models.json` and check the
planned-arena/I-O values still match `src/helia_profiler/validation/matrix.py`.

The checked-in PTE files are consumed directly by hardware validation. GitHub
Actions does not regenerate them.
