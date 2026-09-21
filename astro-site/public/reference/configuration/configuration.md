# Configuration reference

Every hpx.yml key: 106 fields across 15 configuration models, generated from the package.

## ProfileConfig

```python
ProfileConfig
```

Top-level immutable configuration for a profiling run.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `model` | `ModelConfig` |  | Required. |
| `engine` | `EngineConfig` | EngineConfig(type=<EngineType.HELIA_RT: 'helia-rt'>, backend=None, config={}, config_path=None) | Default built per instance. |
| `target` | `TargetConfig` | TargetConfig(board='apollo510_evb', toolchain=<Toolchain.ARM_NONE_EABI_GCC: 'arm-none-eabi-gcc'>, jlink_serial=None, transport=<Transport.RTT: 'rtt'>, usb_port=None, segger_rtt_path=None, rtt_buffer_size_up=None, clock=ClockSelection(cpu=None), psram=PsramConfig(clock_hz=48000000), heartbeat=HeartbeatConfig(enabled=True, every_n_ops=8, every_ms=2000, host_timeout_s=30, overall_timeout_s=None), custom_socs=None, custom_boards=None, ensure_board_powered=False) | Default built per instance. |
| `profiling` | `ProfilingConfig` | ProfilingConfig(pmu_counters={'cpu': 'default'}, per_layer=True, iterations=100, warmup=5, window_mode=<WindowMode.AUTO: 'auto'>, window_target_ms=1000, window_min=10, window_max=500000, clean_window_probe=<CleanWindowProbe.INFER: 'infer'>, clean_window_trace=False, force_shared_sram=False, aggregation=<Aggregation.MEDIAN: 'median'>, extreme_mode=False) | Default built per instance. |
| `power` | `PowerConfig` | PowerConfig(enabled=False, driver='joulescope', firmware=<PowerFirmware.DEDICATED: 'dedicated'>, mode=<PowerMode.EXTERNAL: 'external'>, duration_s=None, io_voltage=1.8, sync_gpio_pin=10, sync_input_index=0, lockstep=None, state_gpio_pin=0, go_gpio_pin=0, state_input_index=1, go_output_index=0, stats_rate_hz=1000, reset_strategy=<ResetStrategy.AUTO: 'auto'>, serial=None, ina228=None) | Default built per instance. |
| `output` | `OutputConfig` | OutputConfig(format=<OutputFormat.CSV: 'csv'>, dir=PosixPath('results'), model_explorer=True, detailed=False, fail_on_invalid=False) | Default built per instance. |
| `timeouts` | `TimeoutsConfig` | TimeoutsConfig(configure_s=120, build_s=300, flash_s=120, toolchain_probe_s=5, binary_probe_s=10, download_api_s=30, download_asset_s=300) | Default built per instance. |
| `build` | `BuildConfig` | BuildConfig(channel=None, nsx_modules={}, compiler_launcher='auto', update_dependencies=False, offline=False) | Default built per instance. |
| `platform_registry` | `PlatformRegistry` | PlatformRegistry(socs=mappingproxy({'apollo3p': SocDef(name='apollo3p', family=<SocFamily.AP3: 'ap3'>, core=<CoreArch.CORTEX_M4: 'cortex-m4'>, pmu_tier=<PmuTier.DWT_ONLY: 'dwt'>, has_mve=False, memory=MemoryLayout(mram_kb=2000, sram_kb=700, dtcm_kb=64, itcm_kb=0, psram_kb=0, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=48, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=96, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO3P', cmsis_header='apollo3p.h', rtt_scan_ranges=((268435456, 1048576),), jlink_device='AMA3B2KK-KBR', pmu_max_ops=2048, swo_trace_clock_mhz=48, has_usb=False, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo3p'), 'apollo4p': SocDef(name='apollo4p', family=<SocFamily.AP4: 'ap4'>, core=<CoreArch.CORTEX_M4: 'cortex-m4'>, pmu_tier=<PmuTier.DWT_ONLY: 'dwt'>, has_mve=False, memory=MemoryLayout(mram_kb=2000, sram_kb=1024, dtcm_kb=384, itcm_kb=0, psram_kb=0, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=192, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO4P', cmsis_header='apollo4p.h', rtt_scan_ranges=((268435456, 1048576),), jlink_device='AMAP42KP-KBR', pmu_max_ops=2048, swo_trace_clock_mhz=None, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo4p'), 'apollo4l': SocDef(name='apollo4l', family=<SocFamily.AP4: 'ap4'>, core=<CoreArch.CORTEX_M4: 'cortex-m4'>, pmu_tier=<PmuTier.DWT_ONLY: 'dwt'>, has_mve=False, memory=MemoryLayout(mram_kb=2000, sram_kb=1024, dtcm_kb=384, itcm_kb=0, psram_kb=0, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=192, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO4L', cmsis_header='apollo4l.h', rtt_scan_ranges=((268435456, 1048576),), jlink_device='AMAP42KL-KBR', pmu_max_ops=2048, swo_trace_clock_mhz=None, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo4l'), 'apollo510': SocDef(name='apollo510', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=4096, sram_kb=3072, dtcm_kb=512, itcm_kb=256, psram_kb=65536, nvm_kb=8192), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=250, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO510', cmsis_header='apollo510.h', rtt_scan_ranges=((536870912, 524288),), jlink_device='AP510NFA-CBR', pmu_max_ops=4096, swo_trace_clock_mhz=None, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo510'), 'apollo510b': SocDef(name='apollo510b', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=4096, sram_kb=3072, dtcm_kb=512, itcm_kb=256, psram_kb=65536, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=250, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO510B', cmsis_header='apollo510.h', rtt_scan_ranges=((536870912, 524288),), jlink_device='AP510BFA-CBR', pmu_max_ops=4096, swo_trace_clock_mhz=None, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo510b'), 'apollo5b': SocDef(name='apollo5b', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=4096, sram_kb=3072, dtcm_kb=512, itcm_kb=256, psram_kb=32768, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=250, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO5B', cmsis_header='apollo510.h', rtt_scan_ranges=((536870912, 524288),), jlink_device='AP510NFA-CBR', pmu_max_ops=4096, swo_trace_clock_mhz=None, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo5b'), 'apollo330P': SocDef(name='apollo330P', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=1984, sram_kb=1792, dtcm_kb=240, itcm_kb=0, psram_kb=32768, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=250, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO330P', cmsis_header='apollo330P.h', rtt_scan_ranges=((536870912, 245760),), jlink_device='Apollo330P_510L', pmu_max_ops=512, swo_trace_clock_mhz=48, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_1P75M', has_radio_subsystem=True, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo330P'), 'apollo510L': SocDef(name='apollo510L', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=1984, sram_kb=1792, dtcm_kb=240, itcm_kb=0, psram_kb=32768, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=96, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>), ClockSpeed(name='hp', mhz=250, perf_tier=<PerfTier.HIGH: 'NSX_PERF_HIGH'>)), default='lp'),), c_define='AM_PART_APOLLO510L', cmsis_header='apollo510L.h', rtt_scan_ranges=((536870912, 245760),), jlink_device='AP510L', pmu_max_ops=512, swo_trace_clock_mhz=48, has_usb=True, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_1P75M', has_radio_subsystem=True, npu=None, app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='apollo510L'), 'atomiq110': SocDef(name='atomiq110', family=<SocFamily.AP5: 'ap5'>, core=<CoreArch.CORTEX_M55: 'cortex-m55'>, pmu_tier=<PmuTier.ARMV8M_PMU: 'pmu'>, has_mve=True, memory=MemoryLayout(mram_kb=4096, sram_kb=3072, dtcm_kb=496, itcm_kb=256, psram_kb=0, nvm_kb=0), clocks=(ClockDomain(name='cpu', speeds=(ClockSpeed(name='lp', mhz=25, perf_tier=<PerfTier.LOW: 'NSX_PERF_LOW'>),), default='lp'),), c_define='PART_atomiq110', cmsis_header='atomiq110.h', rtt_scan_ranges=((536870912, 507904),), jlink_device='Atomiq110', pmu_max_ops=4096, swo_trace_clock_mhz=None, has_usb=False, ssram_full_power_enum='AM_HAL_PWRCTRL_SRAM_3M', has_radio_subsystem=False, npu='ethos-u85-256', app_flash_load_addr=None, memory_bases_like=None, origin=<SocOrigin.BUILTIN: 'builtin'>, registered_name='atomiq110')}), boards=mappingproxy({'apollo3p_evb': BoardDef(name='apollo3p_evb', soc='apollo3p', channel='stable', psram_kb=8192, default_sync_gpio_pin=26, default_state_gpio_pin=24, default_go_gpio_pin=25, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo3p_evb_cygnus': BoardDef(name='apollo3p_evb_cygnus', soc='apollo3p', channel='preview', psram_kb=8192, default_sync_gpio_pin=26, default_state_gpio_pin=24, default_go_gpio_pin=25, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo4p_evb': BoardDef(name='apollo4p_evb', soc='apollo4p', channel='preview', psram_kb=32768, default_sync_gpio_pin=22, default_state_gpio_pin=23, default_go_gpio_pin=24, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo4l_evb': BoardDef(name='apollo4l_evb', soc='apollo4l', channel='preview', psram_kb=32768, default_sync_gpio_pin=61, default_state_gpio_pin=23, default_go_gpio_pin=24, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo4l_blue_evb': BoardDef(name='apollo4l_blue_evb', soc='apollo4l', channel='preview', psram_kb=32768, default_sync_gpio_pin=61, default_state_gpio_pin=23, default_go_gpio_pin=24, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=55), 'apollo4p_blue_kbr_evb': BoardDef(name='apollo4p_blue_kbr_evb', soc='apollo4p', channel='preview', psram_kb=32768, default_sync_gpio_pin=22, default_state_gpio_pin=23, default_go_gpio_pin=24, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=42), 'apollo4p_blue_kxr_evb': BoardDef(name='apollo4p_blue_kxr_evb', soc='apollo4p', channel='preview', psram_kb=32768, default_sync_gpio_pin=22, default_state_gpio_pin=23, default_go_gpio_pin=24, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=55), 'apollo510_evb': BoardDef(name='apollo510_evb', soc='apollo510', channel='stable', psram_kb=None, default_sync_gpio_pin=29, default_state_gpio_pin=36, default_go_gpio_pin=14, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo510b_evb': BoardDef(name='apollo510b_evb', soc='apollo510b', channel='preview', psram_kb=None, default_sync_gpio_pin=29, default_state_gpio_pin=36, default_go_gpio_pin=14, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo5b_evb': BoardDef(name='apollo5b_evb', soc='apollo5b', channel='preview', psram_kb=None, default_sync_gpio_pin=10, default_state_gpio_pin=0, default_go_gpio_pin=0, starter_profile_board=None, is_fpga=False, description='', ble_reset_gpio_pin=None), 'apollo330mP_evb': BoardDef(name='apollo330mP_evb', soc='apollo330P', channel='preview', psram_kb=None, default_sync_gpio_pin=10, default_state_gpio_pin=0, default_go_gpio_pin=0, starter_profile_board=None, is_fpga=False, description='Apollo330 — Cortex-M55 (AP5 family)', ble_reset_gpio_pin=None), 'apollo510dL_evb': BoardDef(name='apollo510dL_evb', soc='apollo510L', channel='preview', psram_kb=0, default_sync_gpio_pin=10, default_state_gpio_pin=0, default_go_gpio_pin=0, starter_profile_board=None, is_fpga=False, description='Apollo510 Lite — Cortex-M55 (AP5 family)', ble_reset_gpio_pin=None), 'atomiq110_fpga_turbo': BoardDef(name='atomiq110_fpga_turbo', soc='atomiq110', channel='preview', psram_kb=None, default_sync_gpio_pin=10, default_state_gpio_pin=0, default_go_gpio_pin=0, starter_profile_board=None, is_fpga=True, description='Atomiq110 FPGA turbo — Cortex-M55 + Ethos-U85 NPU (fixed 25 MHz, FPGA-only)', ble_reset_gpio_pin=None)})) | Default built per instance. |
| `compatibility_baseline` | `CompatibilityBaseline` | CompatibilityBaseline(schema='hpx.compatibility-baseline', schema_version=1, baseline_id='hpx-neuralspotx-0.8.1-2026-09', neuralspotx_package='neuralspotx', neuralspotx_version='0.8.1', neuralspotx_sha256='7aac6f1b2e89ebf41dfe087c7588e0db11d65e8a11cac2cda03f6d7c510a9094', projects=(CompatibilityProject(name='neuralspotx', url='https://github.com/AmbiqAI/neuralspotx.git', ref='2dbe12a2799fd8c3df85f1a103b0adca340c901f'), CompatibilityProject(name='nsx-ambiq-sdk', url='https://github.com/AmbiqAI/nsx-ambiq-sdk.git', ref='aefce2ca858795e783c76726ebe7d14d9d4bde7c'), CompatibilityProject(name='nsx-ethos-u-driver', url='https://github.com/AmbiqAI/nsx-ethos-u-driver.git', ref='f0f99bb124b22486ef55694c76567008680cb5a8'), CompatibilityProject(name='nsx-pmu-armv8m', url='https://github.com/AmbiqAI/nsx-pmu-armv8m.git', ref='5725c065a0c3603132f1064ee2684d1fa8587c88'), CompatibilityProject(name='nsx-tflite-micro', url='https://github.com/AmbiqAI/nsx-tflite-micro.git', ref='7afcf2b4170e039caf4c49f91e2c45d5869be333'), CompatibilityProject(name='arm-cmsis-nn', url='https://github.com/AmbiqAI/arm-cmsis-nn.git', ref='6d21a6f821fb72541173a6c4d05d83329fa74f7c'), CompatibilityProject(name='ns-cmsis-nn', url='https://github.com/AmbiqAI/ns-cmsis-nn.git', ref='aaeb145a67c3decd9869f96474e36e7dbdc2030c'), CompatibilityProject(name='nsx-executorch', url='https://github.com/AmbiqAI/nsx-executorch.git', ref='5514ac1ea8439b3fe615d180bf68c75a9dabb48e'), CompatibilityProject(name='helia-rt', url='https://github.com/AmbiqAI/helia-rt.git', ref='edb3a25fc96c8e9b634dabdb9cd31cb22aa43440'), CompatibilityProject(name='nsx-sensors', url='https://github.com/AmbiqAI/nsx-sensors.git', ref='c219a2bc98c62f96819fae20ab6c8911fcea3e25')), modules=(CompatibilityModule(name='nsx-ambiq-bsp', project='nsx-ambiq-sdk', ref='aefce2ca858795e783c76726ebe7d14d9d4bde7c'), CompatibilityModule(name='nsx-npu', project='nsx-ambiq-sdk', ref='aefce2ca858795e783c76726ebe7d14d9d4bde7c'), CompatibilityModule(name='nsx-pmu-armv8m', project='nsx-pmu-armv8m', ref='5725c065a0c3603132f1064ee2684d1fa8587c88'), CompatibilityModule(name='nsx-tflite-micro', project='nsx-tflite-micro', ref='7afcf2b4170e039caf4c49f91e2c45d5869be333'), CompatibilityModule(name='arm-cmsis-nn', project='arm-cmsis-nn', ref='6d21a6f821fb72541173a6c4d05d83329fa74f7c'), CompatibilityModule(name='nsx-cmsis-nn', project='ns-cmsis-nn', ref='aaeb145a67c3decd9869f96474e36e7dbdc2030c'), CompatibilityModule(name='nsx-executorch', project='nsx-executorch', ref='5514ac1ea8439b3fe615d180bf68c75a9dabb48e'), CompatibilityModule(name='nsx-helia-rt', project='helia-rt', ref='edb3a25fc96c8e9b634dabdb9cd31cb22aa43440'), CompatibilityModule(name='nsx-sensors', project='nsx-sensors', ref='c219a2bc98c62f96819fae20ab6c8911fcea3e25')), engines=(CompatibilityEngine(name='helia-rt', version='1.20.0', ref='edb3a25fc96c8e9b634dabdb9cd31cb22aa43440', min_version=None, max_version_exclusive=None, governed_by_modules=False), CompatibilityEngine(name='helia-aot', version=None, ref=None, min_version='0.20.0', max_version_exclusive='0.21.0', governed_by_modules=False), CompatibilityEngine(name='tflm', version=None, ref=None, min_version=None, max_version_exclusive=None, governed_by_modules=True), CompatibilityEngine(name='executorch', version='0.1.0', ref='5514ac1ea8439b3fe615d180bf68c75a9dabb48e', min_version=None, max_version_exclusive=None, governed_by_modules=False))) | Default built per instance. |
| `compatibility` | `CompatibilityResolution \| None` |  |  |
| `frozen` | `bool` | false | Frozen |
| `work_dir` | `Path \| None` |  | Work Dir |
| `clean` | `bool` | false | Clean |
| `verbose` | `int` | 0 | Verbose |

## ModelConfig

```python
model:  # ModelConfig
```

Model file and arena sizing.

``arena_location`` and ``weights_location`` are the preferred placement
controls for runtime engines such as heliaRT: the arena is the mutable
tensor arena, while weights are the model flatbuffer/constant data.

    When a split field is omitted, the engine and memory planner choose the
    fastest region that fits. ``helia-aot`` translates these coarse controls
    into tensor rules; explicit ``engine.config.aot_args.memory.tensors`` rules
    remain available for per-kind and per-tensor placement.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `path` | `Path` |  | Path Required. |
| `arena_size` | `int \| None` |  | Arena Size |
| `arena_location` | `Placement \| str \| None` |  | Arena Location |
| `weights_location` | `Placement \| str \| None` |  | Weights Location |

## EngineConfig

```python
engine:  # EngineConfig
```

Inference engine selection and passthrough config.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `type` | `EngineType` | helia-rt |  |
| `backend` | `str \| None` |  | Backend |
| `config` | `dict[str, Any]` | {} | Config Default built per instance. |
| `config_path` | `Path \| None` |  | Config Path |

## TargetConfig

```python
target:  # TargetConfig
```

Hardware target.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `board` | `str` | apollo510_evb | Board |
| `toolchain` | `Toolchain` | arm-none-eabi-gcc |  |
| `jlink_serial` | `str \| None` |  | Jlink Serial |
| `transport` | `Transport` | rtt |  |
| `usb_port` | `str \| None` |  | Usb Port |
| `segger_rtt_path` | `Path \| None` |  | Segger Rtt Path |
| `rtt_buffer_size_up` | `int \| None` |  | Rtt Buffer Size Up |
| `clock` | `ClockSelection` | ClockSelection(cpu=None) | Default built per instance. |
| `psram` | `PsramConfig` | PsramConfig(clock_hz=48000000) | Default built per instance. |
| `heartbeat` | `HeartbeatConfig` | HeartbeatConfig(enabled=True, every_n_ops=8, every_ms=2000, host_timeout_s=30, overall_timeout_s=None) | Default built per instance. |
| `custom_socs` | `dict[str, Any] \| None` |  | Custom Socs |
| `custom_boards` | `dict[str, Any] \| None` |  | Custom Boards |
| `ensure_board_powered` | `bool` | false | Ensure Board Powered |

## ClockSelection

```python
target.clock:  # ClockSelection
```

Per-domain clock speed selection for the generated firmware.

Each field names a speed within the SoC's matching clock domain using
Ambiq datasheet terminology (e.g. ``cpu="hp"``).  ``None`` selects that
domain's default speed.  Values are validated against the resolved SoC in
stage 1, so unknown names raise a clear ConfigError rather than failing
silently.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `cpu` | `str \| None` |  | Cpu |

## PsramConfig

```python
target.psram:  # PsramConfig
```

External PSRAM interface selection.

The selected clock is passed to ``nsx-psram`` when a run places weights or
arenas in PSRAM. Board-specific support is enforced by the NSX module.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `clock_hz` | `int` | 48000000 | Clock Hz |

## HeartbeatConfig

```python
target.heartbeat:  # HeartbeatConfig
```

Liveness / progress-reporting settings.

The firmware emits ``HPX_HEARTBEAT`` lines at configurable intervals so
the host can (a) detect a hung run without using a large wall-clock
timeout, and (b) show the user live progress.

Attributes:
    enabled: Master switch.  When ``False``, no heartbeats are emitted or
        expected and the host falls back to the legacy line-gap timeout.
    every_n_ops: Emit a heartbeat after this many profiled ops.  ``0``
        disables this trigger.  Lower values add more PMU/inter-op
        overhead but give finer-grained progress.
    every_ms: Emit a heartbeat when at least this many wall-clock
        milliseconds have elapsed since the last heartbeat.  ``0``
        disables this trigger.  Useful for engines with a single large
        invocation (e.g. AOT command streams) where ``every_n_ops`` does
        not fire.
    host_timeout_s: Maximum time the host will wait without receiving
        *any* line from the firmware before declaring the run hung.
    overall_timeout_s: Hard ceiling on total capture time, in seconds.
        ``None`` means unbounded (rely on heartbeats).  Set to a positive
        int for a safety net in CI or unattended runs.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `bool` | true | Enabled |
| `every_n_ops` | `int` | 8 | Every N Ops |
| `every_ms` | `int` | 2000 | Every Ms |
| `host_timeout_s` | `int` | 30 | Host Timeout S |
| `overall_timeout_s` | `int \| None` |  | Overall Timeout S |

## ProfilingConfig

```python
profiling:  # ProfilingConfig
```

PMU capture settings.

Counter selection is specified via *pmu_counters* — a mapping of
compute-unit group (``cpu``, ``mve``, ``memory``, ``ethos_npu``) to a
selection:

* ``"default"`` — curated set of the most useful counters.
* ``"all"``     — every counter in the group (multi-pass).
* ``["NAME", …]`` — explicit counter names.

The ``ethos_npu`` group samples the Ethos-U NPU's own PMU (NPU cycles,
MAC activity, SRAM/external bus beats) and requires an NPU-equipped board
plus ``engine.backend: ethos_u`` (engine.type helia-rt or helia-aot).

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `pmu_counters` | `dict[str, str \| list[str]]` | {"cpu":"default"} | Pmu Counters Default built per instance. |
| `per_layer` | `bool` | true | Per Layer |
| `iterations` | `int` | 100 | Iterations |
| `warmup` | `int` | 5 | Warmup |
| `window_mode` | `WindowMode` | auto |  |
| `window_target_ms` | `int` | 1000 | Window Target Ms |
| `window_min` | `int` | 10 | Window Min |
| `window_max` | `int` | 500000 | Window Max |
| `clean_window_probe` | `CleanWindowProbe` | infer |  |
| `clean_window_trace` | `bool` | false | Clean Window Trace |
| `force_shared_sram` | `bool` | false | Force Shared Sram |
| `aggregation` | `Aggregation` | median |  |
| `extreme_mode` | `bool` | false | Extreme Mode |

## PowerConfig

```python
power:  # PowerConfig
```

Power measurement settings.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `enabled` | `bool` | false | Enabled |
| `driver` | `str` | joulescope | Driver |
| `firmware` | `PowerFirmware` | dedicated |  |
| `mode` | `PowerMode` | external |  |
| `duration_s` | `int \| None` |  | Duration S |
| `io_voltage` | `float` | 1.8 | Io Voltage |
| `sync_gpio_pin` | `int` | 10 | Sync Gpio Pin |
| `sync_input_index` | `int` | 0 | Sync Input Index |
| `lockstep` | `bool \| None` |  | Lockstep |
| `state_gpio_pin` | `int` | 0 | State Gpio Pin |
| `go_gpio_pin` | `int` | 0 | Go Gpio Pin |
| `state_input_index` | `int` | 1 | State Input Index |
| `go_output_index` | `int` | 0 | Go Output Index |
| `stats_rate_hz` | `int` | 1000 | Stats Rate Hz |
| `reset_strategy` | `ResetStrategy` | auto |  |
| `serial` | `str \| None` |  | Serial |
| `ina228` | `Ina228Config \| None` |  |  |

## Ina228Config

```python
power.ina228:  # Ina228Config
```

On-target INA228 power monitor wiring and calibration (I2C).

The INA228 sits in series with the target rail and integrates energy and
charge in hardware; firmware reads the accumulators over I2C around the
fixed-N inference window.

``board`` selects a known carrier's electrical facts (address strapping,
onboard shunt) from :data:`INA228_BOARD_PRESETS`; explicit values always
win over the preset. ``shunt_ohms`` has no bare default on purpose: a
wrong shunt calibration produces plausible-looking but wrong energy, so
the value must come either from the user's wiring or from a board that
physically carries its shunt.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `board` | `str \| None` |  | Board |
| `shunt_ohms` | `float \| None` |  | Shunt Ohms |
| `max_current_a` | `float` | 0.5 | Max Current A |
| `i2c_iom` | `int` | 1 | I2C Iom |
| `i2c_address` | `int \| None` |  | I2C Address |
| `i2c_speed_hz` | `int` | 400000 | I2C Speed Hz |
| `conversion_time_us` | `int` | 540 | Conversion Time Us |
| `averaging_count` | `int` | 16 | Averaging Count |
| `calibration_id` | `str \| None` |  | Calibration Id |

## OutputConfig

```python
output:  # OutputConfig
```

Report output settings.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `format` | `OutputFormat` | csv |  |
| `dir` | `Path` | results | Dir |
| `model_explorer` | `bool` | true | Model Explorer |
| `detailed` | `bool` | false | Detailed |
| `fail_on_invalid` | `bool` | false | Fail On Invalid |

## TimeoutsConfig

```python
timeouts:  # TimeoutsConfig
```

Subprocess and network timeouts (seconds).

Every subprocess and long-lived HTTP call in heliaPROFILER reads its
timeout from this struct instead of hard-coding it.  Override any value
in YAML under ``timeouts:`` to adapt to slow CI machines, laggy J-Link
probes, or poor network conditions.

Capture-time timeouts (heartbeat / overall) live on ``HeartbeatConfig``
because they are tied to the on-device progress protocol.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `configure_s` | `int` | 120 | Configure S |
| `build_s` | `int` | 300 | Build S |
| `flash_s` | `int` | 120 | Flash S |
| `toolchain_probe_s` | `int` | 5 | Toolchain Probe S |
| `binary_probe_s` | `int` | 10 | Binary Probe S |
| `download_api_s` | `int` | 30 | Download Api S |
| `download_asset_s` | `int` | 300 | Download Asset S |

## BuildConfig

```python
build:  # BuildConfig
```

NSX build-system overrides.

Controls how the generated firmware's NSX manifest resolves modules.
Default behaviour keeps the selected board's default NSX channel, and
generated manifests pin the qualified compatibility baseline's full commit
SHAs for the ``neuralspotx`` and ``nsx-ambiq-sdk`` projects unless the
user overrides those modules.

Advanced users can pin individual modules to a version, point them at
a local checkout, or select a custom git ref — useful for SoC/board
bring-up before changes land in the stable channel.

``compiler_launcher`` selects a CMake compiler launcher (e.g. ``sccache``
or ``ccache``) that wraps every compile to cache object output and speed
up repeated builds.  ``"auto"`` (the default) uses ``sccache`` then
``ccache`` if either is on ``PATH`` and otherwise does nothing — so the
mere presence of the binary is the opt-in.  ``"none"`` disables it; an
explicit tool name or path requires that the launcher be found.

Ordinary profiles reuse a structurally compatible ``nsx.lock`` byte for
byte and always materialize with frozen sync. ``update_dependencies`` is
the only mode that intentionally advances refs. ``offline`` additionally
requires the exact lock and all locked module trees to already exist.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `channel` | `str \| None` |  | Channel |
| `nsx_modules` | `dict[str, NsxModuleOverride]` | {} | Nsx Modules Default built per instance. |
| `compiler_launcher` | `str` | auto | Compiler Launcher |
| `update_dependencies` | `bool` | false | Update Dependencies |
| `offline` | `bool` | false | Offline |

## NsxModuleOverride

```python
build.nsx_modules:  # NsxModuleOverride
```

Override resolution for a single NSX module.

Exactly one mode must be set:
* *path* — use a local directory as the module source (``local: true``).
* *ref* — resolve the module's project at a specific git ref/tag.
* *version* — pin the module to an exact version constraint.

Only applies to modules NSX resolves itself (e.g. ``nsx-core``,
``nsx-ambiq-bsp``). Engine-provided modules (``nsx-helia-rt``,
``nsx-cmsis-nn``) are configured through ``engine.config``
(``dist_path``/``source_path``/``source``/``cmsis_nn_path``/``cmsis_nn_ref``) instead —
an entry here targeting one of those names is ignored with a warning.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `path` | `Path \| None` |  | Path |
| `ref` | `str \| None` |  | Ref |
| `version` | `str \| None` |  | Version |

## MonitorBoardPreset

```python
MonitorBoardPreset
```

Known breakout/Click board carrying an on-target power monitor chip.

A preset is pure data: the electrical facts a specific board fixes
(address strapping, an onboard shunt when the board has one) plus the
board-specific hint to show when a required fact is missing. Explicit
``power.ina228.*`` values always win over the preset. Adding support for
a new breakout of an already-supported chip is one registry entry here —
no new driver, no new firmware.

Not reachable from `ProfileConfig`; surfaced through a lookup table.

| Key | Type | Default | Description |
| --- | --- | --- | --- |
| `label` | `str` |  | Label Required. |
| `i2c_address` | `int \| None` |  | I2C Address |
| `shunt_ohms` | `float \| None` |  | Shunt Ohms |
| `missing_shunt_hint` | `str \| None` |  | Missing Shunt Hint |

## CleanWindowProbe

| Value |
| --- |
| `infer` |
| `busy_loop` |

## OutputFormat

| Value |
| --- |
| `csv` |
| `json` |
| `model-explorer` |

## PowerFirmware

| Value |
| --- |
| `dedicated` |
| `shared` |

## WindowMode

| Value |
| --- |
| `fixed` |
| `auto` |

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59` with pydantic 2.13.4.
