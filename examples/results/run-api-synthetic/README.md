# Run API example: a compiled build and a run result (synthetic)

`build.json` is a `hpx.compiled-build` (schema version 3) for the tiny CNN on heliaAOT, Apollo510 EVB, ATfE, the `lp` clock, an SRAM arena and MRAM weights, with the `carry` lifecycle. The files it pins sit beside it under one root, `run`:

```python
from pathlib import Path
import helia_profiler as hpx

root = Path("examples/results/run-api-synthetic").resolve()
compiled = hpx.load_build(root / "build.json", roots={"run": root})
print(compiled.identity.intent_hash)
```

`result.json` is what `examples/run_api_conformance.py`'s `report()` prints for one run of that build: the run's `RunIdentity`, the per-tensor verification against host LiteRT, and key measurements.

**Synthetic:**
- The firmware files (`build/app/*`, `build/evidence/*`) are test stand-ins, not a real image.
- The capture came from a mocked device that returned the host LiteRT outputs. No hardware ran.
- Identity facts the stand-ins don't record (the NSX lock, the CMSIS-NN module) are `unknown` with a reason.

Use it to exercise readers and admission, not as a measurement.
