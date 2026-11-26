# hpx sweep

Measure plan candidates on the selected board.

## hpx sweep

```bash
hpx sweep [OPTIONS] PLANS
```

Measure plan candidates on the selected board.

Defined in `src/helia_profiler/cli/app.py` line 751.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `plans` | `list[Path]` |  | Ordered heliaAOT candidate plans. Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--config` | `Path` |  | Required. |
| `--output` | `Path` |  | Sweep artifacts and measurement cache. Required. |
| `--reference-digest` | `list[str]` |  | FNV1a64 per output, in tensor order. Required. Repeatable. |
| `--remeasure` | `Optional[list[str]]` |  | Recapture this plan SHA-256. Repeatable. |

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
