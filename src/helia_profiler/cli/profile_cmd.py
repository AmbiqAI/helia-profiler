"""Implementation of the ``hpx profile`` command.

The ``hpx profile`` flag surface is declared exactly once as typed Typer
parameters in ``app.py``; this module maps those parameter values onto the
config-overrides dict consumed by :func:`helia_profiler.config.load_config`.
The mapping is table-driven (`_OVERRIDE_SPECS`): one spec per CLI parameter
naming its destination path in the overrides dict and how to apply it, so a
new flag is a one-line Typer parameter plus a one-line spec.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..results import ResultValidity

_UNSET: Any = object()


@dataclass(frozen=True)
class _OverrideSpec:
    """Mapping from one CLI parameter to its overrides-dict destination.

    ``path`` is the destination in the nested overrides dict (last element is
    the key, the rest are ``setdefault``-created sections). ``apply`` decides
    when the parameter takes effect: ``not_none`` (value flags with a None
    sentinel), ``truthy`` (boolean/presence flags), or ``always``.  ``const``
    stores a fixed value instead of the parameter's own (e.g. ``--no-ensure-
    power`` storing False); ``coerce`` transforms the value first.  Specs
    apply in table order; ``overwrite=False`` makes a spec yield to an
    earlier one that already set the same key (the ``--no-compiler-launcher``
    vs ``--compiler-launcher`` precedence).
    """

    param: str
    path: tuple[str, ...]
    default: Any = None
    apply: Literal["not_none", "truthy", "always"] = "not_none"
    const: Any = _UNSET
    coerce: Callable[[Any], Any] | None = None
    overwrite: bool = True


def _parse_pmu_counters(specs: list[str]) -> dict[str, str | list[str]]:
    """Parse repeatable ``--pmu-counters GROUP:SELECT`` values."""
    parsed: dict[str, str | list[str]] = {}
    for spec in specs:
        if ":" not in spec:
            print(
                f"Error: --pmu-counters format is GROUP:SELECT "
                f"(e.g. cpu:default, mve:all). Got: '{spec}'",
                file=sys.stderr,
            )
            sys.exit(1)
        group, sel = spec.split(":", 1)
        if sel in ("default", "all"):
            parsed[group] = sel
        else:
            parsed[group] = sel.split(",")
    return parsed


def _parse_nsx_modules(specs: list[str]) -> dict[str, dict[str, str]]:
    """Parse repeatable ``--nsx-module NAME:KEY=VALUE`` values."""
    modules: dict[str, dict[str, str]] = {}
    for spec in specs:
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
        modules.setdefault(name, {})[key] = val
    return modules


_OVERRIDE_SPECS: tuple[_OverrideSpec, ...] = (
    # -- model / engine --
    _OverrideSpec("model", ("model", "path"), coerce=str),
    _OverrideSpec("arena_size", ("model", "arena_size")),
    _OverrideSpec("runtime_arena_location", ("model", "arena_location")),
    _OverrideSpec("runtime_weights_location", ("model", "weights_location")),
    _OverrideSpec("core_override", ("engine", "config", "core_override")),
    _OverrideSpec("engine", ("engine", "type")),
    _OverrideSpec("engine_config", ("engine", "config_path"), coerce=str),
    # -- target hardware --
    _OverrideSpec("board", ("target", "board")),
    _OverrideSpec("toolchain", ("target", "toolchain")),
    _OverrideSpec("jlink_serial", ("target", "jlink_serial")),
    _OverrideSpec("transport", ("target", "transport")),
    _OverrideSpec("usb_port", ("target", "usb_port")),
    _OverrideSpec("rtt_buffer_size_up", ("target", "rtt_buffer_size_up")),
    _OverrideSpec("cpu_clock", ("target", "clock"), coerce=lambda v: {"cpu": v}),
    _OverrideSpec("frozen", ("frozen",), default=False, apply="truthy", const=True),
    _OverrideSpec("offline", ("build", "offline"), default=False, apply="truthy", const=True),
    _OverrideSpec(
        "update_dependencies",
        ("build", "update_dependencies"),
        default=False,
        apply="truthy",
        const=True,
    ),
    # -- PMU profiling --
    _OverrideSpec("pmu_counters", ("profiling", "pmu_counters"), coerce=_parse_pmu_counters),
    _OverrideSpec("per_layer", ("profiling", "per_layer")),
    _OverrideSpec("iterations", ("profiling", "iterations")),
    _OverrideSpec("warmup", ("profiling", "warmup")),
    _OverrideSpec("aggregation", ("profiling", "aggregation")),
    # -- power measurement --
    _OverrideSpec("power", ("power", "enabled"), default=False, apply="truthy", const=True),
    _OverrideSpec("power_driver", ("power", "driver")),
    _OverrideSpec("power_firmware", ("power", "firmware")),
    _OverrideSpec("power_mode", ("power", "mode")),
    _OverrideSpec("power_duration", ("power", "duration_s")),
    _OverrideSpec("power_reset_strategy", ("power", "reset_strategy")),
    _OverrideSpec("sync_gpio", ("power", "sync_gpio_pin")),
    _OverrideSpec(
        "ensure_power",
        ("target", "ensure_board_powered"),
        default=False,
        apply="truthy",
        const=True,
    ),
    _OverrideSpec(
        "no_ensure_power",
        ("target", "ensure_board_powered"),
        default=False,
        apply="truthy",
        const=False,
    ),
    _OverrideSpec("power_serial", ("power", "serial"), apply="truthy"),
    # -- output --
    _OverrideSpec("output_dir", ("output", "dir"), coerce=str),
    _OverrideSpec("output_format", ("output", "format")),
    _OverrideSpec(
        "no_model_explorer",
        ("output", "model_explorer"),
        default=False,
        apply="truthy",
        const=False,
    ),
    _OverrideSpec("detailed", ("output", "detailed"), default=False, apply="truthy", const=True),
    _OverrideSpec(
        "fail_on_invalid", ("output", "fail_on_invalid"), default=False, apply="truthy", const=True
    ),
    # -- advanced --
    _OverrideSpec("work_dir", ("work_dir",), coerce=str),
    _OverrideSpec("clean", ("clean",), default=False, apply="truthy", const=True),
    _OverrideSpec("verbose", ("verbose",), default=0, apply="always"),
    # -- build / NSX overrides -- (last, matching the historical apply order:
    # with several malformed flags the --pmu-counters parse error wins)
    _OverrideSpec("nsx_channel", ("build", "channel"), apply="truthy"),
    _OverrideSpec(
        "no_compiler_launcher",
        ("build", "compiler_launcher"),
        default=False,
        apply="truthy",
        const="none",
    ),
    _OverrideSpec(
        "compiler_launcher", ("build", "compiler_launcher"), apply="truthy", overwrite=False
    ),
    _OverrideSpec(
        "nsx_module", ("build", "nsx_modules"), apply="truthy", coerce=_parse_nsx_modules
    ),
)

_KNOWN_PARAMS = frozenset(spec.param for spec in _OVERRIDE_SPECS)


def _build_cli_overrides(**params: Any) -> dict:
    """Build the config-overrides dict from ``hpx profile`` parameter values.

    Accepts any subset of the parameters named in ``_OVERRIDE_SPECS``;
    omitted parameters take each spec's default. Unknown names raise
    ``TypeError`` so a typo in the Typer signature fails loudly instead of
    silently dropping a flag.
    """
    unknown = set(params) - _KNOWN_PARAMS
    if unknown:
        raise TypeError(f"unknown profile CLI parameter(s): {sorted(unknown)}")

    cli: dict = {}
    for spec in _OVERRIDE_SPECS:
        value = params.get(spec.param, spec.default)
        if spec.apply == "not_none" and value is None:
            continue
        if spec.apply == "truthy" and not value:
            continue
        section = cli
        for key in spec.path[:-1]:
            section = section.setdefault(key, {})
        if not spec.overwrite and spec.path[-1] in section:
            continue
        if spec.const is not _UNSET:
            stored = spec.const
        elif spec.coerce is not None:
            stored = spec.coerce(value)
        else:
            stored = value
        section[spec.path[-1]] = stored
    return cli


def _cmd_profile(*, config: Path | None = None, verbose: int = 0, **params: Any) -> None:
    """Run the profiling pipeline."""
    from ..config import load_config
    from ..console import HpxConsole
    from ..errors import HpxError

    cli = _build_cli_overrides(verbose=verbose, **params)

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
