"""Validate model files and engine-specific quantized Softmax support."""

from __future__ import annotations

import logging
from pathlib import Path

from . import EngineType
from ..errors import ConfigError
from ..modelcost.softmax_preflight import aot_softmax_verdict, scan_softmax_scaling

log = logging.getLogger("hpx")


# TFLite flatbuffers start with a 4-byte file identifier.  Some flatc
# versions emit the identifier at offset 4 (after the root-table offset),
# so we accept either placement.
_TFLITE_MAGIC = b"TFL3"


def check_model(path: Path, engine: EngineType) -> None:
    if not path.exists():
        raise ConfigError(
            f"Model file not found: {path}",
            hint="Check the positional MODEL argument on the CLI or model.path in YAML.",
        )
    if not path.is_file():
        raise ConfigError(
            f"Model path is not a regular file: {path}",
            hint="model.path must point to a model file (.tflite or .pte), not a directory.",
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


def check_softmax_scaling(path: Path, engine: EngineType) -> None:
    """Reject quantized Softmax scales the selected engine cannot handle (#57).

    TFLM aborts inside ``AllocateTensors()`` when ``beta * input_scale * 2**26
    <= 1`` -- from the host that is a HardFault / RTT timeout with no
    indication the model was the problem, after the board was powered, the
    firmware built, and the image flashed. heliaAOT has no target-side abort,
    but its compiler raises ``ValueError: negative shift count`` for
    multipliers below 0.5 -- a stage-2 crash whose message names nothing. The
    two numbers sit in the flatbuffer, so either run dies HERE instead, with
    the quantization named. See ``modelcost/softmax_preflight`` for the
    per-engine boundaries and how each was established.

    Ordered after :func:`check_model`, which has already verified the file
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
    # verdict: TFLM value-initialises beta to 0.0 while helia-aot defaults it
    # to 1.0, so the engines do not agree on what the model says -- and
    # neither runs it (#57).
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
            (f.subgraph_index, f.op_index): (f, aot_softmax_verdict(f.multiplier)) for f in findings
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
