# hpx ports

List host serial ports relevant to HPX transports

## hpx ports

```bash
hpx ports COMMAND [ARGS]...
```

List host serial ports relevant to HPX transports

Defined in `src/helia_profiler/cli/inspect_app.py` line 191.

## hpx ports list

```bash
hpx ports list [OPTIONS]
```

List serial ports with J-Link/CDC hints

Defined in `src/helia_profiler/cli/inspect_app.py` line 198.

### Options

| Name | Type | Default | Description |
| --- | --- | --- | --- |
| `--all` | `bool` | false | Show every host serial port, not just HPX-relevant USB/J-Link ports |
| `--json` | `bool` | false | Emit machine-readable JSON |

Generated from the `src/helia_profiler` tree `872d67ad4e9083b1eacd7d6d3859148437b96b59` with typer 0.26.8 and click 8.3.3.
