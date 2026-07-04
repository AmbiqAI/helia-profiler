"""Resolver planning for RT/TFLM firmware generation."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from ..engines import EngineType
from ..model_analysis import ModelAnalysis

log = logging.getLogger("hpx")


@dataclass(frozen=True)
class ResolverPlan:
    """Concrete registration plan for generated firmware."""

    mode: str
    registrations: tuple[str, ...]

    @property
    def max_ops(self) -> int:
        return max(1, len(self.registrations))


_ALL_REGISTRATIONS: tuple[tuple[str, str], ...] = (
    ("ABS", "r.AddAbs();"),
    ("ADD", "r.AddAdd();"),
    ("ADD_N", "r.AddAddN();"),
    ("ASSIGN_VARIABLE", "r.AddAssignVariable();"),
    ("ARG_MAX", "r.AddArgMax();"),
    ("ARG_MIN", "r.AddArgMin();"),
    ("AVERAGE_POOL_2D", "r.AddAveragePool2D();"),
    ("BATCH_MATMUL", "r.AddBatchMatMul();"),
    ("BATCH_TO_SPACE_ND", "r.AddBatchToSpaceNd();"),
    ("BROADCAST_ARGS", "r.AddBroadcastArgs();"),
    ("BROADCAST_TO", "r.AddBroadcastTo();"),
    ("CAST", "r.AddCast();"),
    ("CALL_ONCE", "r.AddCallOnce();"),
    ("CEIL", "r.AddCeil();"),
    ("CONCATENATION", "r.AddConcatenation();"),
    ("CONV_2D", "r.AddConv2D();"),
    ("CUM_SUM", "r.AddCumSum();"),
    ("DEPTH_TO_SPACE", "r.AddDepthToSpace();"),
    ("DEPTHWISE_CONV_2D", "r.AddDepthwiseConv2D();"),
    ("DEQUANTIZE", "r.AddDequantize();"),
    ("DIV", "r.AddDiv();"),
    ("ELU", "r.AddElu();"),
    ("EMBEDDING_LOOKUP", "r.AddEmbeddingLookup();"),
    ("EQUAL", "r.AddEqual();"),
    ("EXP", "r.AddExp();"),
    ("EXPAND_DIMS", "r.AddExpandDims();"),
    ("FILL", "r.AddFill();"),
    ("FLOOR", "r.AddFloor();"),
    ("FLOOR_DIV", "r.AddFloorDiv();"),
    ("FLOOR_MOD", "r.AddFloorMod();"),
    ("FULLY_CONNECTED", "r.AddFullyConnected();"),
    ("GATHER", "r.AddGather();"),
    ("GATHER_ND", "r.AddGatherNd();"),
    ("GREATER", "r.AddGreater();"),
    ("GREATER_EQUAL", "r.AddGreaterEqual();"),
    ("HARD_SWISH", "r.AddHardSwish();"),
    ("L2_NORMALIZATION", "r.AddL2Normalization();"),
    ("L2_POOL_2D", "r.AddL2Pool2D();"),
    ("LEAKY_RELU", "r.AddLeakyRelu();"),
    ("LESS", "r.AddLess();"),
    ("LESS_EQUAL", "r.AddLessEqual();"),
    ("LOG", "r.AddLog();"),
    ("LOG_SOFTMAX", "r.AddLogSoftmax();"),
    ("LOGICAL_AND", "r.AddLogicalAnd();"),
    ("LOGICAL_NOT", "r.AddLogicalNot();"),
    ("LOGICAL_OR", "r.AddLogicalOr();"),
    ("LOGISTIC", "r.AddLogistic();"),
    ("MAX_POOL_2D", "r.AddMaxPool2D();"),
    ("MAXIMUM", "r.AddMaximum();"),
    ("MEAN", "r.AddMean();"),
    ("MINIMUM", "r.AddMinimum();"),
    ("MIRROR_PAD", "r.AddMirrorPad();"),
    ("MUL", "r.AddMul();"),
    ("NEG", "r.AddNeg();"),
    ("NOT_EQUAL", "r.AddNotEqual();"),
    ("PACK", "r.AddPack();"),
    ("PAD", "r.AddPad();"),
    ("PAD_V2", "r.AddPadV2();"),
    ("PRELU", "r.AddPrelu();"),
    ("QUANTIZE", "r.AddQuantize();"),
    ("READ_VARIABLE", "r.AddReadVariable();"),
    ("REDUCE_MAX", "r.AddReduceMax();"),
    ("RELU", "r.AddRelu();"),
    ("RELU6", "r.AddRelu6();"),
    ("RESHAPE", "r.AddReshape();"),
    ("RESIZE_BILINEAR", "r.AddResizeBilinear();"),
    ("RESIZE_NEAREST_NEIGHBOR", "r.AddResizeNearestNeighbor();"),
    ("ROUND", "r.AddRound();"),
    ("RSQRT", "r.AddRsqrt();"),
    ("SELECT_V2", "r.AddSelectV2();"),
    ("SHAPE", "r.AddShape();"),
    ("SLICE", "r.AddSlice();"),
    ("SOFTMAX", "r.AddSoftmax();"),
    ("SPACE_TO_BATCH_ND", "r.AddSpaceToBatchNd();"),
    ("SPACE_TO_DEPTH", "r.AddSpaceToDepth();"),
    ("SPLIT", "r.AddSplit();"),
    ("SPLIT_V", "r.AddSplitV();"),
    ("SQRT", "r.AddSqrt();"),
    ("SQUARE", "r.AddSquare();"),
    ("SQUARED_DIFFERENCE", "r.AddSquaredDifference();"),
    ("SQUEEZE", "r.AddSqueeze();"),
    ("STRIDED_SLICE", "r.AddStridedSlice();"),
    ("SUB", "r.AddSub();"),
    ("SUM", "r.AddSum();"),
    ("SVDF", "r.AddSvdf();"),
    ("TANH", "r.AddTanh();"),
    ("TRANSPOSE", "r.AddTranspose();"),
    ("TRANSPOSE_CONV", "r.AddTransposeConv();"),
    ("UNIDIRECTIONAL_SEQUENCE_LSTM", "r.AddUnidirectionalSequenceLSTM();"),
    ("UNPACK", "r.AddUnpack();"),
    ("VAR_HANDLE", "r.AddVarHandle();"),
    ("ZEROS_LIKE", "r.AddZerosLike();"),
)

_ALL_BY_NAME = dict(_ALL_REGISTRATIONS)

# heliaRT can require QUANTIZE during its runtime model preparation even when
# the source flatbuffer operator list does not contain an explicit QUANTIZE op.
_HELIA_RT_AUTO_REQUIRED_OPS = frozenset({"QUANTIZE", "DEQUANTIZE"})


def build_resolver_plan(
    *,
    engine_type: EngineType,
    engine_config: dict[str, object],
    model_analysis: ModelAnalysis | None,
) -> ResolverPlan:
    """Return the selected resolver strategy for generated firmware.

    ``helia-rt`` supports:
    - ``engine.config.resolver_ops: all``  -> current broad allowlist
    - ``engine.config.resolver_ops: auto`` -> builtins observed in model analysis

    All other engines keep the broad allowlist behavior.
    """

    default_mode = "auto" if engine_type is EngineType.HELIA_RT else "all"
    mode = str(engine_config.get("resolver_ops", default_mode)).strip().lower()
    if mode not in {"all", "auto"}:
        log.warning(
            "Unknown engine.config.resolver_ops=%r; falling back to 'all'",
            engine_config.get("resolver_ops"),
        )
        mode = "all"

    if engine_type is not EngineType.HELIA_RT:
        mode = "all"

    if mode == "auto":
        if model_analysis is None:
            log.warning(
                "engine.config.resolver_ops=auto requested, but model analysis is "
                "unavailable; falling back to broad resolver registration"
            )
        else:
            model_ops = {layer.op for layer in model_analysis.layers}
            if engine_type is EngineType.HELIA_RT:
                model_ops |= _HELIA_RT_AUTO_REQUIRED_OPS
            registrations = tuple(code for name, code in _ALL_REGISTRATIONS if name in model_ops)
            unsupported = sorted(
                op for op in model_ops if op not in _ALL_BY_NAME and not op.startswith("CUSTOM(")
            )
            if unsupported:
                log.warning(
                    "Auto resolver selection found builtin ops without RT registration "
                    "helpers: %s. Firmware preflight will still report them at runtime.",
                    ", ".join(unsupported),
                )
            if registrations:
                return ResolverPlan(mode="auto", registrations=registrations)
            log.warning(
                "Auto resolver selection produced no supported registrations; "
                "falling back to broad resolver registration"
            )

    return ResolverPlan(
        mode="all",
        registrations=tuple(code for _, code in _ALL_REGISTRATIONS),
    )
