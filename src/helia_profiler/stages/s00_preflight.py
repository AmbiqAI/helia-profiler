"""Stage 0 — Preflight: fail fast on the common, preventable problems.

This stage runs before any platform resolution, code generation or build so
that users get an immediate, actionable error when something trivial is
wrong — instead of waiting for a confusing failure several stages in.

Checks performed (in order):

1. **Model file** — exists, is a regular file, non-empty, has a ``.tflite``
   extension, starts with the TFLite ``TFL3`` magic string.
2. **Arena size** — if specified, is positive.
3. **Model placement** — ``model_location`` is valid and any runtime-scoped
    split overrides use supported regions for the selected engine.
4. **Output directory** — can be created + written to.
5. **Host toolchain** — ``nsx``, ``cmake``, ``ninja``, the selected compiler,
   and ``JLinkExe`` are available. ATfE is located via ``ATFE_ROOT``.
6. **Transport-specific tools** — e.g. ``JLinkSWOViewerCL`` when
   ``transport=swo``; the Python ``pyocd`` module isn't required because
   heliaPROFILER uses J-Link directly.

All failures raise :class:`ConfigError` with a hint explaining how to fix
it.  The stage never touches hardware — that's reserved for later stages —
so running preflight on a laptop without a board attached is safe.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..engines import get_adapter
from ..errors import ConfigError
from ..pipeline import PipelineContext
from ..placement import ModelLocation, Placement

log = logging.getLogger("hpx")


# TFLite flatbuffers start with a 4-byte file identifier.  Some flatc
# versions emit the identifier at offset 4 (after the root-table offset),
# so we accept either placement.
_TFLITE_MAGIC = b"TFL3"
_VALID_MODEL_LOCATIONS: tuple[ModelLocation, ...] = tuple(ModelLocation)
_VALID_RUNTIME_ARENA_LOCATIONS: tuple[Placement, ...] = (
    Placement.TCM,
    Placement.SRAM,
    Placement.PSRAM,
)
_VALID_RUNTIME_WEIGHTS_LOCATIONS: tuple[Placement, ...] = tuple(Placement)


class PreflightStage:
    @property
    def name(self) -> str:
        return "preflight"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        cfg = ctx.config
        _check_model(cfg.model.path)
        _check_arena_size(cfg.model.arena_size)
        _check_model_location(cfg.model.model_location)
        _check_runtime_split_locations(cfg)
        _check_output_dir(cfg.output.dir)
        _check_host_tools(cfg.target.transport, cfg.target.toolchain)
        log.info("Preflight checks passed.")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_model(path: Path) -> None:
    if not path.exists():
        raise ConfigError(
            f"Model file not found: {path}",
            hint="Check the path in model.path (CLI --model / YAML).",
        )
    if not path.is_file():
        raise ConfigError(
            f"Model path is not a regular file: {path}",
            hint="model.path must point to a .tflite flatbuffer, not a directory.",
        )
    size = path.stat().st_size
    if size == 0:
        raise ConfigError(
            f"Model file is empty: {path}",
            hint="The file exists but has zero bytes — re-export your model.",
        )
    if path.suffix.lower() != ".tflite":
        raise ConfigError(
            f"Model file does not have a .tflite extension: {path.name}",
            hint="heliaPROFILER expects a TFLite flatbuffer (.tflite).",
        )
    # TFLite flatbuffer sanity: 'TFL3' magic should appear in the first 16
    # bytes.  Anything else is either truncated, a different format, or a
    # Python pickle masquerading as a model.
    try:
        head = path.read_bytes()[:16]
    except OSError as exc:
        raise ConfigError(
            f"Cannot read model file: {path} ({exc})",
            hint="Check file permissions.",
        ) from exc
    if _TFLITE_MAGIC not in head:
        raise ConfigError(
            f"Model file does not look like a TFLite flatbuffer: {path}",
            hint=(
                "Expected the 'TFL3' magic marker within the first 16 bytes. "
                "Make sure the file was exported with the TFLite converter."
            ),
        )


def _check_arena_size(arena_size: int | None) -> None:
    if arena_size is None:
        return
    if arena_size <= 0:
        raise ConfigError(
            f"model.arena_size must be positive (got {arena_size}).",
            hint="Leave arena_size unset to let the engine choose, or set a positive byte count.",
        )


def _check_model_location(loc: str) -> None:
    if loc not in _VALID_MODEL_LOCATIONS:
        raise ConfigError(
            f"Invalid model.model_location: '{loc}'.",
            hint=f"Expected one of: {', '.join(_VALID_MODEL_LOCATIONS)}.",
        )


def _check_explicit_location(loc: str | None, *, name: str, valid: tuple[Placement, ...]) -> None:
    if loc is None:
        return
    if loc not in valid:
        raise ConfigError(
            f"Invalid {name}: '{loc}'.",
            hint=f"Expected one of: {', '.join(valid)}.",
        )


def _check_runtime_split_locations(cfg) -> None:
    runtime_arena = cfg.engine.config.get("runtime_arena_location")
    runtime_weights = cfg.engine.config.get("runtime_weights_location")
    weights_in_psram = (
        cfg.model.model_location == Placement.PSRAM
        or runtime_weights == Placement.PSRAM
    )

    if weights_in_psram and cfg.target.transport != "rtt":
        raise ConfigError(
            "PSRAM model weights require target.transport='rtt'.",
            hint=(
                "Host-side PSRAM model upload currently uses the RTT transport. "
                "Use --transport rtt, or keep weights in MRAM/SRAM."
            ),
        )

    adapter = get_adapter(cfg.engine.type)
    if not adapter.supports_runtime_split():
        # Engine bakes placement into its compiled module; the
        # profiler-config split overrides cannot influence weights.
        if runtime_weights is not None:
            raise ConfigError(
                f"engine.config.runtime_weights_location is not supported for engine.type='{cfg.engine.type.value}'.",
                hint=(
                    f"{adapter.name} controls weights placement via its own "
                    "compiler args (e.g. engine.config.aot_args)."
                ),
            )
        _check_explicit_location(
            runtime_arena,
            name="engine.config.runtime_arena_location",
            valid=_VALID_RUNTIME_ARENA_LOCATIONS,
        )
        return

    _check_explicit_location(
        runtime_arena,
        name="engine.config.runtime_arena_location",
        valid=_VALID_RUNTIME_ARENA_LOCATIONS,
    )
    _check_explicit_location(
        runtime_weights,
        name="engine.config.runtime_weights_location",
        valid=_VALID_RUNTIME_WEIGHTS_LOCATIONS,
    )


def _check_output_dir(out_dir: Path) -> None:
    resolved = out_dir.expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Cannot create output directory: {resolved} ({exc})",
            hint="Check output.dir — the parent must be writable.",
        ) from exc
    # Write probe — catches mounted-read-only or permissions issues that
    # mkdir() alone won't flag.
    probe = resolved / ".hpx_write_probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise ConfigError(
            f"Output directory is not writable: {resolved} ({exc})",
            hint="Point output.dir to a writable location.",
        ) from exc


def _check_host_tools(transport: str, toolchain: str) -> None:
    required: list[tuple[str, str]] = [
        ("nsx", "neuralspotx CLI (pip install neuralspotx)"),
        ("cmake", "CMake >= 3.24 (brew install cmake / apt install cmake)"),
        ("ninja", "Ninja build system (brew install ninja / apt install ninja-build)"),
    ]

    # Toolchain-specific compiler binary check.
    if toolchain == "armclang":
        required.append(
            (toolchain, f"ARM Compiler ({toolchain}) (https://developer.arm.com/tools-and-software/embedded/arm-compiler)"),
        )
    elif toolchain == "atfe":
        _check_atfe_tools()
    else:
        gcc_cmd = toolchain if "gcc" in toolchain else f"{toolchain}-gcc"
        required.append(
            (gcc_cmd, "ARM GCC toolchain (https://developer.arm.com/downloads/-/gnu-rm)"),
        )

    # Transport-specific.
    if transport in ("rtt", "swo", "usb_cdc"):
        required.append(
            ("JLinkExe", "SEGGER J-Link commander (https://www.segger.com/downloads/jlink/)"),
        )

    missing: list[str] = []
    for binary, hint in required:
        if shutil.which(binary) is None:
            missing.append(f"  - {binary}: {hint}")

    if missing:
        joined = "\n".join(missing)
        raise ConfigError(
            "Required host tools are missing from PATH.",
            hint=(
                "Install the following and re-run:\n"
                f"{joined}\n"
                "Run 'hpx doctor' for a full diagnostic."
            ),
        )


def _check_atfe_tools() -> None:
    root = os.environ.get("ATFE_ROOT")
    if not root:
        raise ConfigError(
            "ATfE toolchain requested, but ATFE_ROOT is not set.",
            hint=(
                "Set ATFE_ROOT to the Arm Toolchain for Embedded install "
                "directory, e.g. export ATFE_ROOT=/Applications/ATFEToolchain/ATfE-22.1.0."
            ),
        )

    bin_dir = Path(root).expanduser() / "bin"
    required = ("clang", "clang++", "llvm-ar", "llvm-objcopy", "llvm-size")
    missing = [name for name in required if not (bin_dir / name).exists()]
    if missing:
        raise ConfigError(
            f"ATfE toolchain incomplete under ATFE_ROOT: {bin_dir}",
            hint=(
                "Expected these executables: "
                f"{', '.join(required)}. Missing: {', '.join(missing)}. "
                "Install ATfE with the newlib overlay or correct ATFE_ROOT."
            ),
        )
