# hpx results

Inspect and export verified profile bundles and saved run results.

## hpx results

```bash
hpx results COMMAND [ARGS]...
```

Inspect and export verified profile bundles and saved run results.

Defined in `src/helia_profiler/cli/results_cmd.py` line 26.

## hpx results export

```bash
hpx results export [OPTIONS] LOCATION
```

Write portable JSON or recorded transfer traces; existing files are never overwritten.

Defined in `src/helia_profiler/cli/results_cmd.py` line 76.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output` | `Path` |  | Required. |
| `--format` | `json \| perfetto` | json |  |

## hpx results inspect

```bash
hpx results inspect LOCATION
```

Emit a structured inspection; a verified failed run is not corrupt evidence.

Defined in `src/helia_profiler/cli/results_cmd.py` line 31.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

## hpx results show

```bash
hpx results show LOCATION
```

Emit the portable JSON view of a verified profile bundle or saved run result.

Defined in `src/helia_profiler/cli/results_cmd.py` line 66.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
