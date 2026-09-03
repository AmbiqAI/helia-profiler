"""Translate ``hpx validate`` axis flags into a concrete validation plan.

This module owns the *policy* behind the ``hpx validate`` command: alias
normalisation, suite presets, custom-model registry assembly, matrix
expansion, and the pytest argument list. The CLI layer
(:mod:`helia_profiler.cli.validate_cmd`) only renders output, maps
:class:`ValueError` to exit codes, and invokes pytest.

All validation failures raise :class:`ValueError` with a user-facing message
(no ``Error:`` prefix — the CLI adds it).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .matrix import CaseSpec, ModelSpec, build_matrix, load_model_file, models_from_paths

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
            raise ValueError(f"unknown {label} '{token}'. Known: {known}.")
        out.append(aliases[token])
    return ",".join(out)


def normalise_engines(raw: str) -> str:
    """Translate short engine aliases to canonical names."""
    return _normalise_csv_aliases(
        raw,
        aliases=_ENGINE_ALIASES,
        label="engine",
        known="rt, aot, tflm, et, executorch, helia-rt, helia-aot",
    )


def normalise_executorch_backends(raw: str) -> str:
    """Translate ExecuTorch CMSIS-NN provider selection to canonical names."""
    if (raw or "").strip() == "both":
        return "arm,ns"
    return _normalise_csv_aliases(
        raw,
        aliases=_EXECUTORCH_BACKEND_ALIASES,
        label="ExecuTorch backend",
        known="arm, ns, both",
    )


def normalise_toolchains(raw: str) -> str:
    """Translate toolchain aliases (gcc, acfe) to config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TOOLCHAIN_ALIASES,
        label="toolchain",
        known="gcc, arm-none-eabi-gcc, armclang/acfe, atfe",
    )


def normalise_transports(raw: str) -> str:
    """Translate interface aliases (usb) to transport config values."""
    return _normalise_csv_aliases(
        raw,
        aliases=_TRANSPORT_ALIASES,
        label="interface",
        known="rtt, uart, swo, usb_cdc",
    )


def normalise_memories(raw: str) -> str:
    """Translate memory aliases to model placement presets."""
    return _normalise_csv_aliases(
        raw,
        aliases=_MEMORY_ALIASES,
        label="memory",
        known="auto, tcm, sram, mram, psram",
    )


def parse_board_serials(raw: str, *, option: str) -> dict[str, str] | None:
    """Parse a ``board=serial,...`` mapping flag."""
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, serial = item.partition("=")
        if not sep or not board.strip() or not serial.strip():
            raise ValueError(f"invalid {option} entry {item!r}; expected board=serial.")
        mapping[board.strip()] = serial.strip()
    return mapping


def parse_power_gpio_pins(raw: str) -> dict[str, tuple[int, int, int]] | None:
    """Parse a ``board=gate:state:go,...`` GPIO mapping flag."""
    raw = (raw or "").strip()
    if not raw:
        return None
    mapping: dict[str, tuple[int, int, int]] = {}
    for item in [p.strip() for p in raw.split(",") if p.strip()]:
        board, sep, pins_raw = item.partition("=")
        values = [value.strip() for value in pins_raw.split(":")]
        if not sep or not board.strip() or len(values) != 3:
            raise ValueError(f"invalid --power-gpios entry {item!r}; expected board=gate:state:go.")
        try:
            gate, state, go = (int(value, 0) for value in values)
            mapping[board.strip()] = (gate, state, go)
        except ValueError:
            raise ValueError(
                f"invalid --power-gpios entry {item!r}; GPIO pins must be integers."
            ) from None
    return mapping


def assemble_model_registry(
    *,
    models_file: Path | None,
    model_paths: str,
    model_arena_size: int,
    comparison_group: str,
) -> tuple[dict[str, ModelSpec], list[str]]:
    """Build the model registry: canonical MODELS plus any custom entries.

    Returns the registry and the list of custom model IDs added to it.
    """
    from .matrix import MODELS

    model_registry: dict[str, ModelSpec] = dict(MODELS)
    custom_model_ids: list[str] = []
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
    return model_registry, custom_model_ids


@dataclass(frozen=True)
class ValidationPlan:
    """Fully resolved inputs for one ``hpx validate`` invocation.

    Axis fields hold canonical comma-separated values (post alias translation
    and suite presets); the ``*_raw`` fields keep the user's original strings
    for pytest passthrough.
    """

    models_csv: str
    engines_csv: str
    executorch_backends_csv: str
    boards_csv: str
    toolchains_csv: str
    transports_csv: str
    memories_csv: str
    power: str
    power_boards_csv: str
    suite: str | None
    repeat: int
    ns_cmsis_nn_ref: str
    models_file: Path | None
    model_paths_raw: str
    comparison_group: str
    model_arena_size: int
    jlink_serials_raw: str
    power_serials_raw: str
    power_gpios_raw: str
    jlink_serials: dict[str, str] | None
    power_serials: dict[str, str] | None
    power_gpio_pins: dict[str, tuple[int, int, int]] | None
    model_registry: dict[str, ModelSpec]

    def cases(self) -> list[CaseSpec]:
        """Expand the plan into the concrete case matrix.

        Raises :class:`ValueError` for unknown models/boards/etc.
        """

        def _split(csv: str) -> list[str] | None:
            return [item.strip() for item in csv.split(",") if item.strip()] or None

        return build_matrix(
            models=_split(self.models_csv),
            model_registry=self.model_registry,
            engines=_split(self.engines_csv),
            executorch_backends=_split(self.executorch_backends_csv),
            power=self.power,
            power_boards=_split(self.power_boards_csv),
            boards=_split(self.boards_csv),
            toolchains=_split(self.toolchains_csv),
            transports=_split(self.transports_csv),
            memories=_split(self.memories_csv),
            jlink_serials=self.jlink_serials,
            power_serials=self.power_serials,
            power_gpio_pins=self.power_gpio_pins,
            repeat=self.repeat,
        )

    def pytest_args(
        self,
        *,
        tests_dir: Path,
        output_dir: Path,
        timeout: float,
        keyword: str = "",
        junit_xml: Path | None = None,
        verbose: int = 0,
    ) -> list[str]:
        """Build the pytest argument list that runs this plan."""
        args: list[str] = [
            str(tests_dir),
            "-m",
            "hardware",
            "--mlperf-power",
            self.power,
            "--mlperf-output",
            str(output_dir.resolve()),
            "--mlperf-timeout",
            str(timeout),
        ]
        if self.power_boards_csv.strip():
            args += ["--mlperf-power-boards", self.power_boards_csv.strip()]
        if self.suite:
            args += ["--mlperf-suite", self.suite]
        if self.models_csv.strip():
            args += ["--mlperf-models", self.models_csv.strip()]
        if self.models_file is not None:
            args += [
                "--mlperf-models-file",
                str(Path(self.models_file).expanduser().resolve()),
            ]
        if self.model_paths_raw.strip():
            args += ["--mlperf-model-paths", self.model_paths_raw.strip()]
            args += [
                "--mlperf-comparison-group",
                self.comparison_group,
                "--mlperf-model-arena-size",
                str(self.model_arena_size),
            ]
        if self.engines_csv:
            args += ["--mlperf-engines", self.engines_csv]
        if self.executorch_backends_csv:
            args += ["--mlperf-executorch-backends", self.executorch_backends_csv]
        if self.ns_cmsis_nn_ref.strip():
            args += ["--mlperf-ns-cmsis-nn-ref", self.ns_cmsis_nn_ref.strip()]
        if self.boards_csv.strip():
            args += ["--mlperf-boards", self.boards_csv.strip()]
        if self.toolchains_csv:
            args += ["--mlperf-toolchains", self.toolchains_csv]
        if self.transports_csv:
            args += ["--mlperf-transports", self.transports_csv]
        if self.memories_csv:
            args += ["--mlperf-memories", self.memories_csv]
        if self.jlink_serials_raw.strip():
            args += ["--mlperf-jlink-serials", self.jlink_serials_raw.strip()]
        if self.power_serials_raw.strip():
            args += ["--mlperf-power-serials", self.power_serials_raw.strip()]
        if self.power_gpios_raw.strip():
            args += ["--mlperf-power-gpios", self.power_gpios_raw.strip()]
        args += ["--mlperf-repeat", str(self.repeat)]
        if keyword:
            args += ["-k", keyword]
        if junit_xml:
            args += [f"--junitxml={junit_xml.resolve()}"]
        if verbose:
            args.append("-" + "v" * verbose)
        else:
            args.append("-v")
        return args


def _apply_suite_defaults(plan: ValidationPlan) -> ValidationPlan:
    """Fill in preset-suite defaults for any axis the user did not set."""
    updates: dict[str, str] = {}
    if plan.suite == "smoke":
        if not plan.models_csv.strip():
            updates["models_csv"] = "kws"
        if not plan.engines_csv.strip():
            updates["engines_csv"] = "helia-rt"
        if not plan.toolchains_csv.strip():
            updates["toolchains_csv"] = "arm-none-eabi-gcc"
        if not plan.transports_csv.strip():
            updates["transports_csv"] = "rtt"
        if not plan.memories_csv.strip():
            updates["memories_csv"] = "auto"
    elif plan.suite in {"models-rt", "models-aot", "complete"}:
        if not plan.models_csv.strip():
            updates["models_csv"] = "kws,vww,ic,ad"
        if not plan.engines_csv.strip():
            updates["engines_csv"] = {
                "models-rt": "helia-rt",
                "models-aot": "helia-aot",
                "complete": "helia-rt,helia-aot,tflm,executorch",
            }[plan.suite]
        if not plan.boards_csv.strip():
            updates["boards_csv"] = "apollo510_evb,apollo330mP_evb"
        if not plan.toolchains_csv.strip():
            updates["toolchains_csv"] = "arm-none-eabi-gcc,atfe"
        if not plan.transports_csv.strip():
            updates["transports_csv"] = "rtt"
        if not plan.memories_csv.strip():
            updates["memories_csv"] = "auto"
    return replace(plan, **updates) if updates else plan


def resolve_plan(
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
) -> ValidationPlan:
    """Resolve raw ``hpx validate`` flag values into a :class:`ValidationPlan`.

    Raises :class:`ValueError` for any invalid flag value.
    """
    model_registry, custom_model_ids = assemble_model_registry(
        models_file=models_file,
        model_paths=model_paths,
        model_arena_size=model_arena_size,
        comparison_group=comparison_group,
    )

    if custom_model_ids and not models.strip():
        models = ",".join(custom_model_ids)

    plan = ValidationPlan(
        models_csv=models,
        engines_csv=engines,
        executorch_backends_csv=executorch_backends,
        boards_csv=boards,
        toolchains_csv=toolchains,
        transports_csv=transports,
        memories_csv=memories,
        power=power,
        power_boards_csv=power_boards,
        suite=suite,
        repeat=repeat,
        ns_cmsis_nn_ref=ns_cmsis_nn_ref,
        models_file=models_file,
        model_paths_raw=model_paths,
        comparison_group=comparison_group,
        model_arena_size=model_arena_size,
        jlink_serials_raw=jlink_serials,
        power_serials_raw=power_serials,
        power_gpios_raw=power_gpios,
        jlink_serials=parse_board_serials(jlink_serials, option="--jlink-serials"),
        power_serials=parse_board_serials(power_serials, option="--power-serials"),
        power_gpio_pins=parse_power_gpio_pins(power_gpios),
        model_registry=model_registry,
    )
    plan = _apply_suite_defaults(plan)
    if not plan.boards_csv.strip():
        plan = replace(plan, boards_csv="apollo510_evb")
    return replace(
        plan,
        engines_csv=normalise_engines(plan.engines_csv),
        executorch_backends_csv=normalise_executorch_backends(plan.executorch_backends_csv),
        toolchains_csv=normalise_toolchains(plan.toolchains_csv),
        transports_csv=normalise_transports(plan.transports_csv),
        memories_csv=normalise_memories(plan.memories_csv),
    )
