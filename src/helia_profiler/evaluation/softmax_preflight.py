"""Host-side preflight for TFLM's quantized-Softmax scaling requirement (#57).

TFLM prepares every int8/uint8 Softmax with ``PreprocessSoftmaxScaling(beta,
input_scale, kScaledDiffIntegerBits=5, ...)``, which computes the fixed-point
input multiplier as ``beta * input_scale * 2**(31 - 5)`` and hands it to
``QuantizeMultiplierGreaterThanOne()`` -- whose first act is
``TFLITE_CHECK_GT(double_multiplier, 1.)``. A model whose Softmax input scale
puts that product at or below 1.0 therefore aborts ON TARGET, inside
``AllocateTensors()``, before a single inference runs. From the host that
presents as a HardFault / RTT timeout with no hint the model was the problem.

The check is pure arithmetic on two numbers sitting in the flatbuffer, so it
runs on the host, before anything is built, flashed, or even powered. Ground
truth from the issue: the failing model's final Softmax has input scale
4.305568790385905e-09, giving multiplier 0.2889418303966522 -- the exact value
this module computes for it -- while the MLPerf-Tiny KWS reference model's
0.14469251036643982 passes with six orders of magnitude to spare.

Parsing uses the package's own minimal reader (``_tflite_reader``) rather than
``ai-edge-litert``: litert is an optional extra, absent from a plain helia-rt
install and from the CI unit-test environment, and a preflight that silently
skips on exactly the installs that hit the bug is not a preflight. The reader
is cross-validated against litert wherever litert is present.

Engine scope -- established by running each engine's actual code, after two
wrong versions of this paragraph:

* **TFLM and heliaRT** run TFLM's interpreter on target; all three vendored
  kernel implementations share the aborting ``CalculateSoftmaxParams`` chain.
  Gate: ``multiplier <= 1.0`` is an error (strict ``TFLITE_CHECK_GT``).
* **heliaAOT** does NOT share the helper -- it computes softmax scaling on
  the host -- but its own path fails one call later: ``calculate_input_radius``
  does ``1 << shift`` on the frexp exponent, so ``multiplier < 0.5`` raises
  ``ValueError: negative shift count`` inside the compiler (verified against
  the pinned helia-aot 0.18). The first fix exempted AOT entirely after
  verifying only the first call. Gate: ``multiplier < 0.5`` is an error with
  an AOT-specific message; ``0.5 <= multiplier <= 1.0`` compiles but is
  numerically degenerate (diff_min lands 7 orders of magnitude from healthy)
  and would abort under helia-rt, so it warns.
* **ExecuTorch** consumes ``.pte`` and never reaches any of this.

Float Softmax never quantizes the multiplier; int16 takes different prepare
paths not shown to fail -- gating either without evidence would reject models
that may run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._tflite_reader import TENSOR_TYPE_UINT8, read_quantized_softmax_ops

#: ``kScaledDiffIntegerBits`` in TFLM's softmax_common.cc. The multiplier is
#: scaled by ``2**(31 - this)``; 5 integer bits leaves 2**26.
TFLM_SOFTMAX_INTEGER_BITS = 5

_MULTIPLIER_SHIFT = 31 - TFLM_SOFTMAX_INTEGER_BITS


#: Below this, helia-aot 0.18's own softmax path raises ``ValueError:
#: negative shift count``: ``AirFixedPointScale.from_real_multiplier`` stores
#: the frexp exponent as the shift, and multipliers in [0.5, 1) have exponent
#: 0 while anything below 0.5 goes negative -- which ``calculate_input_radius``
#: then feeds to ``1 << shift``. Exactly 0.5 has exponent 0 and compiles. The
#: boundary is a property of the PINNED helia-aot; revisit on a version bump.
AOT_COMPILER_MIN_MULTIPLIER = 0.5


def aot_softmax_verdict(multiplier: float) -> str:
    """helia-aot's fate for one quantized Softmax: 'error', 'warn', or 'ok'.

    'error': the compiler itself raises (negative shift count) -- the gate
    exists to replace that stage-2 crash with an actionable message. 'warn':
    it compiles, but the input can only represent a logit range orders of
    magnitude too small for a meaningful softmax, and the same model aborts
    under helia-rt -- worth telling the user, not worth blocking a profile.
    """
    if multiplier < AOT_COMPILER_MIN_MULTIPLIER:
        return "error"
    if multiplier <= 1.0:
        return "warn"
    return "ok"


def softmax_input_multiplier(beta: float, input_scale: float) -> float:
    """The value TFLM's ``QuantizeMultiplierGreaterThanOne`` receives.

    The target aborts unless this is strictly greater than 1.0.
    """
    return beta * input_scale * float(1 << _MULTIPLIER_SHIFT)


@dataclass(frozen=True)
class SoftmaxScaling:
    """One quantized Softmax op's scaling, as TFLM will see it on target."""

    subgraph_index: int
    op_index: int
    input_tensor: str
    input_type: str
    beta: float
    input_scale: float
    multiplier: float

    @property
    def supported(self) -> bool:
        """Whether TFLM's ``TFLITE_CHECK_GT(multiplier, 1.)`` passes."""
        return self.multiplier > 1.0

    @property
    def minimum_scale(self) -> float:
        """The smallest input scale this op's beta could run with."""
        if self.beta <= 0.0:
            return float("inf")
        return 1.0 / (self.beta * float(1 << _MULTIPLIER_SHIFT))


def scan_softmax_scaling(model_path: Path | str) -> list[SoftmaxScaling]:
    """Every int8/uint8 Softmax in the model, with its on-target multiplier.

    Returns ALL quantized Softmax ops rather than only failing ones, so a
    caller (and a test) can tell "the model has no problem" apart from "the
    scanner saw nothing" -- the difference between a passing model and a
    vacuous check.

    A quantized Softmax with no scale at all is reported with multiplier 0.0
    (unsupported): TFLM would fail to prepare it too, and silence here would
    hide exactly the class of model this exists to catch.
    """
    findings: list[SoftmaxScaling] = []
    for op in read_quantized_softmax_ops(Path(model_path).read_bytes()):
        scale = op.input_scale if op.input_scale is not None else 0.0
        findings.append(
            SoftmaxScaling(
                subgraph_index=op.subgraph_index,
                op_index=op.op_index,
                input_tensor=op.input_tensor,
                input_type="uint8" if op.input_type == TENSOR_TYPE_UINT8 else "int8",
                beta=op.beta,
                input_scale=scale,
                multiplier=softmax_input_multiplier(op.beta, scale),
            )
        )
    return findings
