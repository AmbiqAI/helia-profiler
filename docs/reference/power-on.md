# `hpx power-on`

Enable Joulescope current passthrough so the target board stays powered.

## Synopsis

```bash
hpx power-on [--driver joulescope|jsdrv]
```

## Description

When a board is powered through a Joulescope's `IN±`/`OUT±` terminals,
it only receives power while the Joulescope's current passthrough is
enabled — normally by the Joulescope desktop application or by an active
hpx power capture.

`hpx power-on` opens the Joulescope, enables passthrough, and holds the
connection open until you press ++ctrl+c++. Use it when the Joulescope
app is not running and the board would otherwise be unpowered — for
example while flashing, debugging, or running non-power profiles on a
Joulescope-wired board.

## Options

| Flag | Description |
| --- | --- |
| `--driver` | Joulescope driver to use (default: auto-detect). |

See [Power Profiling](../guide/power.md) for wiring and capture details.
