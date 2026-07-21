"""Stage 0 — Preflight: fail fast on the common, preventable problems.

This stage runs before any platform resolution, code generation or build so
that users get an immediate, actionable error when something trivial is
wrong — instead of waiting for a confusing failure several stages in.

Checks performed (in order):

1. **Model file** — exists, is a regular file, non-empty, has a ``.tflite``
   extension, starts with the TFLite ``TFL3`` magic string.
2. **Arena size** — if specified, is positive.
3. **Model placement** — optional arena/weights overrides use supported regions.
4. **Output directory** — can be created + written to.
5. **Host toolchain** — ``nsx``, ``cmake``, ``ninja``, the selected compiler,
   and ``SEGGER commander`` are available. ATfE is located via ``ATFE_ROOT``.
6. **Transport-specific tools** — e.g. ``pylink`` when ``transport=swo``;
    the Python ``pyocd`` module isn't required because heliaPROFILER uses
    J-Link directly.

All failures raise :class:`ConfigError` with a hint explaining how to fix
it.  The stage never touches hardware — that's reserved for later stages —
so running preflight on a laptop without a board attached is safe.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Transport
from ..counters import (
    supported_groups_for_domains,
    validate_group_selection,
)
from ..engines import get_adapter
from ..errors import ConfigError
from ..pipeline import PipelineContext
from ..placement import Placement
from ..platform import get_soc_for_board

log = logging.getLogger("hpx")


# TFLite flatbuffers start with a 4-byte file identifier.  Some flatc
# versions emit the identifier at offset 4 (after the root-table offset),
# so we accept either placement.
_TFLITE_MAGIC = b"TFL3"
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
        _check_rtt_buffer_size(cfg.target.rtt_buffer_size_up)
        _check_runtime_split_locations(cfg)
        _check_pmu_selection(cfg)
        _check_transport_support(cfg)
        _check_output_dir(cfg.output.dir)
        _check_host_tools(cfg)
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


def _check_explicit_location(loc: str | None, *, name: str, valid: tuple[Placement, ...]) -> None:
    if loc is None:
        return
    if loc not in valid:
        raise ConfigError(
            f"Invalid {name}: '{loc}'.",
            hint=f"Expected one of: {', '.join(valid)}.",
        )


def _check_rtt_buffer_size(size: int | None) -> None:
    if size is None:
        return
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ConfigError(
            f"target.rtt_buffer_size_up must be a positive integer (got {size!r}).",
            hint="Set target.rtt_buffer_size_up to a positive byte count, or leave it unset to use the toolchain-aware default.",
        )


def _check_runtime_split_locations(cfg) -> None:
    runtime_arena = cfg.model.arena_location
    runtime_weights = cfg.model.weights_location

    if runtime_weights == Placement.PSRAM and cfg.target.transport != Transport.RTT:
        raise ConfigError(
            "PSRAM model weights require target.transport='rtt'.",
            hint=(
                "Host-side PSRAM model upload currently uses the RTT transport. "
                "Use --transport rtt, or keep weights in MRAM/SRAM."
            ),
        )

    _check_explicit_location(
        runtime_arena,
        name="model.arena_location",
        valid=_VALID_RUNTIME_ARENA_LOCATIONS,
    )
    _check_explicit_location(
        runtime_weights,
        name="model.weights_location",
        valid=_VALID_RUNTIME_WEIGHTS_LOCATIONS,
    )


def _check_pmu_selection(cfg) -> None:
    try:
        soc = get_soc_for_board(cfg.target.board, registry=cfg.platform_registry)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    supported_groups = supported_groups_for_domains(soc.profiling_domains)
    try:
        validate_group_selection(
            cfg.profiling.pmu_counters,
            supported_groups=supported_groups,
        )
    except ValueError as exc:
        raise ConfigError(
            str(exc),
            hint=(
                f"Board '{cfg.target.board}' exposes profiling groups: "
                f"{', '.join(supported_groups) if supported_groups else 'none'}."
            ),
        ) from exc


def _check_transport_support(cfg) -> None:
    if cfg.target.transport != Transport.USB_CDC:
        return
    try:
        soc = get_soc_for_board(cfg.target.board, registry=cfg.platform_registry)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    if not soc.has_usb:
        raise ConfigError(
            f"Board '{cfg.target.board}' ({soc.name}) has no USB device support.",
            hint=(
                "Apollo3/3P has no compatible nsx-ambiq-usb module — use "
                "transport=uart, swo, or rtt instead."
            ),
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


def _check_host_tools(cfg) -> None:
    from ..doctor import inspect_environment

    result = inspect_environment(
        toolchain=cfg.target.toolchain,
        transport=cfg.target.transport,
        engine=cfg.engine.type,
    )
    if result.ok:
        return
    missing = "\n".join(
        f"  - {check.name}: {check.hint or 'Install this dependency.'}"
        for check in result.missing_required
    )
    raise ConfigError(
        "Required host dependencies are missing.",
        hint=f"Install the following and re-run:\n{missing}\nRun 'hpx doctor' for details.",
    )
