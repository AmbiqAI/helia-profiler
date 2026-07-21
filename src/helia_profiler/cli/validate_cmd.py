"""Implementation of the ``hpx validate`` command.

Drives the hardware-in-the-loop validation suite (MLPerf Tiny models) via
pytest, translating CLI axis flags (models/engines/boards/...) into a matrix
of :class:`~helia_profiler.validation.matrix.CaseSpec` cases.
"""

from __future__ import annotations

import argparse
import sys

from .common import _find_repo_root


_ENGINE_ALIASES = {
    "rt": "helia-rt",
    "aot": "helia-aot",
    "helia-rt": "helia-rt",
    "helia-aot": "helia-aot",
}

_TOOLCHAIN_ALIASES = {
    "gcc": "arm-none-eabi-gcc",
    "arm-none-eabi-gcc": "arm-none-eabi-gcc",
    "armclang": "armclang",
    "acfe": "armclang",
    "atfe": "atfe",
}

_TRANSPORT_ALIASES = {
    "rtt": "rtt",
    "uart": "uart",
    "swo": "swo",
    "usb": "usb_cdc",
    "usb_cdc": "usb_cdc",
}

_MEMORY_ALIASES = {
    "auto": "auto",
    "tcm": "tcm",
    "sram": "sram",
    "mram": "mram",
    "psram": "psram",
}


def _parse_board_serials(raw: str, *, option: str) -> dict[str, str] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, serial = item.partition("=")
        if not sep or not board.strip() or not serial.strip():
            print(
                f"Error: invalid {option} entry {item!r}; expected board=serial.",
                file=sys.stderr,
            )
            sys.exit(2)
        mapping[board.strip()] = serial.strip()
    return mapping


def _parse_power_gpio_pins(raw: str) -> dict[str, tuple[int, int, int]] | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, tuple[int, int, int]] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, pins_raw = item.partition("=")
        values = [value.strip() for value in pins_raw.split(":")]
        if not sep or not board.strip() or len(values) != 3:
            print(
                f"Error: invalid --power-gpios entry {item!r}; expected board=gate:state:go.",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            mapping[board.strip()] = tuple(int(value, 0) for value in values)  # type: ignore[assignment]
        except ValueError:
            print(
                f"Error: invalid --power-gpios entry {item!r}; GPIO pins must be integers.",
                file=sys.stderr,
            )
            sys.exit(2)
    return mapping


def _normalise_engines(raw: str) -> str:
    """Translate short engine aliases (rt, aot) to canonical names."""
    return _normalise_csv_aliases(
        raw,
        aliases=_ENGINE_ALIASES,
        label="engine",
        known="rt, aot, helia-rt, helia-aot",
    )


def _normalise_toolchains(raw: str) -> str:
    """Translate toolchain aliases (gcc, acfe) to config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TOOLCHAIN_ALIASES,
        label="toolchain",
        known="gcc, arm-none-eabi-gcc, armclang/acfe, atfe",
    )


def _normalise_transports(raw: str) -> str:
    """Translate interface aliases (usb) to transport config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TRANSPORT_ALIASES,
        label="interface",
        known="rtt, uart, swo, usb_cdc",
    )


def _normalise_memories(raw: str) -> str:
    """Translate memory aliases to model placement presets."""
    return _normalise_csv_aliases(
        raw,
        aliases=_MEMORY_ALIASES,
        label="memory",
        known="auto, tcm, sram, mram, psram",
    )


def _normalise_csv_aliases(
    raw: str,
    *,
    aliases: dict[str, str],
    label: str,
    known: str,
) -> str:
    if not raw.strip():
        return ""
    out: list[str] = []
    for token in [t.strip() for t in raw.split(",") if t.strip()]:
        if token not in aliases:
            print(
                f"Error: unknown {label} '{token}'. Known: {known}.",
                file=sys.stderr,
            )
            sys.exit(2)
        out.append(aliases[token])
    return ",".join(out)


def _cmd_validate(args: argparse.Namespace) -> None:
    """Drive the hardware validation suite via pytest."""
    from ..validation import MODELS, BOARDS, build_matrix

    # Preset suites fill in defaults for any axis the user did not set.
    suite = getattr(args, "suite", None)
    if suite == "smoke":
        if not args.models.strip():
            args.models = "kws"
        if not args.engines.strip():
            args.engines = "helia-rt"
        if not args.toolchains.strip():
            args.toolchains = "arm-none-eabi-gcc"
        if not args.transports.strip():
            args.transports = "rtt"
        if not args.memories.strip():
            args.memories = "auto"
    elif suite in {"models-rt", "models-aot", "complete"}:
        if not args.models.strip():
            args.models = "kws,vww,ic,ad"
        if not args.engines.strip():
            args.engines = {
                "models-rt": "helia-rt",
                "models-aot": "helia-aot",
                "complete": "helia-rt,helia-aot",
            }[suite]
        if not args.boards.strip():
            args.boards = "apollo510_evb,apollo330mP_evb"
        if not args.toolchains.strip():
            args.toolchains = "arm-none-eabi-gcc,atfe"
        if not args.transports.strip():
            args.transports = "rtt"
        if not args.memories.strip():
            args.memories = "auto"

    if not args.boards.strip():
        args.boards = "apollo510_evb"

    engines_csv = _normalise_engines(args.engines)
    toolchains_csv = _normalise_toolchains(args.toolchains)
    transports_csv = _normalise_transports(args.transports)
    memories_csv = _normalise_memories(args.memories)
    jlink_serials = _parse_board_serials(args.jlink_serials, option="--jlink-serials")
    power_serials = _parse_board_serials(args.power_serials, option="--power-serials")
    power_gpio_pins = _parse_power_gpio_pins(args.power_gpios)

    # --list mode — preview the matrix, don't touch hardware.
    if args.list:
        try:
            cases = build_matrix(
                models=[m.strip() for m in args.models.split(",") if m.strip()] or None,
                engines=[e.strip() for e in engines_csv.split(",") if e.strip()] or None,
                power=args.power,
                boards=[b.strip() for b in args.boards.split(",") if b.strip()] or None,
                toolchains=[t.strip() for t in toolchains_csv.split(",") if t.strip()] or None,
                transports=[t.strip() for t in transports_csv.split(",") if t.strip()] or None,
                memories=[m.strip() for m in memories_csv.split(",") if m.strip()] or None,
                jlink_serials=jlink_serials,
                power_serials=power_serials,
                power_gpio_pins=power_gpio_pins,
                repeat=args.repeat,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

        print(f"Registered models: {', '.join(sorted(MODELS))}")
        print(f"Registered boards: {', '.join(sorted(BOARDS))}")
        print(f"\n{len(cases)} case(s) would run:\n")
        for c in cases:
            power = "power" if c.power else "     "
            print(
                f"  {c.case_id:<82}  {c.engine:<10}  "
                f"{c.toolchain.value:<18}  {c.transport.value:<7}  {c.memory.value:<5}  {power}"
            )
        return

    # Locate the validation test directory inside the installed package /
    # repo checkout.  We support both the editable/repo layout
    # (``helia-profiler/tests/validation``) and any future packaged layout.
    repo_root = _find_repo_root()
    tests_dir = repo_root / "tests" / "validation"
    if not tests_dir.exists():
        print(
            f"Error: validation tests not found at {tests_dir}.\n"
            "  `hpx validate` must be run from a heliaPROFILER checkout.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import pytest  # noqa: F401  (imported to fail fast with a clear msg)
    except ImportError:
        print(
            "Error: pytest is required for `hpx validate`. Install it with `pip install pytest`.",
            file=sys.stderr,
        )
        sys.exit(2)

    pytest_args: list[str] = [
        str(tests_dir),
        "-m",
        "hardware",
        "--mlperf-power",
        args.power,
        "--mlperf-output",
        str(args.output_dir.resolve()),
        "--mlperf-timeout",
        str(args.timeout),
    ]
    if suite:
        pytest_args += ["--mlperf-suite", suite]
    if args.models.strip():
        pytest_args += ["--mlperf-models", args.models.strip()]
    if engines_csv:
        pytest_args += ["--mlperf-engines", engines_csv]
    if args.boards.strip():
        pytest_args += ["--mlperf-boards", args.boards.strip()]
    if toolchains_csv:
        pytest_args += ["--mlperf-toolchains", toolchains_csv]
    if transports_csv:
        pytest_args += ["--mlperf-transports", transports_csv]
    if memories_csv:
        pytest_args += ["--mlperf-memories", memories_csv]
    if args.jlink_serials.strip():
        pytest_args += ["--mlperf-jlink-serials", args.jlink_serials.strip()]
    if args.power_serials.strip():
        pytest_args += ["--mlperf-power-serials", args.power_serials.strip()]
    if args.power_gpios.strip():
        pytest_args += ["--mlperf-power-gpios", args.power_gpios.strip()]
    pytest_args += ["--mlperf-repeat", str(args.repeat)]
    if args.keyword:
        pytest_args += ["-k", args.keyword]
    if args.junit_xml:
        pytest_args += [f"--junitxml={args.junit_xml.resolve()}"]
    if args.verbose:
        pytest_args.append("-" + "v" * args.verbose)
    else:
        pytest_args.append("-v")

    import pytest

    print(f"Running: pytest {' '.join(pytest_args)}\n")
    rc = pytest.main(pytest_args)

    report_md = args.output_dir.resolve() / "validation_report.md"
    report_json = args.output_dir.resolve() / "validation_report.json"
    if report_md.exists():
        print(f"\nMarkdown report: {report_md}")
    if report_json.exists():
        print(f"JSON report:     {report_json}")
    sys.exit(int(rc))
