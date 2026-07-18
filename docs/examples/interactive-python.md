# Interactive Python

Use heliaPROFILER as a Python library when you want to explore a model
interactively, retain typed results, or branch one experiment into several
configurations. The same profiling pipeline powers both `hpx profile` and the
Python API.

## Choose the right interface

| Interface | Best for |
| --- | --- |
| `hpx` CLI | One-off runs, shell scripts, and CI |
| YAML configuration | Reproducible experiments checked into source control |
| `hpx.Session` | Notebooks, IPython, iterative analysis, and comparisons |

A session is immutable. Each `with_*` call returns a new value, so a common
base can safely produce independent heliaRT, heliaAOT, placement, counter, or
power experiments.

## Prepare the notebook environment

From a source checkout, install the notebook kernel and optional static model
analysis support:

```bash
uv sync --group notebook --extra analysis
```

Add `--extra aot` when you want heliaAOT analysis or profiling:

```bash
uv sync --group notebook --extra analysis --extra aot
```

Open the repository in VS Code, select the project `.venv` as the notebook
kernel, and start with both hardware guards disabled.

Executables such as GCC, CMake, Ninja, and J-Link are inherited from the
kernel's `PATH`; the notebook does not guess installation directories. HPX
bundles a pinned SEGGER RTT target-source release for normal RTT profiling.
Pass a path only when testing another RTT release:

```python
base = session.with_target(
    transport="rtt",
    segger_rtt_path="/path/to/SEGGER_RTT",
)
```

Leave `segger_rtt_path` unset to use the bundle. An override root must contain
`RTT/SEGGER_RTT.c` and `Config/SEGGER_RTT_Conf.h`. `Session.doctor()` validates
the selected source dependency for RTT sessions before profiling.

### Choose a transport explicitly

RTT is the recommended default because it is lossless, fast, and needs only the
J-Link connection. HPX intentionally does not retry with another transport when
RTT fails: transport selection changes the generated firmware and the reliability
of captured data.

```python
transport = "rtt"
session = session.with_target(transport=transport)
readiness = session.show(session.doctor())
```

If RTT source setup is the only blocker, use SWO deliberately for a diagnostic
run:

```python
diagnostic = session.with_target(transport="swo", segger_rtt_path=None)
diagnostic.show(diagnostic.doctor())
```

SWO is lossy and may drop protocol lines, so switch back to RTT for measurements.
On supported modern EVBs, `usb_cdc` is the lossless alternative but requires
both the J-Link and target USB cables. `uart` is broadly available through the
J-Link OB virtual COM port, but is low-throughput and has no flow control.

[:material-notebook-outline: Open the full interactive showcase](https://github.com/AmbiqAI/helia-profiler/blob/main/examples/notebooks/hpx_walkthrough_v2.ipynb){ .md-button .md-button--primary }
[:material-download: Download the notebook](https://raw.githubusercontent.com/AmbiqAI/helia-profiler/main/examples/notebooks/hpx_walkthrough_v2.ipynb){ .md-button }

## Start with safe discovery

```python
import helia_profiler as hpx

session = hpx.Session().with_target(board="apollo510_evb")

doctor = session.show(session.doctor())
boards = session.show(session.boards())
engines = session.show(session.engines())
counters = session.show(session.counters("cpu"))
```

`show()` renders a Rich table and returns the original typed value, so the
result remains available for programmatic checks:

```python
if not doctor.ok:
    missing = [
        check
        for check in doctor.checks
        if check.required and not check.available
    ]
```

Probe communication is explicit:

```python
probes = session.show(session.probes())
matches = session.show(session.inspect_probes())
```

Only run probe discovery when a J-Link is connected and available.

## Branch an experiment

```python
model = hpx.examples.tiny_cnn()
base = (
    hpx.Session()
    .with_model(model)
    .with_target(board="apollo510_evb", toolchain="gcc", transport="rtt")
    .with_profiling(
        iterations=100,
        warmup=5,
        pmu_counters={"cpu": "default", "memory": "default"},
    )
)

rt = (
    base
    .with_engine("helia-rt")
    .with_model(model, arena_size=131_072)
    .with_output(dir="results/rt")
)
aot = (
    base
    .with_engine("helia-aot")
    .with_output(dir="results/aot")
)
```

`hpx.examples.tiny_cnn()` materializes a packaged deterministic quantized int8
CNN into the HPX cache and returns a normal `Path`. Its fixed batch size is one,
and its sequence is 3×3 convolution, average pooling, 1×1 convolution, reshape,
fully connected, and softmax. It is intended for tutorials, smoke tests, and
API examples, so no repository checkout or model download is required. Future
packaged models and companion inputs will also be exposed under `hpx.examples`.
The generator and seeded weights are committed with HPX.

`gcc` is the concise alias for `arm-none-eabi-gcc`; resolved configuration and
run metadata use the canonical toolchain name.

The base session remains unchanged. Resolve a branch to inspect the complete,
validated `ProfileConfig` before touching hardware:

```python
config = rt.resolve()
print(config.target.board, config.engine.type, config.profiling.pmu_counters)
```

## Profile and compare

```python
rt_result = rt.profile()
aot_result = aot.profile()
comparison = base.compare(
    rt_result,
    aot_result,
    output_dir="results/rt-vs-aot",
)
```

A `ProfileResult` exposes merged layers, PMU presets and compute groups,
firmware metadata, memory placement, optional power data, run provenance, and
all generated report paths. Model Explorer overlays are included in
`result.report_paths`.

## What the showcase covers

The notebook demonstrates:

- environment checks and typed hardware discovery;
- immutable RT and AOT experiment branches;
- static model operation analysis before flashing;
- per-layer cycle distributions, PMU compute groups, and hotspot filtering;
- memory-region consumers and placement;
- engine and execution-region comparisons;
- YAML snapshot semantics;
- Model Explorer overlays and optional power capture;
- CSV, JSON, and text exports under `results/`.

!!! warning "Hardware is opt-in"
    The notebook defaults to `RUN_HARDWARE = False` and
    `RUN_PROBE_DISCOVERY = False`. Joulescope capture has its own
    `RUN_POWER = False` guard, so ordinary profiling does not imply power
    measurement. Documentation builds do not execute the notebook. Enable
    hardware cells only after reviewing the resolved configuration and
    confirming `doctor.ok`.

## Why the docs do not embed the notebook

The notebook requires optional dependencies and, for its central workflow,
physical hardware. Keeping this page as authored Markdown makes documentation
builds deterministic, searchable, and compatible with the current Material
site and a future Zensical migration. The notebook remains the executable,
full-fidelity companion rather than a build-time input.

For individual API types and signatures, continue to
[Interactive sessions](../reference/api/session.md).
