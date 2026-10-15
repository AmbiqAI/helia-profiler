# hpx results

Inspect and export verified ordinary profile bundles.

## hpx results

```bash
hpx results COMMAND [ARGS]...
```

Inspect and export verified ordinary profile bundles.

Defined in `src/helia_profiler/cli/results_cmd.py` line 20.

## hpx results export

```bash
hpx results export [OPTIONS] LOCATION
```

Write a new portable JSON file; existing evidence is never overwritten.

Defined in `src/helia_profiler/cli/results_cmd.py` line 70.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--output` | `Path` |  | Required. |

## hpx results inspect

```bash
hpx results inspect LOCATION
```

Emit a structured inspection; a verified failed run is not corrupt evidence.

Defined in `src/helia_profiler/cli/results_cmd.py` line 25.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

## hpx results show

```bash
hpx results show LOCATION
```

Emit the portable JSON view of a verified native bundle.

Defined in `src/helia_profiler/cli/results_cmd.py` line 60.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `location` | `Path` |  | Required. |

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
