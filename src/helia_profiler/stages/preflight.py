"""Stage 0 — Preflight: fail fast on the common, preventable problems.

This stage runs before any platform resolution, code generation or build so
that users get an immediate, actionable error when something trivial is
wrong — instead of waiting for a confusing failure several stages in.

Checks performed (in order):

1. **Model file** — exists, is a regular file, non-empty, and matches the
   selected engine: TFLite ``.tflite``/``TFL3`` or ExecuTorch ``.pte``/``ET``.
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

from ..config import CleanWindowProbe, Transport
from ..counters import (
    supported_groups_for_domains,
    validate_group_selection,
)
from ..engines import EngineType, get_adapter
from ..engines.base import PsramWeightsSource
from ..errors import ConfigError
from ..evaluation.softmax_preflight import aot_softmax_verdict, scan_softmax_scaling
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
        _check_model(cfg.model.path, cfg.engine.type)
        _check_softmax_scaling(cfg.model.path, cfg.engine.type)
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


def _check_model(path: Path, engine: EngineType) -> None:
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
    expected_suffix = ".pte" if engine is EngineType.EXECUTORCH else ".tflite"
    if path.suffix.lower() != expected_suffix:
        raise ConfigError(
            f"{engine.value} model file must use the {expected_suffix} extension: {path.name}",
            hint=(
                "Use a PTE exported for ExecuTorch when engine.type is executorch."
                if engine is EngineType.EXECUTORCH
                else "TFLM, heliaRT, and heliaAOT consume TFLite flatbuffers."
            ),
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
    expected_magic = b"ET" if engine is EngineType.EXECUTORCH else _TFLITE_MAGIC
    if expected_magic not in head:
        format_name = "ExecuTorch PTE" if engine is EngineType.EXECUTORCH else "TFLite flatbuffer"
        raise ConfigError(
            f"Model file does not look like an {format_name}: {path}",
            hint=(
                f"Expected the {expected_magic!r} marker within the first 16 bytes. "
                "Make sure the model export completed successfully."
            ),
        )


def _check_softmax_scaling(path: Path, engine: EngineType) -> None:
    """Reject quantized Softmax scales the selected engine cannot handle (#57).

    TFLM aborts inside ``AllocateTensors()`` when ``beta * input_scale * 2**26
    <= 1`` -- from the host that is a HardFault / RTT timeout with no
    indication the model was the problem, after the board was powered, the
    firmware built, and the image flashed. heliaAOT has no target-side abort,
    but its compiler raises ``ValueError: negative shift count`` for
    multipliers below 0.5 -- a stage-2 crash whose message names nothing. The
    two numbers sit in the flatbuffer, so either run dies HERE instead, with
    the quantization named. See ``evaluation/softmax_preflight`` for the
    per-engine boundaries and how each was established.

    Ordered after :func:`_check_model`, which has already verified the file
    reads and carries the TFLite magic -- so a parse failure past that point
    is a malformed flatbuffer, reported as such rather than as a stack trace.
    """
    if engine is EngineType.EXECUTORCH:
        return  # .pte -- never parses a TFLite flatbuffer
    try:
        findings = scan_softmax_scaling(path)
    except Exception as exc:  # struct.error / IndexError on malformed bytes
        raise ConfigError(
            f"Model file could not be parsed as a TFLite flatbuffer: {path} ({exc})",
            hint="The file carries the TFL3 marker but its structure is "
            "damaged — re-export the model.",
        ) from exc

    # An op with no usable beta is its own failure, ahead of any engine
    # verdict: TFLM value-initialises beta to 0.0 while helia-aot's field
    # default is 1.0, so the two engines do not even agree on what this model
    # says -- and neither runs it. Reporting it as a scale problem printed
    # "needs input_scale > inf" and, for helia-aot, named a crash in a
    # function that model never reaches (both found by review).
    no_beta = [f for f in findings if not f.has_usable_beta]
    if no_beta:
        where = "; ".join(
            f"subgraph {f.subgraph_index} op {f.op_index} (input '{f.input_tensor}')"
            for f in no_beta
        )
        raise ConfigError(
            f"{len(no_beta)} quantized Softmax op(s) in {path.name} carry no "
            f"usable SoftmaxOptions beta: {where}. TFLM reads beta as 0 for "
            "these and cannot prepare them; heliaAOT reads 1.0 and fails "
            "earlier still, while parsing the operator.",
            hint=(
                "No input scale can compensate — beta multiplies the scale, "
                "so a zero beta zeroes the product whatever the scale is. The "
                "exported graph is missing its Softmax options; re-export "
                "from the source model rather than re-quantizing."
            ),
        )

    if engine in (EngineType.TFLM, EngineType.HELIA_RT):
        unsupported = [f for f in findings if not f.supported]
        consequence = (
            "The target would abort inside AllocateTensors() (a HardFault / "
            "RTT timeout) before running a single inference."
        )
    elif engine is EngineType.HELIA_AOT:
        verdicts = {
            (f.subgraph_index, f.op_index): (f, aot_softmax_verdict(f.multiplier))
            for f in findings
        }
        unsupported = [f for f, verdict in verdicts.values() if verdict == "error"]
        consequence = (
            "The heliaAOT compiler would crash at calculate_input_radius "
            "('ValueError: negative shift count') during model compilation."
        )
        for f, verdict in verdicts.values():
            if verdict == "warn":
                log.warning(
                    "Softmax at subgraph %d op %d (input '%s') has a "
                    "degenerate input scale (beta=%g x %.9g x 2^26 = %.6g): "
                    "heliaAOT compiles it, but the input can only represent "
                    "a logit range far too small for a meaningful softmax, "
                    "and the same model aborts under helia-rt.",
                    f.subgraph_index,
                    f.op_index,
                    f.input_tensor,
                    f.beta,
                    f.input_scale,
                    f.multiplier,
                )
    else:
        # A future engine parses (so a corrupt file still dies here) but gets
        # no verdict. Deliberately fail-OPEN: wrongly gating a working engine
        # raises with no override, which is how v1 of this check shipped. An
        # engine that runs TFLM's interpreter on target belongs in the tuple
        # above -- a new adapter can name TFLM's engine_header (as heliaRT
        # does) and inherit TFLM firmware without inheriting this gate.
        return

    if not unsupported:
        return
    # The printed bound is minimum_scale -- the TFLM threshold, which for a
    # helia-aot error is 2x the compiler's own 0.5 boundary. Deliberate: a
    # scale clearing it works on EVERY engine, whereas the tighter AOT bound
    # lands the user in the 0.5..1.0 band that only warns here and still
    # aborts under helia-rt. Portable advice over minimal advice.
    detail = "; ".join(
        f"subgraph {f.subgraph_index} op {f.op_index} (input "
        f"'{f.input_tensor}'): beta={f.beta:g} x input_scale="
        f"{f.input_scale:.9g} x 2^26 = {f.multiplier:.6g}, needs input_scale "
        f"> {f.minimum_scale:.4g}"
        for f in unsupported
    )
    raise ConfigError(
        f"{len(unsupported)} quantized Softmax op(s) in {path.name} have an "
        f"input scale {engine.value} cannot handle: {detail}. {consequence}",
        hint=(
            "A scale this small usually means the layer feeding the Softmax "
            "produced a degenerate activation range during quantization — "
            "re-quantize with a representative calibration dataset, or check "
            "that the exported graph's final layers match the trained model."
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

    if runtime_arena == Placement.PSRAM or runtime_weights == Placement.PSRAM:
        # The engine, not the placement, decides how PSRAM gets populated
        # (#219) — so PSRAM validity is an adapter capability, not an
        # EngineType branch.
        adapter = get_adapter(cfg.engine.type)
        if adapter.psram_weights_source is PsramWeightsSource.UNSUPPORTED:
            raise ConfigError(
                f"{adapter.name} profiling does not support PSRAM model or arena placement.",
                hint="Use model.arena_location=tcm|sram and model.weights_location=tcm|sram|mram.",
            )
        # Engine-specific PSRAM-config constraints (e.g. heliaAOT requires
        # external-arena mode, or its firmware renders no PSRAM code).
        adapter.check_psram_placement(cfg)

        if (
            runtime_weights == Placement.PSRAM
            and adapter.psram_weights_source is PsramWeightsSource.HOST_UPLOAD
            and cfg.target.transport != Transport.RTT
        ):
            raise ConfigError(
                "PSRAM model weights require target.transport='rtt' for this engine.",
                hint=(
                    "Host-side PSRAM model upload uses the RTT transport. "
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
    if cfg.engine.type is EngineType.EXECUTORCH and cfg.power.enabled:
        raise ConfigError(
            "ExecuTorch profiling does not yet support the dedicated power binary.",
            hint="Disable power capture; clean end-to-end cycle measurements are supported.",
        )
    if (
        cfg.engine.type is EngineType.EXECUTORCH
        and cfg.profiling.clean_window_probe is CleanWindowProbe.BUSY_LOOP
    ):
        # The busy_loop probe is a power-window diagnostic: it replaces the
        # model with a calibrated CPU spin so an external instrument has a
        # known-shape window to gate on, and reports HPX_CLEAN_INFER_COUNT=1
        # for the single unit of work it performs.  ExecuTorch has no power
        # support at all (rejected just above), so the probe has nothing to
        # gate -- and since #154 phase 4 the render would silently succeed:
        # main_executorch.cc.j2's engine_clean_window override delegates the
        # busy_loop branch straight back to the base, so the firmware would
        # come back with a nop-loop window reporting COUNT=1 where this
        # engine's HPX_CLEAN_INFER_* are defined as real execute-only
        # inference timing.
        raise ConfigError(
            "The busy_loop clean-window probe requires an engine with "
            "power-window support; engine.type=executorch has none.",
            hint=(
                "Use profiling.clean_window_probe=infer with ExecuTorch, or "
                "switch to an engine that supports the dedicated power binary "
                "(tflm, helia-rt, helia-aot)."
            ),
        )
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
