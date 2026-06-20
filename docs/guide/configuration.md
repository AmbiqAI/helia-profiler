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
2. Tensor arena size in bytes. Required for heliaRT. heliaAOT can auto-size.
3. Engine: `helia-rt` or `helia-aot`.
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
| `type` | string | `helia-rt` | Engine: `helia-rt` or `helia-aot` |
| `config` | dict | `{}` | Engine-specific configuration (see [Engines](engines.md)) |

### heliaRT config notes

- `engine.config.resolver_ops` now defaults to `auto` for `helia-rt`. Leave it
  unset unless you specifically want the broader `all` resolver surface.
- `target.clock.cpu` is the supported way to choose CPU frequency. Set it to
  one of the board's named speeds (`lp`/`hp`, or `ulp`/`lp`/`hp` on Atomiq);
  HPX validates the selection against the chosen board's platform registry
  entry and maps it onto the correct NSX perf mode in the generated firmware.
  Leave it unset to use the board's lowest-power tier.
- Models with `CALL_ONCE`, `VAR_HANDLE`, `ASSIGN_VARIABLE`, or
  `READ_VARIABLE` do not need special-case firmware patches in config; HPX now
  enables the resource-variable runtime automatically when model analysis sees
  those ops.
- If a `helia-rt` run succeeds, use the reported `allocated_arena` to tighten
  `model.arena_size` instead of growing the arena blindly.

### `target`

| Field | Type | Default | Description |
|---|---|---|---|
| `board` | string | `apollo510_evb` | Target board name |
| `toolchain` | string | `arm-none-eabi-gcc` | Compiler toolchain prefix |
| `jlink_serial` | string | `""` | J-Link serial (empty = auto-detect) |
| `clock.cpu` | string | board default | CPU clock speed name, e.g. `lp`/`hp` (rejected if the board has no such speed) |

Advanced target overrides:

- `target.custom_boards` adds config-scoped board definitions without editing the built-in platform registry.
- `target.custom_socs` adds config-scoped SoC definitions for bring-up cases where the built-in SoC metadata is not sufficient.
- `target.custom_boards.<name>.based_on` clones an existing built-in board and lets you override fields like `channel`, `psram_kb`, and `default_sync_gpio_pin`.
- `target.custom_boards.<name>.starter_profile_board` reuses the NSX starter profile from a built-in board when the custom board should inherit its module graph.

### `build`

| Field | Type | Default | Description |
|---|---|---|---|
| `channel` | string | board default | NSX channel recorded in the generated app manifest |
| `nsx_modules` | dict | `{}` | Per-module NSX source overrides (`path`, `ref`, or `version`) |
| `compiler_launcher` | string | `auto` | CMake compiler launcher used to cache compiles. `auto`, `none`, or a tool name/path (`sccache`, `ccache`). |

Build-resolution notes:

- By default, generated profiler apps keep the board's normal NSX `channel`, but HPX explicitly resolves both `neuralspotx` and `nsx-ambiq-sdk` from `main`.
- `build.nsx_modules.<module>.ref` or `.version` overrides win over that default for the owning project.
- `build.nsx_modules.<module>.path` installs a local module checkout into the generated app and bypasses registry resolution for that module only.

Compiler-launcher notes:

- `auto` (the default) wraps every compile with [`sccache`](https://github.com/mozilla/sccache) or [`ccache`](https://ccache.dev) if either is found on `PATH`, and does nothing otherwise — so simply installing the binary opts you in. Caching is correctness-safe (the launcher hashes the full compile inputs) and only accelerates the compile step, not NSX lock/sync/configure or flash.
- `none` (also `off`/`false`) disables the launcher.
- An explicit tool name or path (e.g. `sccache`) is **required**: the build fails if it cannot be found.
- The `HPX_COMPILER_LAUNCHER` environment variable overrides this field, and the `--compiler-launcher` / `--no-compiler-launcher` CLI flags override both.

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
| `sync_gpio_pin` | int | board default (`29` on `apollo510_evb` / `apollo510b_evb`) | GPIO pin for inference sync |

### `output`

| Field | Type | Default | Description |
|---|---|---|---|
| `format` | string | `csv` | Output format: `csv` or `json` |
| `dir` | string | `./results` | Output directory |
| `model_explorer` | bool | `true` | Generate Model Explorer overlays |
| `detailed` | bool | `false` | Emit detailed breakdowns (per-preset CSVs, memory.json) |
