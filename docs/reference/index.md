# Reference

Every `hpx` subcommand and flag, the full configuration schema, and the
Python API. For learning the tool, the
[Getting Started](../getting-started/index.md) and
[User Guide](../guide/configuration.md) pages are usually a better
starting point.

## CLI

<div class="grid cards" markdown>

-   :material-play:{ .lg .middle } __`hpx profile`__

    ---

    Build, flash, capture, report.

    [:octicons-arrow-right-24: profile](profile.md)

-   :material-stethoscope:{ .lg .middle } __`hpx doctor`__

    ---

    Verify toolchain and probe availability.

    [:octicons-arrow-right-24: doctor](doctor.md)

-   :material-developer-board:{ .lg .middle } __`hpx boards`__

    ---

    List supported boards.

    [:octicons-arrow-right-24: boards](boards.md)

-   :material-engine:{ .lg .middle } __`hpx engines`__

    ---

    List supported engines.

    [:octicons-arrow-right-24: engines](engines.md)

-   :material-usb-port:{ .lg .middle } __`hpx probes`__

  ---

  List and match J-Link probes.

  [:octicons-arrow-right-24: probes](probes.md)

-   :material-serial-port:{ .lg .middle } __`hpx ports`__

  ---

  List host serial ports.

  [:octicons-arrow-right-24: ports](ports.md)

-   :material-restart:{ .lg .middle } __`hpx target`__

  ---

  Run target reset helpers.

  [:octicons-arrow-right-24: target](target.md)

-   :material-chart-bar:{ .lg .middle } __`hpx analyze`__

  ---

  Static model analysis, no hardware.

  [:octicons-arrow-right-24: analyze](analyze.md)

-   :material-compare:{ .lg .middle } __`hpx compare`__

  ---

  Diff two profile result directories.

  [:octicons-arrow-right-24: compare](compare.md)

-   :material-check-decagram:{ .lg .middle } __`hpx validate`__

  ---

  Hardware-in-the-loop validation suite.

  [:octicons-arrow-right-24: validate](validate.md)

-   :material-power:{ .lg .middle } __`hpx power-on`__

  ---

  Hold Joulescope passthrough power on.

  [:octicons-arrow-right-24: power-on](power-on.md)

-   :material-folder-cog:{ .lg .middle } __`hpx cache`__

  ---

  Inspect or purge local build caches.

  [:octicons-arrow-right-24: cache](cache.md)

</div>

## Configuration Reference

Every `ProfileConfig` field, its YAML path, CLI flag, default, and type —
see the [Configuration Reference](configuration.md).

## Python API

Calling heliaPROFILER from Python instead of the shell? See the
[Python API reference](api/index.md) for `profile()`, `ProfileConfig`, and
the typed result and error types.

## Common conventions

- **Positional arg vs `--config`** — `hpx profile model.tflite` is a
  shortcut for `model.path`. With a config file, omit the positional
  arg and use `--config hpx.yml`.
- **CLI overrides YAML** — any flag passed on the command line wins
  over the matching field in the config file.
- **Verbose** — `-v` adds `INFO` logs, `-vv` adds `DEBUG`. Useful for
  diagnosing build/flash/capture failures.
- **Help** — every subcommand accepts `-h` / `--help`.
