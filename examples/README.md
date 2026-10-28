# Examples

Choose an example by the operation you want to learn. Repository examples and
retained results are distinct from the packaged `hpx.examples.tiny_cnn()` model,
which is available to installed Python clients without a repository checkout.

| Example | Use and requirements |
| --- | --- |
| [`quickstart/`](quickstart/) | YAML profile runs using the shipped KWS model. Requires the selected compiler/runtime and target board; power recipes also require the configured instrument. Working directories differ as listed below. |
| [`run_api_conformance.py`](run_api_conformance.py) | Freeze, build, save/load and capture through the Python run API. Run from the repository root. See the build-only example below. |
| [`notebooks/hpx_walkthrough.ipynb`](notebooks/hpx_walkthrough.ipynb) | Installed-library walkthrough using `hpx.examples.tiny_cnn()`. Uses the notebook kernel's HPX environment; output paths are relative to its working directory. Probe discovery, hardware and power are controlled by explicit switches in the notebook. |
| [`models/tier1/`](models/tier1/) | Random-weight ExecuTorch provider-comparison assets and their manifest, not accuracy benchmarks. See [recipe groups](../configs/README.md#executorch-recipe-groups). |
| [`results/`](results/README.md) | Offline retained bundles with source/provenance notes. Includes historical hardware captures and a separately labelled synthetic run-API sample. No board is required to read them. |

## Quickstart configs

Model paths are relative to the **process working directory**, not to the YAML
file. Keep the config and invocation directory paired:

| Working directory | Config filenames |
| --- | --- |
| `examples/quickstart/` | `hpx_rt.yml`, `hpx_rt_armclang.yml`, `hpx_aot.yml`, `hpx_aot_armclang.yml`, `hpx_aot_power.yml`, `hpx_full_sweep.yml` |
| Repository root | `hpx_litert_baseline.yml`, `hpx_rt_npu_atomiq110.yml`, `hpx_aot_npu_atomiq110.yml` (pass their `examples/quickstart/` path to `--config`) |

With the project environment installed, a local-model-path recipe runs as:

```bash
cd examples/quickstart
uv run hpx profile --config hpx_rt.yml
```

For the repository-relative LiteRT recipe, start at the repository root:

```bash
uv run hpx profile --config examples/quickstart/hpx_litert_baseline.yml
```

These commands compile, flash and capture. Set the board/probe and check the
selected toolchain before running them. The NPU recipes select the Atomiq110 FPGA
and the shipped Vela-compiled KWS model; they are not Apollo EVB substitutes.
The quickstart models are included in this directory. See the
[engine guide](../astro-site/src/content/docs/guide/engines.mdx) and
[recipe customization](../configs/README.md#adapt-a-station-recipe).

## Run API conformance

The conformance example uses ATfE and the packaged tiny CNN by default. For
heliaRT or LiteRT, prepare the runtime named by the plan before building. From
the repository root, with ATfE installed:

```bash
uv run hpx runtimes prepare helia-rt
uv run python examples/run_api_conformance.py --build-only --engine helia-rt --board apollo510_evb --out runs/conformance
```

Runtime preparation can fetch dependencies; build-only compiles firmware but
does not capture. Use a fresh output location. To capture programmatically, call
`run_conformance` from a harness with a real board guard and explicit probe
serial. The example's `PlaceholderGuard` refuses capture; the script is not an
unattended board-farm runner. See [fixed-fixture usage](../maintainers/fixed-fixture.md)
for runtime, evidence and guard responsibilities.

For a hardware-free reader example, follow
[`results/run-api-synthetic/README.md`](results/run-api-synthetic/README.md).
Its capture and firmware are test stand-ins, not performance measurements.

## Maintaining examples

Keep usage and working-directory guidance next to runnable examples. Preserve
retained bundle bytes and manifests together; historical results are evidence,
not files to refresh during a docs tidy-up. The
[retained-example test](../tests/runs/test_run_api_retained_example.py) checks the
synthetic sample, and the
[conformance-example tests](../tests/runs/test_run_api_conformance_example.py)
exercise its software contracts. For broader experiment recipes use
[`configs/`](../configs/README.md); for asset generators use
[`tools/`](../tools/README.md).
