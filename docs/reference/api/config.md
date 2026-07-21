# Configuration

`ProfileConfig` is the frozen, validated configuration object that
[`profile()`](profile.md) accepts. It is built once at startup by merging a
YAML file with CLI overrides and never mutated afterwards.

!!! tip "YAML users"
    This page documents the Python dataclasses. If you configure heliaPROFILER
    via an `hpx.yml` file rather than calling the API directly, the
    [Configuration Reference](../configuration.md) is the authoritative,
    field-by-field YAML schema.

## Top-level config

::: helia_profiler.ProfileConfig

## Sections

::: helia_profiler.ModelConfig

::: helia_profiler.EngineConfig

::: helia_profiler.TargetConfig

::: helia_profiler.ClockSelection

::: helia_profiler.HeartbeatConfig

::: helia_profiler.ProfilingConfig

::: helia_profiler.PowerConfig

::: helia_profiler.OutputConfig

::: helia_profiler.TimeoutsConfig

::: helia_profiler.BuildConfig

::: helia_profiler.NsxModuleOverride

## Enums

::: helia_profiler.EngineType

::: helia_profiler.Toolchain

::: helia_profiler.Transport

::: helia_profiler.OutputFormat

::: helia_profiler.Placement

::: helia_profiler.PowerMode

::: helia_profiler.ResetStrategy
