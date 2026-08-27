"""Implementation of the ``hpx profile`` command."""

from __future__ import annotations

import sys
from pathlib import Path

from ..results import ResultValidity


def _cmd_profile(
    *,
    model: Path | None = None,
    config: Path | None = None,
    verbose: int = 0,
    engine: str | None = None,
    engine_config: Path | None = None,
    arena_size: int | None = None,
    runtime_arena_location: str | None = None,
    runtime_weights_location: str | None = None,
    core_override: str | None = None,
    board: str | None = None,
    toolchain: str | None = None,
    jlink_serial: str | None = None,
    transport: str | None = None,
    usb_port: str | None = None,
    rtt_buffer_size_up: int | None = None,
    cpu_clock: str | None = None,
    frozen: bool = False,
    offline: bool = False,
    update_dependencies: bool = False,
    nsx_channel: str | None = None,
    nsx_module: list[str] | None = None,
    compiler_launcher: str | None = None,
    no_compiler_launcher: bool = False,
    pmu_counters: list[str] | None = None,
    per_layer: bool | None = None,
    iterations: int | None = None,
    warmup: int | None = None,
    aggregation: str | None = None,
    power: bool = False,
    power_driver: str | None = None,
    power_mode: str | None = None,
    power_duration: int | None = None,
    power_firmware: str | None = None,
    power_reset_strategy: str | None = None,
    sync_gpio: int | None = None,
    ensure_power: bool = False,
    no_ensure_power: bool = False,
    power_serial: str | None = None,
    output_dir: Path | None = None,
    output_format: str | None = None,
    no_model_explorer: bool = False,
    fail_on_invalid: bool = False,
    detailed: bool = False,
    work_dir: Path | None = None,
    clean: bool = False,
) -> None:
    """Run the profiling pipeline."""
    from ..config import load_config
    from ..console import HpxConsole
    from ..errors import HpxError

    # Build CLI overrides dict from the typed CLI values
    cli: dict = {}
    _apply_model_engine_overrides(
        cli,
        model=model,
        arena_size=arena_size,
        runtime_arena_location=runtime_arena_location,
        runtime_weights_location=runtime_weights_location,
        core_override=core_override,
        engine=engine,
        engine_config=engine_config,
    )
    _apply_target_overrides(
        cli,
        board=board,
        toolchain=toolchain,
        jlink_serial=jlink_serial,
        transport=transport,
        usb_port=usb_port,
        rtt_buffer_size_up=rtt_buffer_size_up,
        cpu_clock=cpu_clock,
        frozen=frozen,
        offline=offline,
        update_dependencies=update_dependencies,
    )
    _apply_pmu_overrides(
        cli,
        pmu_counters=pmu_counters,
        per_layer=per_layer,
        iterations=iterations,
        warmup=warmup,
        aggregation=aggregation,
    )
    _apply_power_overrides(
        cli,
        power=power,
        power_driver=power_driver,
        power_firmware=power_firmware,
        power_mode=power_mode,
        power_duration=power_duration,
        power_reset_strategy=power_reset_strategy,
        sync_gpio=sync_gpio,
        ensure_power=ensure_power,
        no_ensure_power=no_ensure_power,
        power_serial=power_serial,
    )
    _apply_output_overrides(
        cli,
        output_dir=output_dir,
        output_format=output_format,
        no_model_explorer=no_model_explorer,
        detailed=detailed,
        fail_on_invalid=fail_on_invalid,
    )
    _apply_workdir_overrides(cli, work_dir=work_dir, clean=clean, verbose=verbose)
    _apply_build_overrides(
        cli,
        nsx_channel=nsx_channel,
        compiler_launcher=compiler_launcher,
        no_compiler_launcher=no_compiler_launcher,
        nsx_module=nsx_module,
    )

    # Use the CLI's own --verbose flag for error reporting during config load,
    # since a ConfigError means we never get a resolved ProfileConfig.verbose.
    console = HpxConsole(verbose)

    try:
        resolved_config = load_config(config, cli)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    console = HpxConsole(resolved_config.verbose)

    from ..profiler import run_profile

    try:
        ctx = run_profile(resolved_config, console=console)
    except KeyboardInterrupt:
        console.print_interrupted()
        sys.exit(130)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    # #197: opt-in exit policy for automation. Deliberately AFTER the run
    # completes -- artifacts are written, the console footer rendered the
    # verdict, and comparability already blocks invalid runs; 3 is distinct
    # from 1 (error) / 2 (usage) / 130 (interrupt). Reads the STORED
    # evaluation only: every run that returns from run_profile has passed
    # write_report (the report stage never skips), so if a skip path is
    # ever introduced this policy must gain the footer's fresh-evaluate
    # fallback with it.
    if (
        ctx is not None
        and ctx.run_evaluation is not None
        and ctx.run_evaluation.validity is ResultValidity.INVALID
        and ctx.config.output.fail_on_invalid
    ):
        sys.exit(3)


def _apply_model_engine_overrides(
    cli: dict,
    *,
    model: Path | None,
    arena_size: int | None,
    runtime_arena_location: str | None,
    runtime_weights_location: str | None,
    core_override: str | None,
    engine: str | None,
    engine_config: Path | None,
) -> None:
    """Apply model/arena/engine CLI flags onto the config overrides dict."""
    if model is not None:
        cli.setdefault("model", {})["path"] = str(model)
    if arena_size is not None:
        cli.setdefault("model", {})["arena_size"] = arena_size
    if runtime_arena_location is not None:
        cli.setdefault("model", {})["arena_location"] = runtime_arena_location
    if runtime_weights_location is not None:
        cli.setdefault("model", {})["weights_location"] = runtime_weights_location
    if core_override is not None:
        cli.setdefault("engine", {}).setdefault("config", {})["core_override"] = core_override

    if engine is not None:
        cli.setdefault("engine", {})["type"] = engine
    if engine_config is not None:
        cli.setdefault("engine", {})["config_path"] = str(engine_config)


def _apply_target_overrides(
    cli: dict,
    *,
    board: str | None,
    toolchain: str | None,
    jlink_serial: str | None,
    transport: str | None,
    usb_port: str | None,
    rtt_buffer_size_up: int | None,
    cpu_clock: str | None,
    frozen: bool,
    offline: bool,
    update_dependencies: bool,
) -> None:
    """Apply target-hardware CLI flags onto the config overrides dict."""
    if board is not None:
        cli.setdefault("target", {})["board"] = board
    if toolchain is not None:
        cli.setdefault("target", {})["toolchain"] = toolchain
    if jlink_serial is not None:
        cli.setdefault("target", {})["jlink_serial"] = jlink_serial
    if transport is not None:
        cli.setdefault("target", {})["transport"] = transport
    if usb_port is not None:
        cli.setdefault("target", {})["usb_port"] = usb_port
    if rtt_buffer_size_up is not None:
        cli.setdefault("target", {})["rtt_buffer_size_up"] = rtt_buffer_size_up
    clock_sel: dict[str, str] = {}
    if cpu_clock is not None:
        clock_sel["cpu"] = cpu_clock
    if clock_sel:
        cli.setdefault("target", {})["clock"] = clock_sel
    if frozen:
        cli["frozen"] = True
    if offline:
        cli.setdefault("build", {})["offline"] = True
    if update_dependencies:
        cli.setdefault("build", {})["update_dependencies"] = True


def _apply_pmu_overrides(
    cli: dict,
    *,
    pmu_counters: list[str] | None,
    per_layer: bool | None,
    iterations: int | None,
    warmup: int | None,
    aggregation: str | None,
) -> None:
    """Apply PMU-profiling CLI flags onto the config overrides dict."""
    if pmu_counters is not None:
        # Parse GROUP:SELECT pairs into a dict
        parsed_counters: dict[str, str | list[str]] = {}
        for spec in pmu_counters:
            if ":" not in spec:
                print(
                    f"Error: --pmu-counters format is GROUP:SELECT "
                    f"(e.g. cpu:default, mve:all). Got: '{spec}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            group, sel = spec.split(":", 1)
            if sel in ("default", "all"):
                parsed_counters[group] = sel
            else:
                parsed_counters[group] = sel.split(",")
        cli.setdefault("profiling", {})["pmu_counters"] = parsed_counters
    if per_layer is not None:
        cli.setdefault("profiling", {})["per_layer"] = per_layer
    if iterations is not None:
        cli.setdefault("profiling", {})["iterations"] = iterations
    if warmup is not None:
        cli.setdefault("profiling", {})["warmup"] = warmup
    if aggregation is not None:
        cli.setdefault("profiling", {})["aggregation"] = aggregation


def _apply_power_overrides(
    cli: dict,
    *,
    power: bool,
    power_driver: str | None,
    power_firmware: str | None,
    power_mode: str | None,
    power_duration: int | None,
    power_reset_strategy: str | None,
    sync_gpio: int | None,
    ensure_power: bool,
    no_ensure_power: bool,
    power_serial: str | None,
) -> None:
    """Apply power-measurement CLI flags onto the config overrides dict."""
    if power:
        cli.setdefault("power", {})["enabled"] = True
    if power_driver is not None:
        cli.setdefault("power", {})["driver"] = power_driver
    if power_firmware is not None:
        cli.setdefault("power", {})["firmware"] = power_firmware
    if power_mode is not None:
        cli.setdefault("power", {})["mode"] = power_mode
    if power_duration is not None:
        cli.setdefault("power", {})["duration_s"] = power_duration
    if power_reset_strategy is not None:
        cli.setdefault("power", {})["reset_strategy"] = power_reset_strategy
    if sync_gpio is not None:
        cli.setdefault("power", {})["sync_gpio_pin"] = sync_gpio
    if ensure_power:
        cli.setdefault("target", {})["ensure_board_powered"] = True
    if no_ensure_power:
        cli.setdefault("target", {})["ensure_board_powered"] = False
    if power_serial:
        cli.setdefault("power", {})["serial"] = power_serial


def _apply_output_overrides(
    cli: dict,
    *,
    output_dir: Path | None,
    output_format: str | None,
    no_model_explorer: bool,
    detailed: bool,
    fail_on_invalid: bool,
) -> None:
    """Apply output-related CLI flags onto the config overrides dict."""
    if output_dir is not None:
        cli.setdefault("output", {})["dir"] = str(output_dir)
    if output_format is not None:
        cli.setdefault("output", {})["format"] = output_format
    if no_model_explorer:
        cli.setdefault("output", {})["model_explorer"] = False
    if detailed:
        cli.setdefault("output", {})["detailed"] = True
    if fail_on_invalid:
        cli.setdefault("output", {})["fail_on_invalid"] = True


def _apply_workdir_overrides(
    cli: dict, *, work_dir: Path | None, clean: bool, verbose: int
) -> None:
    """Apply working-directory/advanced CLI flags onto the config overrides dict."""
    if work_dir is not None:
        cli["work_dir"] = str(work_dir)
    if clean:
        cli["clean"] = True
    cli["verbose"] = verbose


def _apply_build_overrides(
    cli: dict,
    *,
    nsx_channel: str | None,
    compiler_launcher: str | None,
    no_compiler_launcher: bool,
    nsx_module: list[str] | None,
) -> None:
    """Apply build/NSX-override CLI flags onto the config overrides dict."""
    if nsx_channel:
        cli.setdefault("build", {})["channel"] = nsx_channel
    if no_compiler_launcher:
        cli.setdefault("build", {})["compiler_launcher"] = "none"
    elif compiler_launcher:
        cli.setdefault("build", {})["compiler_launcher"] = compiler_launcher
    if nsx_module:
        nsx_modules: dict[str, dict[str, str]] = {}
        for spec in nsx_module:
            if ":" not in spec:
                print(
                    f"Error: --nsx-module format is NAME:KEY=VALUE "
                    f"(e.g. nsx-ambiq-bsp:path=/my/bsp). Got: '{spec}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            name, kv = spec.split(":", 1)
            if "=" not in kv:
                print(
                    f"Error: --nsx-module value must be KEY=VALUE "
                    f"(e.g. path=/my/bsp, ref=feat/new-soc, version=2.0.0). "
                    f"Got: '{kv}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            key, val = kv.split("=", 1)
            if key not in ("path", "ref", "version"):
                print(
                    f"Error: --nsx-module key must be 'path', 'ref', or 'version'. Got: '{key}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            nsx_modules.setdefault(name, {})[key] = val
        cli.setdefault("build", {})["nsx_modules"] = nsx_modules
