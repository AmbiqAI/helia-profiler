# Configuration

heliaPROFILER uses a layered configuration system: a YAML file merged with CLI
flags, resolved once at startup into an immutable config object.

## Config file

Create an `hpx.yml` (any name works — pass it with `--config`):

```yaml title="hpx.yml"
model:
  path: my_model.tflite       # (1)!
  arena_size: 131072           # (2)!

engine:
  type: helia-rt               # (3)!
  config:                      # (4)!
    variant: release-with-logs
    dist_path: path/to/helia_rt_dist

target:
  board: apollo510_evb         # (5)!
  toolchain: arm-none-eabi-gcc # (6)!
  jlink_serial: ""             # (7)!

profiling:
  pmu_counters:                # (8)!
    cpu: all
    memory: all
    mve: all
  per_layer: true              # (9)!
  iterations: 5                # (10)!
  warmup: 2

power:
  enabled: false               # (11)!
  driver: joulescope
  mode: external
  duration_s: 30
  io_voltage: 1.8

output:
  format: csv                  # (12)!
  dir: ./results
  model_explorer: true         # (13)!
  detailed: false              # (14)!
```

1. Path to the `.tflite` model file.
2. Tensor arena size in bytes. Required for TFLM/heliaRT. heliaAOT can auto-size.
3. Engine: `tflm`, `helia-rt`, or `helia-aot`.
4. Engine-specific config (passed through to the adapter).
5. Target board — run `hpx boards` to see options.
6. Toolchain prefix (must be on PATH).
7. Optional — select a specific J-Link probe by serial number.
8. PMU counter groups and selections. See [PMU Counters](pmu-counters.md).
9. Per-layer breakdown (vs. whole-model aggregate).
10. Inference iterations per PMU pass (averaged in results).
11. Enable Joulescope power capture. See [Power Measurement](power.md).
12. Output format: `csv` or `json`.
13. Generate Model Explorer overlay JSONs. See [Model Explorer](model-explorer.md).
14. Emit detailed per-preset CSVs and memory breakdown (`--detailed`).

## CLI overrides

CLI flags override YAML values. Anything you can set in YAML can also be
specified on the command line:

```bash
hpx profile --config hpx.yml \
    --board apollo3p_evb \
    --iterations 50 \
    --engine helia-aot \
    --output-dir ./my_results
```

The model path can also be a positional argument:

```bash
hpx profile my_model.tflite --board apollo510_evb
```

## Config resolution order

1. Load YAML config file (if `--config` provided)
2. Override with CLI flags
3. Apply defaults for any unset fields
4. Freeze into an immutable `ProfileConfig` dataclass

After this point, the config is **never mutated**. Every stage reads from the
same frozen object.

## Full field reference

### `model`

| Field | Type | Default | Description |
|---|---|---|---|
| `path` | string | *(required)* | Path to `.tflite` model file |
| `arena_size` | int | `131072` | Tensor arena size in bytes |

### `engine`

| Field | Type | Default | Description |
|---|---|---|---|
| `type` | string | `helia-rt` | Engine: `tflm`, `helia-rt`, `helia-aot` |
| `config` | dict | `{}` | Engine-specific configuration (see [Engines](engines.md)) |

### `target`

| Field | Type | Default | Description |
|---|---|---|---|
| `board` | string | `apollo510_evb` | Target board name |
| `toolchain` | string | `arm-none-eabi-gcc` | Compiler toolchain prefix |
| `jlink_serial` | string | `""` | J-Link serial (empty = auto-detect) |

### `profiling`

| Field | Type | Default | Description |
|---|---|---|---|
| `pmu_counters` | dict | `{cpu: [basic]}` | Counter group selections |
| `pmu_presets` | list | — | Legacy preset names (prefer `pmu_counters`) |
| `per_layer` | bool | `true` | Per-layer breakdown |
| `iterations` | int | `100` | Inference iterations per PMU pass |
| `warmup` | int | `5` | Warmup iterations before measurement |

### `power`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `false` | Enable power capture |
| `driver` | string | `joulescope` | Power driver name |
| `mode` | string | `external` | `external` (Joulescope) or `internal` |
| `duration_s` | int | `30` | Capture duration in seconds |
| `io_voltage` | float | `1.8` | I/O voltage for Joulescope |
| `sync_gpio_pin` | int | `10` | GPIO pin for inference sync |

### `output`

| Field | Type | Default | Description |
|---|---|---|---|
| `format` | string | `csv` | Output format: `csv` or `json` |
| `dir` | string | `./results` | Output directory |
| `model_explorer` | bool | `true` | Generate Model Explorer overlays |
| `detailed` | bool | `false` | Emit detailed breakdowns (per-preset CSVs, memory.json) |
