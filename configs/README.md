# Profiling recipes

These YAML files are starting points for profile runs and diagnostics. For a
first run, use the [quickstart examples](../examples/README.md#quickstart-configs).
For configuration fields and CLI overrides, use the
[configuration guide](../astro-site/src/content/docs/guide/configuration.mdx).

Run recipes here **from the repository root**: relative model, checkout and
output paths are resolved from the process working directory, not from the YAML
file's directory. Fetch Git LFS assets before using models under `tests/fixtures/`.

## Choose a recipe

| Directory | Purpose and audience | Assets and customization |
| --- | --- | --- |
| [`mlperf_tiny/`](mlperf_tiny/) | Station recipes for comparing model, engine, board, toolchain, memory, clock and transport choices. Includes power, transport and setup-cost variations. | Models are under `tests/fixtures/mlperf_tiny/`. Every recipe names a particular `target.jlink_serial`; replace it for your station. Check the power wiring and selected toolchain. |
| [`bench/`](bench/) | Four focused power/timing diagnostics for maintainers investigating capture behavior. These are not a general benchmark suite. | Uses the checked-in KWS model. The recipes select an Apollo4 board and Joulescope capture; probe serial is unset. See the diagnostic table below. |
| [`executorch/`](executorch/) | Provider comparisons and export templates for ExecuTorch contributors. | Every recipe has a placeholder `engine.config.source_path`. Supply the qualified local runtime checkout and the matching exported model/provider; see the groups below. |

The MLPerf directory's model prefixes are `ad`, `ic`, `kws` and `vww`. `_rt_`
and `_aot_` identify the engine; board/toolchain/transport/placement suffixes
identify variations. `kws_rt_setup_cost.yaml`, `*_noprofile.yaml` and
`*_nopower.yaml` are diagnostic variations: read their actual `profiling` and
`power` settings rather than inferring behavior from a filename.

## Adapt a station recipe

1. Select a model/engine/board combination supported by the current
   [compatibility baseline](../astro-site/src/content/docs/reference/compatibility-baseline.mdx).
2. Check `model.path`, arena size and memory placement. A config loading
   successfully does not prove that the model fits or the board has that memory.
3. Set the probe serial and any transport-specific port. Confirm the board wired
   to that probe, the toolchain installation, CPU clock and power connections.
4. Set an output directory for the experiment. Preserve the resolved config and
   result bundle with the measurements.

For example, after adapting the recipe, run from the repository root:

```bash
uv run hpx probes list
uv run hpx profile --config configs/mlperf_tiny/kws_rt_power_ap510.yaml --jlink-serial YOUR_PROBE_SERIAL
```

The profile command builds, flashes and captures on hardware. The serial argument
overrides the YAML value; it does not change the YAML's board, model or power
settings. Use [hardware CI guidance](../maintainers/hardware-ci.md) for repeatable
station validation rather than treating this recipe collection as the nightly
matrix.

## Bench diagnostics

| Recipe | Question it helps investigate |
| --- | --- |
| [`busy_loop_dedicated.yaml`](bench/busy_loop_dedicated.yaml) | Does dedicated power firmware capture a busy-loop window consistently? |
| [`busy_loop_shared.yaml`](bench/busy_loop_shared.yaml) | How does the busy-loop window agree across shared profile/power boots? |
| [`crystal_settle_cold.yaml`](bench/crystal_settle_cold.yaml) | Does clean timing settle after a cold power-up? Run it first after leaving the board unpowered. |
| [`tolerance_shared_infer.yaml`](bench/tolerance_shared_infer.yaml) | What is the spread of the shared-mode inference-window ratio over repeated captures? |

Inspect recorded issues and `summary.power.gated_window_duration_ratio` when
available. These recipes do not establish a numerical tolerance or a board-wide
qualification result. Timing meanings and capture ownership are documented in
[the capture timeline](../maintainers/capture-timeline.md).

## ExecuTorch recipe groups

| Recipes | Model source |
| --- | --- |
| `tier1_arm.yaml`, `tier1_ns.yaml`, `tier1_arm_atfe.yaml`, `tier1_ns_atfe.yaml`, `tier1mean_arm.yaml`, `tier1mean_ns.yaml` | Shipped random-weight comparison models in [`examples/models/tier1/`](../examples/models/tier1/), with an instruction-map manifest. Exported by [`export_tier1.py`](../tools/export/executorch_tier1/export_tier1.py). |
| `ic_resnet8_arm.yaml`, `ic_resnet8_ns.yaml` | Shipped random-weight INT8 PTE in `tests/fixtures/mlperf_tiny/ic/`; export provenance is in [`executorch_models.json`](../tests/fixtures/mlperf_tiny/executorch_models.json). |
| `ic_aot_ns.yaml` | Generated `examples/models/mlperf_nsx_aot/ic_ns.pte`, not shipped. See [`export_nsx_aot.py`](../tools/export/executorch_mlperf_tiny/export_nsx_aot.py) and the [export guide](../tools/export/executorch_mlperf_tiny/README.md). |
| `resnet8_cmsis_nn.yaml` | External `/path/to/resnet8_cmsis_nn.pte`; replace the placeholder with your export. |

Provider selection and export requirements are described in the
[ExecuTorch kernel guide](../astro-site/src/content/docs/guide/executorch-ns-kernels.mdx).
The paired Tier-1 PTEs are different serializations: use
[`compare_tier1.py`](../tools/export/executorch_tier1/compare_tier1.py) for their
instruction-map comparison. Ordinary `hpx compare` enforces matching model hashes.

## Maintaining recipes

Keep a recipe with the subsystem it exercises: model/engine experiments in
`mlperf_tiny/`, capture diagnostics in `bench/`, export/provider experiments in
`executorch/`. Describe its purpose, required assets and station assumptions;
put historical results and tuning discussions in issues or retained evidence.
A new recipe should name its intended comparison and output location.

[`test_shipped_configs.py`](../tests/test_shipped_configs.py) loads every YAML
here and in `examples/quickstart/`. It checks configuration acceptance, not model
availability, compiler readiness, physical wiring or successful capture. Moving
or retiring a recipe requires checking its documentation, workflow and external
station callers first.
