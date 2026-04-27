# First Profile

A guided, no-config first run. From a fresh install to per-layer cycle
counts in five commands.

## Before you start

- [Installed heliaPROFILER and the toolchain](install.md) — `hpx doctor` shows
  all required tools as ✓.
- An **Apollo510 EVB** plugged into your host via the J-Link USB connector.

!!! note "No model? Use the bundled one."
    The repo ships a small KWS reference model at
    `examples/quickstart/kws_model.tflite`. The commands below assume you
    have a `.tflite` file at hand — substitute that path if you don't.

## 1. Verify the EVB

```bash
hpx doctor
```

Look for:

```
✓ JLinkExe          V8.x
✓ J-Link probe      <serial>  Apollo510 EVB
```

If the probe isn't listed, check the USB cable and the J-Link drivers.

## 2. Run the profiler

```bash
hpx profile examples/quickstart/kws_model.tflite
```

That's it. With no flags, heliaPROFILER:

| Defaults | Value |
|---|---|
| Board | `apollo510_evb` |
| Engine | `helia-rt` (auto-downloads the prebuilt distribution) |
| Toolchain | `arm-none-eabi-gcc` |
| Transport | `rtt` (lossless RTT capture over J-Link) |
| Counters | CPU defaults — cycles, instructions, frontend/backend stalls |
| Iterations | 100 inferences, 5 warmup |
| Output | `./results/` |

Progress prints stage-by-stage. The first run takes ~1–2 minutes (toolchain
download for heliaRT, NSX configure, build, flash, capture). Subsequent runs
are faster — the heliaRT distribution is cached.

## 3. Look at the result

Three files matter on your first run:

```
results/
├── summary.json         ← read this first
├── profile_results.csv  ← per-layer breakdown for spreadsheets
└── run_metadata.json    ← what config was actually used
```

Open `summary.json`:

```json
{
  "engine": "helia-rt",
  "layers": 13,
  "total_cycles": 2014841.7,
  "overflow_detected": false,
  "top_layers": [
    {"index": 1, "op": "CONV_2D", "cycles": 338176},
    {"index": 4, "op": "DEPTHWISE_CONV_2D", "cycles": 207749}
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

## 4. View the per-layer table

`profile_results.csv` has one row per layer, one column per counter:

| index | op | ARM_PMU_CPU_CYCLES | ARM_PMU_INST_RETIRED | … |
|---|---|---|---|---|
| 0 | CONV_2D | 110,234 | 78,901 | … |
| 1 | CONV_2D | 338,176 | 240,118 | … |

Open it in Excel/Numbers/LibreOffice, or pipe it through your favourite
analysis tool. The same data is also rendered as
[Model Explorer overlays](../guide/model-explorer.md) under
`results/model_explorer/`.

## 5. Keep going

Now that you've got a baseline:

- **Repeatable runs?** → [Quick Start with a config file](quickstart.md)
- **Different model placement?** → [Memory Placement](../guide/memory.md)
- **Try heliaAOT or stock TFLM?** → [Inference Engines](../guide/engines.md)
- **Faster build with armclang?** → [Toolchains](../guide/toolchains.md)
- **Add power numbers?** → [Power Measurement](../guide/power.md)

## Troubleshooting the first run

??? failure "`hpx doctor` reports missing tools"
    See [Installation](install.md). Required: `arm-none-eabi-gcc`, `cmake`,
    `ninja`, `JLinkExe`, `nsx`.

??? failure "Build fails downloading heliaRT"
    Set `engine.config.dist_path` in a YAML config to point at a local
    distribution, or unblock GitHub release access. See
    [Engines](../guide/engines.md#heliart).

??? failure "Capture times out"
    The default RTT transport requires a working J-Link connection. Run
    `hpx doctor` again with the EVB plugged in. If RTT is unavailable on
    your setup, switch to USB CDC — see [Transports](../guide/transports.md).

??? failure "`overflow_detected: true` in summary"
    A PMU counter overflowed during one of the iterations. The averaged
    cycle counts are still meaningful, but for a perfectly clean run lower
    `--iterations` or run with `--detailed` to inspect per-iteration data.
