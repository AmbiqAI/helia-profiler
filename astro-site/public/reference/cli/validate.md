# hpx validate

Run hardware-in-the-loop validation suite (MLPerf Tiny models)

## hpx validate

```bash
hpx validate [OPTIONS]
```

Run hardware-in-the-loop validation suite (MLPerf Tiny models)

Defined in `src/helia_profiler/cli/validation_app.py` line 93.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--models` | `str` |  | Comma-separated model IDs (default: all). See `hpx validate --list`. |
| `--models-file` | `Optional[Path]` |  | YAML registry of custom validation models and comparison groups. |
| `--model-paths` | `str` |  | Comma-separated .tflite paths for an ad hoc comparison. |
| `--comparison-group` | `str` | custom | Shared decision group for models supplied through --model-paths. |
| `--model-arena-size` | `int` | 524288 | Arena size in bytes for models supplied through --model-paths. |
| `--engines` | `str` |  | Comma-separated engines: rt,aot,tflm,et,executorch,helia-rt,helia-aot (default: all). |
| `--executorch-backends` | `str` | both | ExecuTorch CMSIS-NN providers: arm, ns, or both (default: both). TFLM always uses ARM CMSIS-NN; heliaRT and heliaAOT always use ns-cmsis-nn. |
| `--ns-cmsis-nn-ref` | `str` |  | Exact ns-cmsis-nn commit/ref used by heliaRT, heliaAOT, and ExecuTorch/ns. |
| `--power` | `both \| on \| off` | off | Power matrix: off (default) \| on (only Joulescope runs) \| both. |
| `--power-boards` | `str` |  | Comma-separated boards allowed to use power capture (default: all selected boards). |
| `--boards` | `str` |  | Comma-separated board IDs (default: apollo510_evb). |
| `--toolchains` | `str` |  | Comma-separated toolchains: gcc,armclang/acfe,atfe (default: board defaults). |
| `--interfaces` | `str` |  | Comma-separated interfaces/transports: rtt,uart,swo,usb_cdc (default: board defaults). |
| `--memories` | `str` |  | Comma-separated model placement presets: auto,tcm,sram,mram,psram (default: board defaults). |
| `--suite` | `smoke \| models-rt \| models-aot \| complete` |  | Preset suite. 'smoke' defaults unset axes to models=kws, engines=helia-rt, toolchains=arm-none-eabi-gcc, interfaces=rtt, memories=auto. 'models-rt' and 'models-aot' default unset axes to all MLPerf Tiny models, Apollo510 + Apollo330mP, gcc + atfe, rtt, auto memory, and the selected engine. 'complete' runs the same axes for helia-rt, helia-aot, TFLM/CMSIS-NN, and both ExecuTorch CMSIS-NN providers. Explicit axis flags always win. |
| `--jlink-serials` | `str` |  | Comma-separated board=serial entries for multi-board validation. |
| `--power-serials` | `str` |  | Comma-separated board=Joulescope-serial entries for powered multi-board validation. |
| `--power-gpios` | `str` |  | Comma-separated board=gate:state:go entries for powered boards without default sync wiring. |
| `--repeat` | `int` | 1 | Repeat each selected case N times for stress testing (default: 1). |
| `--output-dir` | `Path` | results/validation | Where to write per-case artifacts + summary report (default: ./results/validation). |
| `--timeout` | `float` | 900 | Per-case timeout in seconds (default: 900). |
| `-k, -k` | `str` |  | Pytest keyword expression — filter cases by substring match (e.g. 'kws-aot'). |
| `--junit-xml` | `Optional[Path]` |  | Emit JUnit-XML report at this path (for CI consumption). |
| `--list` | `bool` | false | List matching cases and exit without running. |
| `--verbose, -v` | `int` | 0 |  |

### Examples

```text
Hardware validation — runs canonical MLPerf Tiny models end-to-end

against a real EVB + J-Link (and optional Joulescope).

Examples:

  hpx validate                         # Apollo510 reliability matrix, power off

  hpx validate --list                  # preview what would run

  hpx validate --models kws,ic         # subset by model

  hpx validate --engines aot           # subset by engine

  hpx validate --power off             # skip Joulescope (default)

  hpx validate --boards apollo3p_evb --repeat 2 --power off

                                       # require two passing iterations per case

  hpx validate -k kws-aot              # pytest keyword filter

  hpx validate --suite smoke           # quick preset: kws / helia-rt / gcc / rtt / auto

  hpx validate --suite models-rt       # 16-case RT sweep: 2 boards x 4 models x 2 toolchains

  hpx validate --suite models-aot      # 16-case AOT sweep: 2 boards x 4 models x 2 toolchains

  hpx validate --suite complete        # RT + AOT + TFLM + ExecuTorch sweep
```

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315` with typer 0.26.8 and click 8.3.3.
