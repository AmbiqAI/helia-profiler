# `hpx probes`

Inspect connected J-Link probes without opening an interactive SEGGER Commander
session.

## Synopsis

```bash
hpx probes list [--board BOARD] [--json]
hpx probes match --board BOARD [--jlink-serial SERIAL] [--json]
```

## Common Uses

List probes visible to the host:

```bash
hpx probes list
```

Inspect which probes can reach a board's expected core:

```bash
hpx probes list --board apollo510_evb
```

Resolve the serial HPX would use for a board:

```bash
hpx probes match --board apollo510_evb
```

Validate a known serial before a hardware run:

```bash
hpx probes match --board apollo510_evb --jlink-serial 1160002204
```

Use `--json` when scripting lab setup or generating validation inputs.

## Why Use This

`JLinkExe` is interactive by default and can remain open waiting for input.
These commands use HPX's bounded, non-interactive J-Link helpers and always
select probes using the same policy as `hpx profile` and `hpx validate`.
