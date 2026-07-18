"""Implementation of the ``hpx profile`` command."""

from __future__ import annotations

import argparse
import sys


def _cmd_profile(args: argparse.Namespace) -> None:
    """Run the profiling pipeline."""
    from ..config import load_config
    from ..console import HpxConsole
    from ..errors import HpxError

    # Build CLI overrides dict from parsed args
    cli: dict = {}
    _apply_model_engine_overrides(args, cli)
    _apply_target_overrides(args, cli)
    _apply_pmu_overrides(args, cli)
    _apply_power_overrides(args, cli)
    _apply_output_overrides(args, cli)
    _apply_workdir_overrides(args, cli)
    _apply_build_overrides(args, cli)

    # Use the CLI's own --verbose flag for error reporting during config load,
    # since a ConfigError means we never get a resolved ProfileConfig.verbose.
    console = HpxConsole(args.verbose)

    try:
        config = load_config(args.config, cli)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)

    console = HpxConsole(config.verbose)

    from ..api import profile

    try:
        profile(config)
    except KeyboardInterrupt:
        console.print_interrupted()
        sys.exit(130)
    except HpxError as exc:
        console.print_error(exc)
        sys.exit(1)


def _apply_model_engine_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply model/arena/engine CLI flags onto the config overrides dict."""
    if args.model is not None:
        cli.setdefault("model", {})["path"] = str(args.model)
    if args.arena_size is not None:
        cli.setdefault("model", {})["arena_size"] = args.arena_size
    if args.runtime_arena_location is not None:
        cli.setdefault("model", {})["arena_location"] = args.runtime_arena_location
    if args.runtime_weights_location is not None:
        cli.setdefault("model", {})["weights_location"] = args.runtime_weights_location
    if args.core_override is not None:
        cli.setdefault("engine", {}).setdefault("config", {})["core_override"] = args.core_override

    if args.engine is not None:
        cli.setdefault("engine", {})["type"] = args.engine
    if args.engine_config is not None:
        cli.setdefault("engine", {})["config_path"] = str(args.engine_config)


def _apply_target_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply target-hardware CLI flags onto the config overrides dict."""
    if args.board is not None:
        cli.setdefault("target", {})["board"] = args.board
    if args.toolchain is not None:
        cli.setdefault("target", {})["toolchain"] = args.toolchain
    if args.jlink_serial is not None:
        cli.setdefault("target", {})["jlink_serial"] = args.jlink_serial
    if args.transport is not None:
        cli.setdefault("target", {})["transport"] = args.transport
    if getattr(args, "usb_port", None) is not None:
        cli.setdefault("target", {})["usb_port"] = args.usb_port
    if args.rtt_buffer_size_up is not None:
        cli.setdefault("target", {})["rtt_buffer_size_up"] = args.rtt_buffer_size_up
    clock_sel: dict[str, str] = {}
    if args.cpu_clock is not None:
        clock_sel["cpu"] = args.cpu_clock
    if clock_sel:
        cli.setdefault("target", {})["clock"] = clock_sel
    if args.frozen:
        cli["frozen"] = True


def _apply_pmu_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply PMU-profiling CLI flags onto the config overrides dict."""
    if args.pmu_counters is not None:
        # Parse GROUP:SELECT pairs into a dict
        pmu_counters: dict[str, str | list[str]] = {}
        for spec in args.pmu_counters:
            if ":" not in spec:
                print(
                    f"Error: --pmu-counters format is GROUP:SELECT "
                    f"(e.g. cpu:default, mve:all). Got: '{spec}'",
                    file=sys.stderr,
                )
                sys.exit(1)
            group, sel = spec.split(":", 1)
            if sel in ("default", "all"):
                pmu_counters[group] = sel
            else:
                pmu_counters[group] = sel.split(",")
        cli.setdefault("profiling", {})["pmu_counters"] = pmu_counters
    if args.per_layer is not None:
        cli.setdefault("profiling", {})["per_layer"] = args.per_layer
    if args.iterations is not None:
        cli.setdefault("profiling", {})["iterations"] = args.iterations
    if args.warmup is not None:
        cli.setdefault("profiling", {})["warmup"] = args.warmup
    if getattr(args, "aggregation", None) is not None:
        cli.setdefault("profiling", {})["aggregation"] = args.aggregation


def _apply_power_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply power-measurement CLI flags onto the config overrides dict."""
    if args.power:
        cli.setdefault("power", {})["enabled"] = True
    if args.power_driver is not None:
        cli.setdefault("power", {})["driver"] = args.power_driver
    if getattr(args, "power_firmware", None) is not None:
        cli.setdefault("power", {})["firmware"] = args.power_firmware
    if args.power_mode is not None:
        cli.setdefault("power", {})["mode"] = args.power_mode
    if args.power_duration is not None:
        cli.setdefault("power", {})["duration_s"] = args.power_duration
    if getattr(args, "power_reset_strategy", None) is not None:
        cli.setdefault("power", {})["reset_strategy"] = args.power_reset_strategy
    if args.sync_gpio is not None:
        cli.setdefault("power", {})["sync_gpio_pin"] = args.sync_gpio
    if getattr(args, "ensure_power", False):
        cli.setdefault("target", {})["ensure_board_powered"] = True
    if getattr(args, "no_ensure_power", False):
        cli.setdefault("target", {})["ensure_board_powered"] = False
    if getattr(args, "power_serial", None):
        cli.setdefault("power", {})["serial"] = args.power_serial


def _apply_output_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply output-related CLI flags onto the config overrides dict."""
    if args.output_dir is not None:
        cli.setdefault("output", {})["dir"] = str(args.output_dir)
    if args.output_format is not None:
        cli.setdefault("output", {})["format"] = args.output_format
    if args.no_model_explorer:
        cli.setdefault("output", {})["model_explorer"] = False
    if args.detailed:
        cli.setdefault("output", {})["detailed"] = True


def _apply_workdir_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply working-directory/advanced CLI flags onto the config overrides dict."""
    if args.work_dir is not None:
        cli["work_dir"] = str(args.work_dir)
    if args.clean:
        cli["clean"] = True
    cli["verbose"] = args.verbose


def _apply_build_overrides(args: argparse.Namespace, cli: dict) -> None:
    """Apply build/NSX-override CLI flags onto the config overrides dict."""
    if getattr(args, "nsx_channel", None):
        cli.setdefault("build", {})["channel"] = args.nsx_channel
    if getattr(args, "no_compiler_launcher", False):
        cli.setdefault("build", {})["compiler_launcher"] = "none"
    elif getattr(args, "compiler_launcher", None):
        cli.setdefault("build", {})["compiler_launcher"] = args.compiler_launcher
    nsx_overrides_raw = getattr(args, "nsx_module_overrides", None)
    if nsx_overrides_raw:
        nsx_modules: dict[str, dict[str, str]] = {}
        for spec in nsx_overrides_raw:
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
