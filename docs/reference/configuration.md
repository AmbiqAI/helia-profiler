# Configuration Reference

This page is **generated** from the `ProfileConfig` pydantic dataclasses in `src/helia_profiler/config.py` — it is the single source of truth for every config key, its type, default, and status. Regenerate it after any config model change with:

```bash
uv run python tools/gen_config_reference.py
```

Unknown keys anywhere in the config tree are rejected at load time with did-you-mean suggestions drawn from these same models — see [Configuration](../guide/configuration.md#validation) for the general validation behavior.

## `model`

Model file and arena sizing.

``arena_location`` and ``weights_location`` are the preferred placement
controls for runtime engines such as heliaRT: the arena is the mutable
tensor arena, while weights are the model flatbuffer/constant data.

``model_location`` is retained as a compatibility preset for older configs.
Split fields take precedence when present. ``helia-aot`` uses its own
tensor-kind placement controls via ``engine.config.aot_args.memory.tensors``.

Policy values:

* ``auto`` *(default)* — plan-memory stage picks the fastest region(s)
  that fit. Greedy fastest-fit with arena prioritized over weights when
  the two compete for the same region. Order: TCM → SRAM → MRAM.
* ``tcm`` — force both arena and weights into DTCM (highest performance,
  smallest capacity). Fails preflight if the SoC has no TCM or it
  doesn't fit.
* ``sram`` — force both into shared SRAM.
* ``mram`` — weights stay in MRAM/Flash (rodata); arena goes to TCM
  when available, else SRAM. Matches pre-auto-placement behavior.
* ``psram`` — weights uploaded to external PSRAM at runtime via J-Link;
  arena in SRAM. Requires a PSRAM-capable board.

| Key | Type | Default | Notes |
|---|---|---|---|
| `path` | Path | `—` |  |
| `arena_size` | int \| null | `null` |  |
| `model_location` | auto \| tcm \| sram \| mram \| psram | `auto` | **Deprecated** — prefer `arena_location`/`weights_location` for placement control. |
| `arena_location` | tcm \| sram \| mram \| psram \| null | `null` |  |
| `weights_location` | tcm \| sram \| mram \| psram \| null | `null` |  |

## `engine`

Inference engine selection and passthrough config.

| Key | Type | Default | Notes |
|---|---|---|---|
| `type` | tflm \| helia-rt \| helia-aot | `helia-rt` |  |
| `backend` | str \| null | `null` |  |
| `config` | dict[str, Any] | `{}` | free-form engine-specific mapping (not strictly validated). |
| `config_path` | Path \| null | `null` |  |

## `target`

Hardware target.

| Key | Type | Default | Notes |
|---|---|---|---|
| `board` | str | `apollo510_evb` |  |
| `toolchain` | arm-none-eabi-gcc \| gcc \| armclang \| atfe | `arm-none-eabi-gcc` |  |
| `jlink_serial` | str \| null | `null` |  |
| `transport` | rtt \| usb_cdc \| swo \| uart | `rtt` |  |
| `usb_port` | str \| null | `null` |  |
| `rtt_buffer_size_up` | int \| null | `null` |  |
| `clock` | ClockSelection | `see section below` |  |
| `heartbeat` | HeartbeatConfig | `see section below` |  |
| `custom_socs` | dict[str, Any] \| null | `null` | advanced raw mapping validated by the platform layer. |
| `custom_boards` | dict[str, Any] \| null | `null` | advanced raw mapping validated by the platform layer. |
| `ensure_board_powered` | bool | `false` |  |

## `target.clock`

Per-domain clock speed selection for the generated firmware.

Each field names a speed within the SoC's matching clock domain using
Ambiq datasheet terminology (e.g. ``cpu="hp"``).  ``None`` selects that
domain's default speed.  Values are validated against the resolved SoC in
stage 1, so unknown names raise a clear ConfigError rather than failing
silently.

| Key | Type | Default | Notes |
|---|---|---|---|
| `cpu` | str \| null | `null` |  |

## `target.heartbeat`

Liveness / progress-reporting settings.

The firmware emits ``HPX_HEARTBEAT`` lines at configurable intervals so
the host can (a) detect a hung run without using a large wall-clock
timeout, and (b) show the user live progress.

Attributes
----------
enabled:
    Master switch.  When ``False``, no heartbeats are emitted or
    expected and the host falls back to the legacy line-gap timeout.
every_n_ops:
    Emit a heartbeat after this many profiled ops.  ``0`` disables this
    trigger.  Lower values add more PMU/inter-op overhead but give
    finer-grained progress.
every_ms:
    Emit a heartbeat when at least this many wall-clock milliseconds
    have elapsed since the last heartbeat.  ``0`` disables this
    trigger.  Useful for engines with a single large invocation (e.g.
    AOT command streams) where ``every_n_ops`` does
    not fire.
host_timeout_s:
    Maximum time the host will wait without receiving *any* line from
    the firmware before declaring the run hung.
overall_timeout_s:
    Hard ceiling on total capture time, in seconds.  ``None`` means
    unbounded (rely on heartbeats).  Set to a positive int for a safety
    net in CI or unattended runs.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` |  |
| `every_n_ops` | int | `8` |  |
| `every_ms` | int | `2000` | units: milliseconds |
| `host_timeout_s` | int | `30` | units: seconds |
| `overall_timeout_s` | int \| null | `null` | units: seconds |

## `profiling`

PMU capture settings.

Counter selection is specified via *pmu_counters* — a mapping of
compute-unit group (``cpu``, ``mve``, ``memory``) to a selection:

* ``"default"`` — curated set of the most useful counters.
* ``"all"``     — every counter in the group (multi-pass).
* ``["NAME", …]`` — explicit counter names.

The legacy *pmu_presets* field is still accepted for backward
compatibility and is converted internally.

| Key | Type | Default | Notes |
|---|---|---|---|
| `pmu_presets` | tuple[str, ...] | `[basic_cpu]` | **Deprecated** — prefer `pmu_counters`. |
| `pmu_counters` | dict[str, str \| list[str]] \| null | `null` |  |
| `per_layer` | bool | `true` |  |
| `iterations` | int | `100` |  |
| `warmup` | int | `5` |  |
| `window_mode` | str | `auto` |  |
| `window_target_ms` | int | `1000` | units: milliseconds |
| `window_min` | int | `10` |  |
| `window_max` | int | `2000` |  |
| `clean_window_probe` | str | `infer` |  |
| `clean_window_trace` | bool | `false` |  |
| `force_shared_sram` | bool | `false` |  |
| `aggregation` | str | `median` |  |
| `extreme_mode` | bool | `false` |  |

## `power`

Power measurement settings.

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` |  |
| `driver` | str | `joulescope` |  |
| `firmware` | str | `dedicated` |  |
| `mode` | external \| internal | `external` |  |
| `duration_s` | int \| null | `null` | units: seconds |
| `io_voltage` | float | `1.8` |  |
| `sync_gpio_pin` | int | `10` |  |
| `sync_input_index` | int | `0` |  |
| `lockstep` | bool \| null | `null` |  |
| `state_gpio_pin` | int | `0` |  |
| `go_gpio_pin` | int | `0` |  |
| `state_input_index` | int | `1` |  |
| `go_output_index` | int | `0` |  |
| `stats_rate_hz` | int | `1000` | units: hertz |
| `reset_strategy` | auto \| power_cycle \| none \| debug_reset \| swpoi_reset \| debug_reset+swpoi_reset | `auto` |  |
| `serial` | str \| null | `null` |  |

## `output`

Report output settings.

| Key | Type | Default | Notes |
|---|---|---|---|
| `format` | csv \| json \| model-explorer | `csv` |  |
| `dir` | Path | `results` |  |
| `model_explorer` | bool | `true` |  |
| `detailed` | bool | `false` |  |

## `timeouts`

Subprocess and network timeouts (seconds).

Every subprocess and long-lived HTTP call in heliaPROFILER reads its
timeout from this struct instead of hard-coding it.  Override any value
in YAML under ``timeouts:`` to adapt to slow CI machines, laggy J-Link
probes, or poor network conditions.

Capture-time timeouts (heartbeat / overall) live on ``HeartbeatConfig``
because they are tied to the on-device progress protocol.

| Key | Type | Default | Notes |
|---|---|---|---|
| `configure_s` | int | `120` | units: seconds |
| `build_s` | int | `300` | units: seconds |
| `flash_s` | int | `120` | units: seconds |
| `toolchain_probe_s` | int | `5` | units: seconds |
| `binary_probe_s` | int | `10` | units: seconds |
| `download_api_s` | int | `30` | units: seconds |
| `download_asset_s` | int | `300` | units: seconds |

## `build`

NSX build-system overrides.

Controls how the generated firmware's NSX manifest resolves modules.
Default behaviour keeps the selected board's default NSX channel, but
generated manifests explicitly track ``main`` for the ``neuralspotx`` and
``nsx-ambiq-sdk`` projects unless the user overrides those modules.

Advanced users can pin individual modules to a version, point them at
a local checkout, or select a custom git ref — useful for SoC/board
bring-up before changes land in the stable channel.

``compiler_launcher`` selects a CMake compiler launcher (e.g. ``sccache``
or ``ccache``) that wraps every compile to cache object output and speed
up repeated builds.  ``"auto"`` (the default) uses ``sccache`` then
``ccache`` if either is on ``PATH`` and otherwise does nothing — so the
mere presence of the binary is the opt-in.  ``"none"`` disables it; an
explicit tool name or path requires that the launcher be found.

| Key | Type | Default | Notes |
|---|---|---|---|
| `channel` | str \| null | `null` |  |
| `nsx_modules` | dict[str, NsxModuleOverride] | `{}` | see subsection below for the per-entry schema |
| `compiler_launcher` | str | `auto` |  |

## `build.nsx_modules.<name>`

Override resolution for a single NSX module.

Exactly one mode must be set:
* *path* — use a local directory as the module source (``local: true``).
* *ref* — resolve the module's project at a specific git ref/tag.
* *version* — pin the module to an exact version constraint.

| Key | Type | Default | Notes |
|---|---|---|---|
| `path` | Path \| null | `null` |  |
| `ref` | str \| null | `null` |  |
| `version` | str \| null | `null` |  |

## Top-level keys

Top-level immutable configuration for a profiling run.

| Key | Type | Default | Notes |
|---|---|---|---|
| `frozen` | bool | `false` |  |
| `work_dir` | Path \| null | `null` |  |
| `keep_work_dir` | bool | `false` | **Deprecated** — no-op, the cache work directory is always kept. |
| `clean` | bool | `false` |  |
| `verbose` | int | `0` |  |
