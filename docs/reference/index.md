# CLI Reference

Every `hpx` subcommand and its flags. For learning the tool, the
[Getting Started](../getting-started/index.md) and
[User Guide](../guide/configuration.md) pages are usually a better
starting point.

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

</div>

## Common conventions

- **Positional arg vs `--config`** — `hpx profile model.tflite` is a
  shortcut for `model.path`. With a config file, omit the positional
  arg and use `--config hpx.yml`.
- **CLI overrides YAML** — any flag passed on the command line wins
  over the matching field in the config file.
- **Verbose** — `-v` adds `INFO` logs, `-vv` adds `DEBUG`. Useful for
  diagnosing build/flash/capture failures.
- **Help** — every subcommand accepts `-h` / `--help`.
