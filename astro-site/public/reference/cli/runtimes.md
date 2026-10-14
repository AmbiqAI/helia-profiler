# hpx runtimes

Show the runtime versions this hpx records and their qualification

## hpx runtimes

```bash
hpx runtimes COMMAND [ARGS]...
```

Show the runtime versions this hpx records and their qualification

Defined in `src/helia_profiler/cli/app.py` line 706.

## hpx runtimes list

```bash
hpx runtimes list
```

List every runtime record

Defined in `src/helia_profiler/cli/app.py` line 713.

## hpx runtimes prepare

```bash
hpx runtimes prepare NAME [VERSION]
```

Build a runtime's prepared archive into the hpx cache (helia-rt, tflm)

Defined in `src/helia_profiler/cli/app.py` line 730.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `str` |  | Runtime name, e.g. helia-rt Required. |
| `version` | `Optional[str]` |  | Runtime version |

## hpx runtimes show

```bash
hpx runtimes show NAME [VERSION]
```

Print one runtime record (the default when VERSION is omitted)

Defined in `src/helia_profiler/cli/app.py` line 720.

### Arguments

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `str` |  | Runtime name, e.g. helia-rt Required. |
| `version` | `Optional[str]` |  | Runtime version |

Generated from the `src/helia_profiler` tree `__DOCS_SOURCE_TREE__` with typer 0.27.3 and click 8.3.3.
