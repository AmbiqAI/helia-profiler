# hpx transfers

Measure PSRAM-to-DTCM copy costs on the selected board.

## hpx transfers

```bash
hpx transfers [OPTIONS]
```

Measure PSRAM-to-DTCM copy costs on the selected board.

Defined in `src/helia_profiler/cli/transfers_cmd.py` line 9.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--config` | `Path` |  | Required. |
| `--output, -o` | `Path` |  | Required. |
| `--provider-plan` | `Path \| None` |  | Emit an independent DMA provider from this plan, retaining the calibration model settings. |

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
