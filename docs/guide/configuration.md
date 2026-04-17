# Configuration

!!! note "Under construction"
    This page will cover YAML config files and CLI flag merging.

heliaPROFILER uses a layered configuration system: YAML file + CLI overrides,
merged into a frozen `ProfileConfig` at startup.

## Config File

Create an `hpx.yml`:

```yaml
model:
  path: my_model.tflite
  arena_size: 65536

engine:
  type: tflm

target:
  board: apollo510_evb

profiling:
  pmu_presets: [basic_cpu]
  per_layer: true
  iterations: 100

output:
  format: csv
  dir: ./results
  model_explorer: true
```

## CLI Overrides

CLI flags override YAML values:

```bash
hpx profile --config hpx.yml --board apollo3p_evb --iterations 50
```
