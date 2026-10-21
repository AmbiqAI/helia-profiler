# Run API example: a compiled build and a saved run result (synthetic)

`build.json` is a `hpx.compiled-build` (schema version 3) for the tiny CNN on heliaAOT, Apollo510 EVB, ATfE, the `lp` clock, an SRAM arena and MRAM weights, with the `carry` lifecycle. `result.json` is a `hpx.run-result` (schema version 1) for one run of that build: the capture's facts, the run's identity, and the per-tensor comparison with host LiteRT. It pins `build.json`, the captured output under `evidence/` and the reference output `result.reference_0.bin` by sha256 and size. Every path is relative to one root, `run`:

```python
from pathlib import Path
import helia_profiler as hpx
from helia_profiler.api import load_result, observations

root = Path("examples/results/run-api-synthetic").resolve()
record = hpx.load_run(root / "result.json", roots={"run": root})
print(record.result.identity.observation_hash)
view = observations(load_result(root / "result.json"))
```

`portable.json` is that view: the run mapped to the portable result schema (`1.0-draft`, mapping `hpx-run-2`).

**Synthetic:**
- The firmware files (`build/app/*`, `build/evidence/*`) are test stand-ins, not a real image.
- The capture came from a mocked device that returned the host LiteRT outputs. No hardware ran, so the latency is a placeholder.
- Identity facts the stand-ins don't record (the NSX lock, the CMSIS-NN module, the heliaAOT release) are `unknown` with a reason.

Use it to exercise readers and admission, not as a measurement.
