# `hpx ports`

List host serial ports relevant to HPX transports.

## Synopsis

```bash
hpx ports list [--all] [--json]
```

## Output

The table includes the port path, a best-effort kind, USB serial number,
description, and product.  HPX classifies common cases as:

- `jlink-vcom` — SEGGER J-Link virtual COM port used by `--transport uart`.
- `hpx-usb-cdc` — target USB CDC port with an `HPX-<jlink_serial>` marker.
- `serial` — other host serial devices.

By default, `hpx ports list` hides built-in system serial ports such as
`/dev/ttyS*` and shows HPX-relevant USB/J-Link ports.  Use `--all` to include
the full host serial inventory.

Use `--json` when scripts need to map ports to probes or validation cases.
