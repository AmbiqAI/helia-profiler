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

Float Softmax never quantizes the multiplier. int16 is out of scope in both
directions, and the asymmetry is worth knowing: TFLM's int16 prepare path has
not been shown to abort, while helia-aot's int16 branch never calls
calculate_input_radius at all -- a degenerate int16 scale reaches the
generated source as a negative shift with no host-side check anywhere. So this
gate protects int8/uint8 only. Gating int16 without evidence of a real failure
would reject models that may run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ._tflite_reader import TENSOR_TYPE_UINT8, read_quantized_softmax_ops

# helia-aot is an optional extra (same guard shape as model_analysis): the
# verdict itself must stay pure arithmetic -- a preflight that needs the AOT
# compiler installed to predict the AOT compiler is no preflight -- but where
# the package IS present, constants that mirror its internals are read live
# so a version bump moves them here instead of silently diverging (#147).
try:
    from helia_aot.air.options import (  # type: ignore[import-untyped]
        AirSoftmaxOptions as _AirSoftmaxOptions,
    )
except ImportError:
    _AirSoftmaxOptions = None

#: ``kScaledDiffIntegerBits`` in TFLM's softmax_common.cc. The multiplier is
#: scaled by ``2**(31 - this)``; 5 integer bits leaves 2**26.
TFLM_SOFTMAX_INTEGER_BITS = 5

_MULTIPLIER_SHIFT = 31 - TFLM_SOFTMAX_INTEGER_BITS


#: helia-aot 0.18 raises ``ValueError: negative shift count`` for multipliers
#: in ``[2**-32, 0.5)``, and ONLY that band.
#:
#: ``AirFixedPointScale.from_real_multiplier`` stores the frexp exponent as
#: the shift, which ``calculate_input_radius`` then feeds to ``1 << shift``.
#: Multipliers in [0.5, 1) have exponent 0; below 0.5 it goes negative and the
#: shift raises. But ``quantize_multiplier`` FLUSHES TO (0, 0) once the
#: exponent would fall below -31, so an even smaller multiplier gets shift 0
#: and compiles again. Measured against the pinned 0.18:
#:
#:     2**-33  shift  0  compiles      0.2889 (the issue)  shift -1  raises
#:     2**-32  shift -31 raises        0.49                shift -1  raises
#:     2**-31  shift -30 raises        0.5                 shift  0  compiles
#:
#: The first version of this gate errored on everything below 0.5, which
#: blocked the sub-flush band that helia-aot compiles fine -- the same
#: over-blocking mistake as gating heliaAOT at all, one dimension over.
#:
#: One refinement on top of the table: ``quantize_multiplier`` ROUNDS the
#: frexp fraction to Q31 (round half up) and promotes the exponent when the
#: fraction rounds to 1.0 -- BEFORE the flush check. So the band is exact for
#: the ROUNDED multiplier, and off by one rounding step at each raw edge:
#: [0.5 - 2**-33, 0.5) rounds up to 0.5 and compiles, while
#: [2**-32 * (1 - 2**-32), 2**-32) rounds up to 2**-32 and raises.
#: :func:`_aot_q31_rounded_multiplier` applies that rounding so the constants
#: below delimit the observable band exactly (found by the #147 sweep).
#:
#: All of this mirrors the PINNED helia-aot's internals by hand. The tripwire
#: sweep in tests/test_softmax_preflight.py drives the REAL
#: ``preprocess_softmax_scaling`` / ``calculate_input_radius`` across every
#: edge and fails CI's analysis-tests job if a version bump moves any of it.
AOT_COMPILER_MIN_MULTIPLIER = 0.5
AOT_FLUSH_TO_ZERO_MULTIPLIER = 2.0**-32

#: What an ABSENT ``SoftmaxOptions`` table means for beta, per engine -- and
#: they disagree, which is why the verdict cannot be computed once and shared.
#: TFLM value-initialises the POD (``ParseSoftmax``'s no-options branch is a
#: deliberate no-op, verified against the vendored source), so beta reaches
#: the kernel as 0.0. helia-aot's ``SoftmaxOptions`` is a pydantic model whose
#: field default is 1.0. Applying TFLM's convention to an AOT verdict is how
#: this gate came to claim a crash that could not happen.
#:
#: AOT_ABSENT_BETA is read LIVE from the installed helia-aot's pydantic field
#: default whenever the optional extra is present, so a version bump that
#: changes the default changes this constant with it; 1.0 is the fallback for
#: installs without the extra, pinned by test_softmax_preflight (#147).
TFLM_ABSENT_BETA = 0.0
AOT_ABSENT_BETA = 1.0 if _AirSoftmaxOptions is None else float(_AirSoftmaxOptions().beta)


def _aot_q31_rounded_multiplier(multiplier: float) -> float:
    """The multiplier as helia-aot's ``quantize_multiplier`` actually sees it.

    Mirrors, expression for expression, the rounding at the top of the pinned
    ``helia_aot.air.utils.quantize_multiplier``: the frexp fraction becomes a
    Q31 integer via ``floor(fraction * 2**31 + 0.5)`` (round HALF UP, not
    banker's), and a fraction that rounds to 1.0 is promoted one exponent --
    both before the sub-``2**-32`` flush check. Applying the band constants to
    this rounded value instead of the raw one is what makes them exact at the
    edges. Caller guarantees a finite argument (frexp of an infinity would
    overflow the floor).
    """
    fraction, exponent = math.frexp(multiplier)
    quantized = math.floor(fraction * (1 << 31) + 0.5)
    if quantized == (1 << 31):
        quantized //= 2
        exponent += 1
    return math.ldexp(quantized, exponent - 31)


def aot_softmax_verdict(multiplier: float) -> str:
    """helia-aot's fate for one quantized Softmax: 'error', 'warn', or 'ok'.

    'error': the compiler itself raises ``negative shift count`` -- the gate
    exists to replace that stage-2 crash with an actionable message. 'warn':
    it compiles, but the input can only represent a logit range orders of
    magnitude too small for a meaningful softmax, and the same model aborts
    under helia-rt -- worth telling the user, not worth blocking a profile.

    NaN is checked first and errors: it can only come from a corrupt file, and
    every ordered comparison against it is False, so falling through would
    return 'ok' for a model helia-aot raises on (found by review).
    """
    if multiplier != multiplier:  # NaN
        return "error"
    if (
        math.isfinite(multiplier)
        and AOT_FLUSH_TO_ZERO_MULTIPLIER
        <= _aot_q31_rounded_multiplier(multiplier)
        < AOT_COMPILER_MIN_MULTIPLIER
    ):
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
    def has_usable_beta(self) -> bool:
        """Whether beta can rescue this op at all.

        beta <= 0 means the model carries no usable ``SoftmaxOptions``: no
        input scale makes ``beta * scale * 2**26`` exceed 1, so the advice
        'use a larger scale' is false and :attr:`minimum_scale` is infinite.
        """
        return self.beta > 0.0

    @property
    def minimum_scale(self) -> float:
        """The smallest input scale this op's beta could run with.

        ``inf`` when :attr:`has_usable_beta` is False -- callers must not
        print it raw, which produced 'needs input_scale > inf' (found by
        review).
        """
        if not self.has_usable_beta:
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
