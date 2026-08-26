"""Implementation of the ``hpx validate`` command.

Drives the hardware-in-the-loop validation suite (MLPerf Tiny models) via
pytest, translating CLI axis flags (models/engines/boards/...) into a matrix
of :class:`~helia_profiler.validation.matrix.CaseSpec` cases.
"""

from __future__ import annotations

from pathlib import Path
import sys

from .common import _find_repo_root


_ENGINE_ALIASES = {
    "rt": "helia-rt",
    "aot": "helia-aot",
    "tflm": "tflm",
    "et": "executorch",
    "executorch": "executorch",
    "helia-rt": "helia-rt",
    "helia-aot": "helia-aot",
}

_EXECUTORCH_BACKEND_ALIASES = {
    "arm": "arm",
    "ns": "ns",
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
            gate, state, go = (int(value, 0) for value in values)
            mapping[board.strip()] = (gate, state, go)
        except ValueError:
            print(
                f"Error: invalid --power-gpios entry {item!r}; GPIO pins must be integers.",
                file=sys.stderr,
            )
            sys.exit(2)
    return mapping


def _normalise_engines(raw: str) -> str:
    """Translate short engine aliases to canonical names."""
    return _normalise_csv_aliases(
        raw,
        aliases=_ENGINE_ALIASES,
        label="engine",
        known="rt, aot, tflm, et, executorch, helia-rt, helia-aot",
    )


def _normalise_executorch_backends(raw: str) -> str:
    """Translate ExecuTorch CMSIS-NN provider selection to canonical names."""
    if (raw or "").strip() == "both":
        return "arm,ns"
    return _normalise_csv_aliases(
        raw,
        aliases=_EXECUTORCH_BACKEND_ALIASES,
        label="ExecuTorch backend",
        known="arm, ns, both",
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


def _cmd_validate(
    *,
    models: str = "",
    models_file: Path | None = None,
    model_paths: str = "",
    comparison_group: str = "custom",
    model_arena_size: int = 524288,
    engines: str = "",
    executorch_backends: str = "both",
    ns_cmsis_nn_ref: str = "",
    power: str = "off",
    power_boards: str = "",
    boards: str = "",
    toolchains: str = "",
    transports: str = "",
    memories: str = "",
    suite: str | None = None,
    jlink_serials: str = "",
    power_serials: str = "",
    power_gpios: str = "",
    repeat: int = 1,
    output_dir: Path = Path("results/validation"),
    timeout: float = 900.0,
    keyword: str = "",
    junit_xml: Path | None = None,
    list_: bool = False,
    verbose: int = 0,
) -> None:
    """Drive the hardware validation suite via pytest."""
    from ..validation import (
        BOARDS,
        MODELS,
        build_matrix,
        load_model_file,
        models_from_paths,
    )

    model_registry = dict(MODELS)
    custom_model_ids: list[str] = []
    try:
        if models_file is not None:
            file_models = load_model_file(Path(models_file))
            model_registry.update(file_models)
            custom_model_ids.extend(file_models)

        if model_paths.strip():
            path_models = models_from_paths(
                [Path(item.strip()) for item in model_paths.split(",") if item.strip()],
                arena_size=model_arena_size,
                comparison_group=comparison_group,
            )
            duplicates = sorted(set(path_models) & set(model_registry))
            if duplicates:
                raise ValueError(f"Duplicate custom model ID(s): {duplicates}")
            model_registry.update(path_models)
            custom_model_ids.extend(path_models)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    if custom_model_ids and not models.strip():
        models = ",".join(custom_model_ids)

    # Preset suites fill in defaults for any axis the user did not set.
    if suite == "smoke":
        if not models.strip():
            models = "kws"
        if not engines.strip():
            engines = "helia-rt"
        if not toolchains.strip():
            toolchains = "arm-none-eabi-gcc"
        if not transports.strip():
            transports = "rtt"
        if not memories.strip():
            memories = "auto"
    elif suite in {"models-rt", "models-aot", "complete"}:
        if not models.strip():
            models = "kws,vww,ic,ad"
        if not engines.strip():
            engines = {
                "models-rt": "helia-rt",
                "models-aot": "helia-aot",
                "complete": "helia-rt,helia-aot,tflm,executorch",
            }[suite]
        if not boards.strip():
            boards = "apollo510_evb,apollo330mP_evb"
        if not toolchains.strip():
            toolchains = "arm-none-eabi-gcc,atfe"
        if not transports.strip():
            transports = "rtt"
        if not memories.strip():
            memories = "auto"

    if not boards.strip():
        boards = "apollo510_evb"

    engines_csv = _normalise_engines(engines)
    executorch_backends_csv = _normalise_executorch_backends(executorch_backends)
    toolchains_csv = _normalise_toolchains(toolchains)
    transports_csv = _normalise_transports(transports)
    memories_csv = _normalise_memories(memories)
    jlink_serial_map = _parse_board_serials(jlink_serials, option="--jlink-serials")
    power_serial_map = _parse_board_serials(power_serials, option="--power-serials")
    power_gpio_pins = _parse_power_gpio_pins(power_gpios)

    # --list mode — preview the matrix, don't touch hardware.
    if list_:
        try:
            cases = build_matrix(
                models=[m.strip() for m in models.split(",") if m.strip()] or None,
                model_registry=model_registry,
                engines=[e.strip() for e in engines_csv.split(",") if e.strip()] or None,
                executorch_backends=[
                    backend.strip()
                    for backend in executorch_backends_csv.split(",")
                    if backend.strip()
                ]
                or None,
                power=power,
                power_boards=[b.strip() for b in power_boards.split(",") if b.strip()] or None,
                boards=[b.strip() for b in boards.split(",") if b.strip()] or None,
                toolchains=[t.strip() for t in toolchains_csv.split(",") if t.strip()] or None,
                transports=[t.strip() for t in transports_csv.split(",") if t.strip()] or None,
                memories=[m.strip() for m in memories_csv.split(",") if m.strip()] or None,
                jlink_serials=jlink_serial_map,
                power_serials=power_serial_map,
                power_gpio_pins=power_gpio_pins,
                repeat=repeat,
            )
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

        print(f"Registered models: {', '.join(sorted(model_registry))}")
        print(f"Registered boards: {', '.join(sorted(BOARDS))}")
        print(f"\n{len(cases)} case(s) would run:\n")
        for c in cases:
            power_flag = "power" if c.power else "     "
            engine = c.engine.value
            engine = f"{engine}/{c.cmsis_nn_provider.value}"
            print(
                f"  {c.case_id:<82}  {engine:<14}  "
                f"{c.toolchain.value:<18}  {c.transport.value:<7}  {c.memory.value:<5}  "
                f"{power_flag}"
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
        import pytest  # noqa: F401
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
        power,
        "--mlperf-output",
        str(output_dir.resolve()),
        "--mlperf-timeout",
        str(timeout),
    ]
    if power_boards.strip():
        pytest_args += ["--mlperf-power-boards", power_boards.strip()]
    if suite:
        pytest_args += ["--mlperf-suite", suite]
    if models.strip():
        pytest_args += ["--mlperf-models", models.strip()]
    if models_file is not None:
        pytest_args += ["--mlperf-models-file", str(Path(models_file).expanduser().resolve())]
    if model_paths.strip():
        pytest_args += ["--mlperf-model-paths", model_paths.strip()]
        pytest_args += [
            "--mlperf-comparison-group",
            comparison_group,
            "--mlperf-model-arena-size",
            str(model_arena_size),
        ]
    if engines_csv:
        pytest_args += ["--mlperf-engines", engines_csv]
    if executorch_backends_csv:
        pytest_args += ["--mlperf-executorch-backends", executorch_backends_csv]
    if ns_cmsis_nn_ref.strip():
        pytest_args += ["--mlperf-ns-cmsis-nn-ref", ns_cmsis_nn_ref.strip()]
    if boards.strip():
        pytest_args += ["--mlperf-boards", boards.strip()]
    if toolchains_csv:
        pytest_args += ["--mlperf-toolchains", toolchains_csv]
    if transports_csv:
        pytest_args += ["--mlperf-transports", transports_csv]
    if memories_csv:
        pytest_args += ["--mlperf-memories", memories_csv]
    if jlink_serials.strip():
        pytest_args += ["--mlperf-jlink-serials", jlink_serials.strip()]
    if power_serials.strip():
        pytest_args += ["--mlperf-power-serials", power_serials.strip()]
    if power_gpios.strip():
        pytest_args += ["--mlperf-power-gpios", power_gpios.strip()]
    pytest_args += ["--mlperf-repeat", str(repeat)]
    if keyword:
        pytest_args += ["-k", keyword]
    if junit_xml:
        pytest_args += [f"--junitxml={junit_xml.resolve()}"]
    if verbose:
        pytest_args.append("-" + "v" * verbose)
    else:
        pytest_args.append("-v")

    report_dir = output_dir.resolve()
    report_json = report_dir / "validation_report.json"
    report_before = report_json.stat().st_mtime_ns if report_json.exists() else None

    import pytest

    print(f"Running: pytest {' '.join(pytest_args)}\n")
    rc = pytest.main(pytest_args)

    report_md = report_dir / "validation_report.md"
    report_manifest = report_dir / "validation_manifest.json"
    report_after = report_json.stat().st_mtime_ns if report_json.exists() else None
    report_is_fresh = report_after is not None and report_after != report_before
    if report_is_fresh:
        from ..console import HpxConsole
        from ..errors import ReportError
        from ..validation.report import load_validation_report

        console = HpxConsole(verbosity=verbose)
        try:
            report = load_validation_report(report_json)
        except ReportError as exc:
            console.print_error(exc)
            rc = int(rc) or 1
        else:
            output_paths = [
                path for path in (report_json, report_md, report_manifest) if path.exists()
            ]
            console.print_validation(report, output_paths=output_paths)
    else:
        if report_md.exists():
            print(f"\nMarkdown report: {report_md}")
        if report_json.exists():
            print(f"JSON report:     {report_json}")
    sys.exit(int(rc))
