# hpx probes

Inspect connected J-Link probes without opening an interactive SEGGER commander session

## hpx probes

```bash
hpx probes COMMAND [ARGS]...
```

Inspect connected J-Link probes without opening an interactive SEGGER commander session

Defined in `src/helia_profiler/cli/inspect_app.py` line 153.

## hpx probes list

```bash
hpx probes list [OPTIONS]
```

List connected J-Link probes

Defined in `src/helia_profiler/cli/inspect_app.py` line 160.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `Optional[str]` |  | Inspect each probe against this board's J-Link device string |
| `--inspect` | `bool` | false | Inspect target cores. Requires --board. |
| `--json` | `bool` | false | Emit machine-readable JSON |

## hpx probes match

```bash
hpx probes match [OPTIONS]
```

Resolve the J-Link serial for a board using HPX's normal selection policy

Defined in `src/helia_profiler/cli/inspect_app.py` line 178.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--board` | `str` |  | Target board ID Required. |
| `--jlink-serial` | `Optional[str]` |  | Optional requested serial to validate against the selected board |
| `--json` | `bool` | false | Emit machine-readable JSON |

Generated from the `src/helia_profiler` tree `d8fc7a994d8f2b900482cdc6119d92751a2b7315` with typer 0.26.8 and click 8.3.3.
