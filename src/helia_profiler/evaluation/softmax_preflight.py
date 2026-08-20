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

Applies to every TFLite-consuming engine -- TFLM, heliaRT, and heliaAOT share
the reference helper (the issue reproduces the abort on all of them).
ExecuTorch consumes ``.pte`` and never reaches this code. Float Softmax never
quantizes the multiplier; int16 takes a different TFLM prepare path that has
not been shown to abort -- gating either without evidence would reject models
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
