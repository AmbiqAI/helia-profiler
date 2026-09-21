# hpx target

Run explicit target-side utility operations

## hpx target

```bash
hpx target COMMAND [ARGS]...
```

Run explicit target-side utility operations

Defined in `src/helia_profiler/cli/inspect_app.py` line 216.

## hpx target reset

```bash
hpx target reset [OPTIONS]
```

Reset a target through HPX's non-interactive J-Link wrapper

Defined in `src/helia_profiler/cli/inspect_app.py` line 223.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `str` |  | Target board ID Required. |
| `--jlink-serial` | `Optional[str]` |  | J-Link probe serial number |
| `--kind` | `debug \| swpoi` | debug | Reset kind: debug r/g reset (default) or SWPOI reset |

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59` with typer 0.26.8 and click 8.3.3.
