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
  does ``1 << shift`` on the quantized-multiplier shift, so the compiler
  raises ``ValueError: negative shift count`` exactly when that shift is
  negative. The gate mirrors the shift computation itself
  (``_aot_quantized_shift``) -- for positive multipliers that is the
  Q31-rounded band ``[2**-32, 0.5)``, for negatives the boundaries differ
  because the Q31 promotion fires only at ``+2**31`` (see the comment block
  above ``_aot_quantized_shift``). The first fix exempted AOT entirely after
  verifying only the first call. Gate: a negative shift is an error with an
  AOT-specific message; ``0.5 <= multiplier <= 1.0`` compiles but is
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
    from helia_aot.air.options import (
        AirSoftmaxOptions as _AirSoftmaxOptions,
    )
except ImportError:
    _AirSoftmaxOptions = None

#: ``kScaledDiffIntegerBits`` in TFLM's softmax_common.cc. The multiplier is
#: scaled by ``2**(31 - this)``; 5 integer bits leaves 2**26.
TFLM_SOFTMAX_INTEGER_BITS = 5

_MULTIPLIER_SHIFT = 31 - TFLM_SOFTMAX_INTEGER_BITS


# How helia-aot 0.18 decides a quantized Softmax's fate, measured and then
# mirrored exactly (the history matters — this gate has been wrong three
# times, each in a different direction):
#
#   * ``AirFixedPointScale.from_real_multiplier`` -> ``quantize_multiplier``
#     stores the frexp exponent as the shift; ``calculate_input_radius`` does
#     ``1 << shift``, so a NEGATIVE shift raises ``ValueError`` inside the
#     compiler. Shift < -31 is flushed to (0, 0) first (compiles), > 30 is
#     clamped.
#   * v1 of this gate errored on everything below 0.5 — over-blocking the
#     sub-flush band. v2 used band constants on the raw multiplier — off by
#     one ULP at each edge, because quantize_multiplier ROUNDS the fraction
#     to Q31 (half up, promoting at +2**31) BEFORE the flush check. v3
#     banded the ROUNDED value — exact for positives, but the promotion
#     fires only at +2**31, so for negatives the rounded value is AMBIGUOUS
#     (-0.5 arises from both a compiling and a raising state; #172 round-2).
#   * The verdict therefore mirrors the SHIFT itself
#     (:func:`_aot_quantized_shift`): error iff shift < 0. Measured against
#     the pinned 0.18 (positive column, negatives differ per the above):
#
#       2**-33  shift  0  compiles      0.2889 (the issue)  shift -1  raises
#       2**-32  shift -31 raises        0.49                shift -1  raises
#       2**-31  shift -30 raises        0.5                 shift  0  compiles
#
# All of this mirrors the PINNED helia-aot's internals by hand. The tripwire
# sweep in tests/test_softmax_preflight.py drives the REAL
# ``preprocess_softmax_scaling`` / ``calculate_input_radius`` across every
# edge — both signs — and fails CI's analysis-tests job if a version bump
# moves any of it.

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


def _read_aot_absent_beta() -> float:
    """Live-read helia-aot's beta default, degrading to 1.0 on ANY failure.

    This runs at import time of a module that stages/preflight.py pulls in
    for every engine, so a helia-aot bump that makes the field required or
    renames it must not turn into "hpx won't start" — it degrades here and
    fails LOUDLY in analysis-tests instead, where the aot-guarded pinning
    test compares this value against the real default (#172 review).
    """
    if _AirSoftmaxOptions is None:
        return 1.0
    try:
        return float(_AirSoftmaxOptions().beta)
    except Exception:  # noqa: BLE001 - degrade, never block startup
        return 1.0


AOT_ABSENT_BETA = _read_aot_absent_beta()


def _aot_quantized_shift(multiplier: float) -> int:
    """The shift helia-aot's ``quantize_multiplier`` emits — sign included.

    ``calculate_input_radius`` raises exactly when this is negative
    (``1 << shift``), so the verdict asks THIS, not a band on the rounded
    value: for negative multipliers the rounded value is ambiguous — the
    ``== 1 << 31`` promotion fires only for positive fractions, so ``-0.5``
    arises both from exponent 0 (compiles) and from ``-0.49999999999999994``
    rounding to ``-2**31`` at exponent -1 (raises). Positives never hit the
    ambiguity, which is why the band constants stayed exact there; the
    round-2 review's negative sweep is what exposed the asymmetry (218/689
    disagreements under a sign-blind guard). Mirrors the pinned
    ``helia_aot.air.utils.quantize_multiplier`` expression for expression:
    zero early-out, frexp, Q31 round-half-up, positive-only promotion, the
    ``shift < -31`` flush to (0, 0), and the ``shift > 30`` clamp.
    """
    if multiplier == 0.0:
        return 0
    fraction, exponent = math.frexp(multiplier)
    quantized = math.floor(fraction * (1 << 31) + 0.5)
    if quantized == (1 << 31):
        quantized //= 2
        exponent += 1
    if exponent < -31:
        return 0
    if exponent > 30:
        return 30
    return exponent


def aot_softmax_verdict(multiplier: float) -> str:
    """helia-aot's fate for one quantized Softmax: 'error', 'warn', or 'ok'.

    'error': the compiler itself raises ``negative shift count`` -- the gate
    exists to replace that stage-2 crash with an actionable message. 'warn':
    it compiles, but the input can only represent a logit range orders of
    magnitude too small for a meaningful softmax, and the same model aborts
    under helia-rt -- worth telling the user, not worth blocking a profile.

    NaN errors (only a corrupt file produces it, and every ordered
    comparison against it is False, so falling through returned 'ok' for a
    model helia-aot raises on). ``-inf`` errors: ``preprocess_softmax_scaling``
    overflows the Q31 floor on it. Other negatives get the SAME shift mirror
    as positives — the first #172 fix blanket-errored them, and the round-2
    negative sweep showed the real chain compiles most of that domain
    (e.g. -0.75, shift 0); the corrupt-file smell is real but the verdict's
    contract is the compiler's fate, nothing else.
    """
    if multiplier != multiplier:  # NaN
        return "error"
    if not math.isfinite(multiplier):
        return "error" if multiplier < 0.0 else "ok"
    if _aot_quantized_shift(multiplier) < 0:
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
