# SEGGER RTT target sources

- Upstream: [SEGGERMicro/RTT](https://github.com/SEGGERMicro/RTT)
- Release: V8.58.0
- Commit: `4d8feab3150f86f37a9d323ddc88d6cdf5673072`
- Imported: 2026-07-17

This directory contains only the target source and configuration files used by
heliaPROFILER. The files are copied unchanged from the tagged upstream release.
At firmware-generation time, HPX copies them into the temporary application and
adds HPX-specific buffer placement settings to that temporary configuration.

To update the bundle, import the same paths from a tagged upstream release,
retain `LICENSE.md`, update this file, and run the firmware and packaging tests.
