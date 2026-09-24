# helia_profiler.config.power

Power capture settings: which instrument measures the rail, how it is wired to the board, and which window of the run is integrated.

Every name on this page is imported from `helia_profiler`.

**API tier:** `stable`

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59`.

## helia_profiler.PowerConfig

`class` · `python`

```python
PowerConfig()
```

Power measurement settings.

**API tier:** `stable`

Source: `src/helia_profiler/config/power.py:232`

### helia_profiler.PowerConfig.enabled

`attribute` · `python`

```python
enabled: bool = False
```

Source: `src/helia_profiler/config/power.py:236`

### helia_profiler.PowerConfig.driver

`attribute` · `python`

```python
driver: str = DEFAULT_POWER_DRIVER
```

Source: `src/helia_profiler/config/power.py:237`

### helia_profiler.PowerConfig.firmware

`attribute` · `python`

```python
firmware: PowerFirmware = DEFAULT_POWER_FIRMWARE
```

Source: `src/helia_profiler/config/power.py:238`

### helia_profiler.PowerConfig.mode

`attribute` · `python`

```python
mode: PowerMode = DEFAULT_POWER_MODE
```

Source: `src/helia_profiler/config/power.py:239`

### helia_profiler.PowerConfig.duration_s

`attribute` · `python`

```python
duration_s: int | None = None
```

Source: `src/helia_profiler/config/power.py:244`

### helia_profiler.PowerConfig.io_voltage

`attribute` · `python`

```python
io_voltage: float = DEFAULT_IO_VOLTAGE
```

Source: `src/helia_profiler/config/power.py:245`

### helia_profiler.PowerConfig.sync_gpio_pin

`attribute` · `python`

```python
sync_gpio_pin: int = DEFAULT_SYNC_GPIO_PIN
```

Source: `src/helia_profiler/config/power.py:246`

### helia_profiler.PowerConfig.sync_input_index

`attribute` · `python`

```python
sync_input_index: int = DEFAULT_POWER_SYNC_INPUT_INDEX
```

Source: `src/helia_profiler/config/power.py:249`

### helia_profiler.PowerConfig.lockstep

`attribute` · `python`

```python
lockstep: bool | None = None
```

Source: `src/helia_profiler/config/power.py:253`

### helia_profiler.PowerConfig.state_gpio_pin

`attribute` · `python`

```python
state_gpio_pin: int = DEFAULT_STATE_GPIO_PIN
```

Source: `src/helia_profiler/config/power.py:254`

### helia_profiler.PowerConfig.go_gpio_pin

`attribute` · `python`

```python
go_gpio_pin: int = DEFAULT_GO_GPIO_PIN
```

Source: `src/helia_profiler/config/power.py:255`

### helia_profiler.PowerConfig.state_input_index

`attribute` · `python`

```python
state_input_index: int = DEFAULT_POWER_STATE_INPUT_INDEX
```

Source: `src/helia_profiler/config/power.py:256`

### helia_profiler.PowerConfig.go_output_index

`attribute` · `python`

```python
go_output_index: int = DEFAULT_POWER_GO_OUTPUT_INDEX
```

Source: `src/helia_profiler/config/power.py:257`

### helia_profiler.PowerConfig.stats_rate_hz

`attribute` · `python`

```python
stats_rate_hz: int = DEFAULT_POWER_STATS_RATE_HZ
```

Source: `src/helia_profiler/config/power.py:261`

### helia_profiler.PowerConfig.reset_strategy

`attribute` · `python`

```python
reset_strategy: ResetStrategy = ResetStrategy.AUTO
```

Source: `src/helia_profiler/config/power.py:264`

### helia_profiler.PowerConfig.serial

`attribute` · `python`

```python
serial: str | None = None
```

Source: `src/helia_profiler/config/power.py:268`

### helia_profiler.PowerConfig.ina228

`attribute` · `python`

```python
ina228: Ina228Config | None = None
```

Source: `src/helia_profiler/config/power.py:274`

### helia_profiler.PowerConfig.monitor_selected

`attribute` · `python`

```python
monitor_selected: bool
```

Whether generated power firmware talks to an on-target monitor.

The single source of truth for both firmware gates: NSX module
selection in ``firmware/__init__.py`` and render-context derivation
in ``PowerMonitorContext.from_config`` must agree, or a run could
silently build no monitor while appearing to configure one.

Source: `src/helia_profiler/config/power.py:277`

### helia_profiler.PowerConfig.gated_external_capture

`attribute` · `python`

```python
gated_external_capture: bool
```

Whether this run asks for host-gated *external* power capture.

The single source of the predicate that gates every piece of GPIO sync
machinery: the firmware's ``kPowerSyncEnabled`` (via
``SyncContext.power_sync_enabled`` and the NSX GPIO module selection in
``firmware/__init__.py``) and the host-side lock-step default below.
Internal (on-device monitor) mode measures inside the firmware and has
no host poller to race, so it is excluded.

Source: `src/helia_profiler/config/power.py:288`

### helia_profiler.PowerConfig.lockstep_wiring_available

`attribute` · `python`

```python
lockstep_wiring_available: bool
```

Whether the board carries the two extra lock-step wires.

``state`` (device -> host) and ``go`` (host -> device); ``0`` means the
wire is not assigned. Single-sourced because three consumers ask the
same question and must agree: the lock-step default
(:attr:`lockstep_resolved`), the ``power.lockstep: true`` config
validator, and the ``no_gate_rise`` diagnostic, which only names
lock-step as the likely fix when the wiring can actually support it.

Source: `src/helia_profiler/config/power.py:301`

### helia_profiler.PowerConfig.lockstep_resolved

`attribute` · `python`

```python
lockstep_resolved: bool
```

Effective 3-wire GPIO lock-step decision for this run.

An explicit ``power.lockstep`` always wins -- auto-enable is a
*default*, never an override, so ``lockstep: false`` still forces the
free-running path for bring-up on incomplete wiring.

Left unset, lock-step is enabled whenever the board is wired for it and
gated external capture is requested. The hazard it closes is not
family-specific: without lock-step ``kSyncLockstep`` bakes false,
``hpx_sync_wait_go()`` compiles to a no-op, and the target free-runs its
measured window straight out of reset. Any reset latency the host
spends after that -- flash-tool exit, JLinkExe teardown, poller
start-up -- races the gate. Apollo5's default
``debug_reset+swpoi_reset`` widens the gap most (two sequential
JLinkExe invocations), but every family can reproduce the same
``no_gate_rise`` degradation (issue #114). The condition is the
wiring and the mode, not the SoC family.

This is the one place both the firmware generator (which bakes
``kSyncLockstep`` in at build time, via ``FirmwareRenderContext``) and
the host-side capture path (which must arm/wait/signal accordingly)
resolve the *same* answer -- callers must not read
:attr:`lockstep` directly.

Source: `src/helia_profiler/config/power.py:314`
