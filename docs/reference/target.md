# `hpx target`

Run explicit target-side utility operations through HPX's non-interactive
J-Link wrappers.

## Synopsis

```bash
hpx target reset --board BOARD [--jlink-serial SERIAL] [--kind debug|swpoi]
```

## Reset Kinds

| Kind | Behavior |
| --- | --- |
| `debug` | Sends a debug reset/go sequence. This is the default and matches normal profiling reset behavior. |
| `swpoi` | Triggers software power-on-initialization reset. Use for controlled reset/power experiments only. |

Always pass `--jlink-serial` when multiple J-Link probes are attached.
