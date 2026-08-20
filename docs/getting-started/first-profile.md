# First Profile

A guided, no-config first run: from unboxing an Apollo EVB to a per-layer
cycle-count table in about five commands.

## Before you start

- [Installed heliaPROFILER and the toolchain](install.md) — `hpx doctor`
  shows all required tools as ✓.
- An Ambiq Apollo EVB, powered and connected to your host over its J-Link
  USB connector (the on-board debug USB port — check your specific EVB's
  quick-start card if it has more than one USB connector).

You also need a LiteRT `.tflite` model. Replace `/path/to/model.tflite` below with
your model path. If you installed with `pip` into the active Python
environment, heliaPROFILER includes a small deterministic example model:

```bash
python -c "from helia_profiler.examples import tiny_cnn; print(tiny_cnn())"
```

Use the printed path in the commands below. A source checkout also contains
`examples/quickstart/kws_model.tflite`. `uv tool install` keeps packages in an
isolated environment, so supply your own model or use the source-checkout path.

## 1. Confirm the toolchain

```bash
hpx doctor
```

All required rows should show `✓` (see [Installation](install.md) if not).

## 2. Find your J-Link probe

```bash
hpx probes list
```

```text
serial      product                    connection
----------  -------------------------  ----------
801000001  J-Link-OB-Apollo4-CortexM  USB
```

If nothing is listed, check the USB cable and that the SEGGER J-Link
software from [Installation](install.md#4-segger-j-link-software) is
installed.

## 3. Find your board name

heliaPROFILER needs a `--board` value that matches your EVB. List every
board it knows about:

```bash
hpx boards
```

Match the silkscreen/part number on your EVB against the `Board` column
(for example `apollo510_evb` for an Apollo510 EVB). The default board is
`apollo510_evb` — pass `--board <name>` explicitly if yours is different.

## 4. Run the profiler

```bash
hpx profile /path/to/model.tflite --board apollo510_evb
```

With no other flags, heliaPROFILER:

| Default | Value |
|---|---|
| Engine | `helia-rt` (resolved from the NSX registry and built from source) |
| Toolchain | `arm-none-eabi-gcc` |
| Transport | `rtt` (lossless RTT capture over J-Link) |
| Counters | CPU defaults — cycles, instructions, frontend/backend stalls |
| Iterations | 100 inferences, 5 warmup |
| Output | `./results/` |

Progress prints stage-by-stage. The first run takes longer because NSX resolves
and clones the pinned heliaRT and firmware modules, then configures, builds,
flashes, and captures. Subsequent runs are faster because module and build
artifacts are cached.

!!! tip "Rerunning without touching the network"
    Once a run has succeeded, `hpx profile ... --offline` skips NSX
    dependency resolution and reuses the existing `nsx.lock`/module state
    — useful for fast, reproducible reruns once your setup is stable.

## 5. Look at the result

Four files matter on your first run:

```text
results/
├── result_manifest.json ← bundle validity, provenance, and artifact digests
├── summary.json         ← headline metrics; read this first
├── profile_results.csv  ← per-layer breakdown for spreadsheets
└── run_metadata.json    ← resolved config and environment provenance
```

Open `summary.json`:

```json
{
  "engine": "helia-rt",
  "layers": 13,
  "total_cycles": 123456.0,
  "overflow_detected": false,
  "top_layers": [
    {"index": 1, "op": "CONV_2D", "cycles": 34567},
    {"index": 4, "op": "DEPTHWISE_CONV_2D", "cycles": 23456}
  ],
  "memory": { "arena_size": 131072, "allocated_arena": 29780 }
}
```

Three things to read off:

- **`total_cycles`** — the headline cost of one inference, averaged across
  iterations.
- **`top_layers`** — which ops dominate runtime. Optimization effort goes here.
- **`memory.allocated_arena`** — how much RAM TFLM actually used. If it's
  much smaller than `arena_size`, you can shrink the arena.

Before consuming the headline metrics, check `validity` and `issues` in
`summary.json` or `result_manifest.json`. A result is `valid`, `degraded`, or
`invalid`; structured issues explain any capture or integrity problem.

## 6. View the per-layer table

`profile_results.csv` has one row per layer, one column per counter. See
[Output & Reports](../guide/output.md) for the full column reference and
what each counter means. The same data is also rendered as
[Model Explorer overlays](../guide/model-explorer.md) under
`results/model_explorer/`.

## 7. Keep going

Now that you've got a baseline:

- **Repeatable runs?** → [Quick Start with a config file](quickstart.md)
- **Different model placement?** → [Memory Placement](../guide/memory.md)
- **Try heliaAOT?** → [Inference Engines](../guide/engines.md)
- **Faster build with armclang?** → [Toolchains](../guide/toolchains.md)
- **Add power numbers?** → [Power Measurement](../guide/power.md)

## Troubleshooting the first run

??? failure "`hpx doctor` reports missing tools"
    See [Installation](install.md). Required: `arm-none-eabi-gcc`, `cmake`,
    `ninja`, `JLinkExe`, `neuralspotx`.

??? failure "`hpx probes list` shows nothing"
    Replug the J-Link USB cable and confirm the SEGGER J-Link software is
    installed. On Linux, replug after installing so udev rules take effect.

??? failure "Build fails downloading heliaRT"
    Set `engine.config.dist_path` in a YAML config to point at a local
    distribution, or unblock GitHub release access. See
    [Engines](../guide/engines.md#heliart).

??? failure "Capture times out"
    The default RTT transport requires a working J-Link connection. Run
    `hpx probes list` again with the EVB plugged in. If RTT is unavailable
    on your setup, switch to USB CDC — see [Transports](../guide/transports.md).

??? failure "`overflow_detected: true` in summary"
    A PMU counter overflowed during measurement, so the result is invalid.
    Lower `--iterations`, reduce the requested counter set, or run with
    `--detailed` to diagnose the affected pass before using the metrics.
