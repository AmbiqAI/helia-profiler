"""Model analysis — extract per-layer OPS/MACs from a LiteRT flatbuffer.

Uses ``ai-edge-litert`` (official Google LiteRT package) and ``flatbuffers``
to parse the ``.tflite`` schema and compute MAC counts from operator
parameters and tensor shapes.  Both packages are **optional** — when
unavailable the analysis gracefully returns ``None``.

Optionally, when ``helia-aot`` is installed, :func:`analyze_air_model` can
compute the same breakdown on a *transformed* ``AirModel`` graph after AOT
fusions/optimizations.

Public API
----------
``analyze_model(path)``
    Parse a ``.tflite`` file and return a :class:`ModelAnalysis`.
``analyze_air_model(air_model)``
    Compute per-layer MACs from a heliaAOT :class:`AirModel`.
``is_available()``
    True if ``ai-edge-litert`` is installed.
``is_aot_available()``
    True if ``helia-aot`` is installed (for ``analyze_air_model``).
"""

from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("hpx")

# ---------------------------------------------------------------------------
# Availability guard — keep ai-edge-litert / flatbuffers optional
# ---------------------------------------------------------------------------

_HAS_LITERT = False
try:
    from ai_edge_litert import schema_py_generated as _schema  # type: ignore[import-untyped]

    _HAS_LITERT = True
except ImportError:
    _schema = None  # type: ignore[assignment]


def is_available() -> bool:
    """Return True if model analysis dependencies are installed."""
    return _HAS_LITERT


# ---------------------------------------------------------------------------
# Availability guard — keep helia-aot optional (for AirModel analysis)
# ---------------------------------------------------------------------------

_HAS_AOT = False
try:
    from helia_aot.air.model import AirModel as _AirModel  # type: ignore[import-untyped]
    from helia_aot.air.enums import AirOpType as _AirOpType  # type: ignore[import-untyped]

    _HAS_AOT = True
except ImportError:
    _AirModel = None  # type: ignore[assignment,misc]
    _AirOpType = None  # type: ignore[assignment,misc]


def is_aot_available() -> bool:
    """Return True if helia-aot is installed (for AirModel analysis)."""
    return _HAS_AOT


def analyze_for_engine(
    model_path: str | Path,
    *,
    engine: str = "tflite",
    board: str = "apollo510_evb",
) -> ModelAnalysis:
    """Analyze a model as the selected inference engine executes it."""
    from ..engines import EngineType
    from ..errors import ConfigError, EngineError

    path = Path(model_path)
    if not path.is_file():
        raise ConfigError(f"Model file not found: {path}")
    if not is_available():
        raise ConfigError(
            "ai-edge-litert is not installed.",
            hint="Install with: pip install 'helia-profiler[analysis]'",
        )

    engine_type = EngineType(engine)
    if engine_type is not EngineType.HELIA_AOT:
        result = analyze_model(path)
        if result is None:
            raise EngineError(f"Failed to analyze model: {path}")
        return result

    if not is_aot_available():
        raise ConfigError(
            "helia-aot is not installed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        )

    import tempfile

    try:
        from helia_aot.cli.defines import ConvertArgs  # type: ignore[import-untyped]
        from helia_aot.converter import AotConverter  # type: ignore[import-untyped]
        from helia_aot.defines import ModuleType  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ConfigError(
            "helia-aot import failed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        ) from exc

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        convert_args = ConvertArgs(
            model={"path": str(path)},
            module={"path": tmp, "type": ModuleType.nsx.value},
            platform={"name": board},
        )
        try:
            context = AotConverter(config=convert_args).convert()
        except Exception as exc:
            raise EngineError(f"AOT compilation failed: {exc}") from exc

        result = analyze_air_model(context.model)
        if result is None:
            raise EngineError(f"Failed to analyze AOT-transformed model: {path}")
        return result


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

#: Display name of the Vela-generated Ethos-U custom op (see _display_name).
ETHOS_U_OP_NAME = "CUSTOM(ethos-u)"

# --- Vela accelerator-config extraction ------------------------------------
#
# Vela stores the accelerator it compiled for inside the ethos-u custom op's
# payload ("Custom Operator Payload 1"), not in tflite metadata.  Layout, per
# ethos-u-core-driver ethosu_driver.c:
#
#   word 0            : 'COP1' fourcc
#   word 1            : struct cop_data_s — byte 0 is the driver action;
#                       OPTIMIZER_CONFIG == 1
#   word 2 (cfg)      : struct config_r
#   word 3 (id)       : struct id_r
#
# config_r bit layout is identical across the u55/u65/u85 interface headers
# for the two fields we need:
#   bits [3:0]   macs_per_cc — log2(MACs per clock cycle)
#   bits [31:28] product     — 0=U55, 1=U65, 2=U85
#
# The driver compares this whole word against the NPU's own CONFIG register at
# inference time; a mismatch is exactly the u55-model-on-u85-hardware case.
_ETHOS_U_COP_FOURCC = b"COP1"
_ETHOS_U_DRIVER_ACTION_OPTIMIZER_CONFIG = 1
_ETHOS_U_PRODUCTS: dict[int, str] = {0: "u55", 1: "u65", 2: "u85"}


def vela_accelerator_config(model_path: str | Path) -> str | None:
    """Return a Vela-compiled model's accelerator config, e.g. ``ethos-u85-256``.

    Returns ``None`` when the model is not Vela-compiled, when the payload
    cannot be decoded, or when ``ai-edge-litert`` is not installed — callers
    treat "unknown" as "cannot check", never as a mismatch.
    """
    if _schema is None:
        return None
    try:
        buf = Path(model_path).read_bytes()
        model = _schema.Model.GetRootAs(buf, 0)
        for sg_idx in range(model.SubgraphsLength()):
            graph = model.Subgraphs(sg_idx)
            for op_idx in range(graph.OperatorsLength()):
                op = graph.Operators(op_idx)
                custom = model.OperatorCodes(op.OpcodeIndex()).CustomCode()
                if not custom or b"ethos-u" not in custom:
                    continue
                for i in range(op.InputsLength()):
                    tensor_idx = op.Inputs(i)
                    if tensor_idx < 0:
                        continue
                    buffer = model.Buffers(graph.Tensors(tensor_idx).Buffer())
                    if buffer.DataLength() < 16:
                        continue
                    payload = bytes(buffer.DataAsNumpy())
                    cfg = _decode_cop_optimizer_config(payload)
                    if cfg is not None:
                        return cfg
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("Vela accelerator-config extraction failed: %s", exc)
    return None


def _decode_cop_optimizer_config(payload: bytes) -> str | None:
    """Decode ``ethos-u<product>-<macs>`` from a COP1 payload, or None."""
    if len(payload) < 16 or payload[:4] != _ETHOS_U_COP_FOURCC:
        return None
    driver_action = payload[4]
    if driver_action != _ETHOS_U_DRIVER_ACTION_OPTIMIZER_CONFIG:
        return None
    (cfg_word,) = struct.unpack_from("<I", payload, 8)
    product = _ETHOS_U_PRODUCTS.get((cfg_word >> 28) & 0xF)
    macs_per_cc = cfg_word & 0xF
    if product is None or not 0 < macs_per_cc < 24:
        return None
    return f"ethos-{product}-{1 << macs_per_cc}"


@dataclass(frozen=True)
class LayerOps:
    """OPS estimate for a single layer."""

    id: int
    op: str
    macs: int = 0
    """Multiply-accumulate operations (1 MAC = 2 FLOPs)."""
    ops: int = 0
    """Total arithmetic operations (FLOPs). For MAC-dominated layers: ops = 2 * macs."""
    input_shapes: list[list[int]] = field(default_factory=list)
    output_shapes: list[list[int]] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    """Op-specific parameters (stride, kernel, dilation, etc.)."""
    original_id: int | None = None
    """Original tflite operator index.  Set by engine-specific analysers
    (e.g. heliaAOT preserves the original op index through transforms)."""


@dataclass(frozen=True)
class ModelAnalysis:
    """Full model analysis result."""

    layers: list[LayerOps]
    total_macs: int
    total_ops: int
    num_parameters: int
    """Approximate parameter count (weights + biases)."""
    engine: str = "tflite"
    """Engine/interpreter that produced this analysis ('tflite', 'helia-rt', 'helia-aot')."""

    @property
    def ethos_u_op_count(self) -> int:
        """Number of Vela-generated ethos-u custom ops in the graph."""
        return sum(1 for layer in self.layers if layer.op == ETHOS_U_OP_NAME)

    @property
    def has_ethos_u_op(self) -> bool:
        """True when the model was compiled by Vela (contains ethos-u ops)."""
        return self.ethos_u_op_count > 0


# ---------------------------------------------------------------------------
# BuiltinOperator code → human name mapping
# ---------------------------------------------------------------------------

_OP_NAMES: dict[int, str] = {}


def _ensure_op_names() -> None:
    """Lazily populate _OP_NAMES from the schema enum."""
    if _OP_NAMES or _schema is None:
        return
    bo = _schema.BuiltinOperator
    for attr in dir(bo):
        if attr.startswith("_"):
            continue
        val = getattr(bo, attr)
        if isinstance(val, int):
            _OP_NAMES[val] = attr


def _op_name(code: int) -> str:
    _ensure_op_names()
    return _OP_NAMES.get(code, f"CUSTOM({code})")


# ---------------------------------------------------------------------------
# MAC computation per operator type
# ---------------------------------------------------------------------------


def _conv2d_macs(
    input_shape: list[int],
    weight_shape: list[int],
    output_shape: list[int],
    stride_h: int,
    stride_w: int,
    dilation_h: int,
    dilation_w: int,
    has_bias: bool,
) -> int:
    """Compute MACs for CONV_2D.

    Weight shape: [C_out, K_h, K_w, C_in]   (TFLite convention)
    Output shape: [N, H_out, W_out, C_out]   (NHWC)
    MACs = K_h * K_w * C_in * C_out * H_out * W_out * N
    """
    if len(weight_shape) != 4 or len(output_shape) < 3:
        return 0
    c_out, k_h, k_w, c_in = weight_shape
    n = output_shape[0]
    # Output spatial dims — handle both [N,H,W,C] and [N,1,H,C]
    if len(output_shape) == 4:
        h_out = output_shape[1]
        w_out = output_shape[2]
    else:
        h_out = output_shape[1]
        w_out = 1
    return n * k_h * k_w * c_in * c_out * h_out * w_out


def _depthwise_conv2d_macs(
    input_shape: list[int],
    weight_shape: list[int],
    output_shape: list[int],
    depth_multiplier: int,
    has_bias: bool,
) -> int:
    """Compute MACs for DEPTHWISE_CONV_2D.

    Weight shape: [1, K_h, K_w, C_in * depth_multiplier]
    MACs = K_h * K_w * C_in * depth_multiplier * H_out * W_out * N
    """
    if len(weight_shape) != 4 or len(output_shape) < 3:
        return 0
    _, k_h, k_w, _ = weight_shape
    n = output_shape[0]
    if len(input_shape) >= 4:
        c_in = input_shape[3]
    else:
        c_in = input_shape[-1]
    if len(output_shape) == 4:
        h_out = output_shape[1]
        w_out = output_shape[2]
    else:
        h_out = output_shape[1]
        w_out = 1
    return n * k_h * k_w * c_in * depth_multiplier * h_out * w_out


def _fully_connected_macs(
    input_shape: list[int],
    weight_shape: list[int],
    has_bias: bool,
) -> int:
    """Compute MACs for FULLY_CONNECTED.

    Weight shape: [N_out, N_in]
    MACs = N_in * N_out * batch
    """
    if len(weight_shape) != 2:
        return 0
    n_out, n_in = weight_shape
    batch = 1
    for d in input_shape[:-1]:
        batch *= d
    return batch * n_in * n_out


def _transpose_conv_macs(
    weight_shape: list[int],
    output_shape: list[int],
) -> int:
    """Compute MACs for TRANSPOSE_CONV.

    Weight shape: [C_out, K_h, K_w, C_in]
    Output shape: [N, H_out, W_out, C_out]
    Same MAC count as forward conv.
    """
    if len(weight_shape) != 4 or len(output_shape) < 3:
        return 0
    c_out, k_h, k_w, c_in = weight_shape
    n = output_shape[0]
    if len(output_shape) == 4:
        h_out = output_shape[1]
        w_out = output_shape[2]
    else:
        h_out = output_shape[1]
        w_out = 1
    return n * k_h * k_w * c_in * c_out * h_out * w_out


def _elementwise_ops(output_shape: list[int]) -> int:
    """Element-wise ops (ADD, MUL, RELU, LEAKY_RELU, etc.): 1 op per element."""
    return math.prod(output_shape) if output_shape else 0


# ---------------------------------------------------------------------------
# Parameter counting
# ---------------------------------------------------------------------------


def _count_tensor_elements(sg: Any, tensor_idx: int) -> int:
    """Count elements in a tensor by its index."""
    if tensor_idx < 0:
        return 0
    t = sg.Tensors(tensor_idx)
    return math.prod(t.Shape(d) for d in range(t.ShapeLength()))


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------


def analyze_model(model_path: str | Path) -> ModelAnalysis | None:
    """Parse a ``.tflite`` file and return per-layer OPS/MAC counts.

    Returns ``None`` if ``ai-edge-litert`` is not installed.
    """
    if not _HAS_LITERT:
        log.debug("ai-edge-litert not installed — skipping model analysis")
        return None

    path = Path(model_path)
    buf = path.read_bytes()
    model = _schema.Model.GetRootAs(buf, 0)

    if model.SubgraphsLength() == 0:
        log.warning("Model has no subgraphs")
        return None

    sg = model.Subgraphs(0)
    layers: list[LayerOps] = []
    total_params = 0

    # Build opcode → builtin-code lookup
    def _builtin_code(opcode_idx: int) -> int:
        oc = model.OperatorCodes(opcode_idx)
        code = oc.BuiltinCode()
        deprecated = oc.DeprecatedBuiltinCode()
        # Older flatbuffers often leave BuiltinCode() at ADD(0) and store the
        # real builtin enum only in DeprecatedBuiltinCode(). Prefer the
        # deprecated field when BuiltinCode() still carries the placeholder.
        if code == 0 and deprecated != 0:
            return deprecated
        return code

    def _display_name(opcode_idx: int, builtin: int) -> str:
        """Op name for reporting; custom ops carry their custom_code string.

        Vela-compiled models wrap NPU subgraphs in a CUSTOM op whose
        custom_code is ``"ethos-u"`` — surfacing the string (as
        ``CUSTOM(ethos-u)``) lets callers detect Vela models and lets the
        resolver planner register the matching kernel.
        """
        name = _op_name(builtin)
        if name != "CUSTOM":
            return name
        custom = model.OperatorCodes(opcode_idx).CustomCode()
        if custom:
            return f"CUSTOM({custom.decode('utf-8', errors='replace')})"
        return name

    # Element-wise ops that count as 1 op per element
    _ELEMENTWISE_OPS = set()
    bo = _schema.BuiltinOperator
    for name in (
        "RELU",
        "RELU6",
        "RELU_N1_TO_1",
        "LEAKY_RELU",
        "PRELU",
        "ELU",
        "LOGISTIC",
        "TANH",
        "HARD_SWISH",
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "MAXIMUM",
        "MINIMUM",
        "SQUARED_DIFFERENCE",
        "RSQRT",
        "SQRT",
        "ABS",
        "NEG",
        "FLOOR",
        "CEIL",
        "ROUND",
        "LOG",
        "EXP",
        "SIN",
        "COS",
        "QUANTIZE",
        "DEQUANTIZE",
    ):
        val = getattr(bo, name, None)
        if val is not None:
            _ELEMENTWISE_OPS.add(val)

    # Zero-cost ops (no compute)
    _ZERO_OPS = set()
    for name in (
        "RESHAPE",
        "SQUEEZE",
        "EXPAND_DIMS",
        "TRANSPOSE",
        "PAD",
        "PADV2",
        "MIRROR_PAD",
        "CONCATENATION",
        "SPLIT",
        "SPLIT_V",
        "SLICE",
        "STRIDED_SLICE",
        "GATHER",
        "GATHER_ND",
        "CAST",
        "PACK",
        "UNPACK",
    ):
        val = getattr(bo, name, None)
        if val is not None:
            _ZERO_OPS.add(val)

    for i in range(sg.OperatorsLength()):
        op = sg.Operators(i)
        opcode_idx = op.OpcodeIndex()
        builtin = _builtin_code(opcode_idx)
        name = _display_name(opcode_idx, builtin)

        # Collect input/output shapes
        in_shapes: list[list[int]] = []
        for j in range(op.InputsLength()):
            idx = op.Inputs(j)
            if idx >= 0:
                t = sg.Tensors(idx)
                in_shapes.append([t.Shape(d) for d in range(t.ShapeLength())])
            else:
                in_shapes.append([])

        out_shapes: list[list[int]] = []
        for j in range(op.OutputsLength()):
            idx = op.Outputs(j)
            if idx >= 0:
                t = sg.Tensors(idx)
                out_shapes.append([t.Shape(d) for d in range(t.ShapeLength())])
            else:
                out_shapes.append([])

        macs = 0
        ops = 0
        params: dict[str, Any] = {}
        has_bias = op.InputsLength() >= 3 and op.Inputs(2) >= 0

        # ---- CONV_2D ----
        if builtin == bo.CONV_2D and len(in_shapes) >= 2:
            conv_opts = _schema.Conv2DOptions()
            conv_opts.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
            params = {
                "stride_h": conv_opts.StrideH(),
                "stride_w": conv_opts.StrideW(),
                "dilation_h": conv_opts.DilationHFactor(),
                "dilation_w": conv_opts.DilationWFactor(),
                "padding": conv_opts.Padding(),
            }
            macs = _conv2d_macs(
                in_shapes[0],
                in_shapes[1],
                out_shapes[0] if out_shapes else [],
                conv_opts.StrideH(),
                conv_opts.StrideW(),
                conv_opts.DilationHFactor(),
                conv_opts.DilationWFactor(),
                has_bias,
            )
            ops = 2 * macs
            # Count parameters (weights + bias)
            total_params += _count_tensor_elements(sg, op.Inputs(1))
            if has_bias:
                total_params += _count_tensor_elements(sg, op.Inputs(2))

        # ---- DEPTHWISE_CONV_2D ----
        elif builtin == bo.DEPTHWISE_CONV_2D and len(in_shapes) >= 2:
            dw_opts = _schema.DepthwiseConv2DOptions()
            dw_opts.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
            dm = dw_opts.DepthMultiplier()
            params = {
                "stride_h": dw_opts.StrideH(),
                "stride_w": dw_opts.StrideW(),
                "dilation_h": dw_opts.DilationHFactor(),
                "dilation_w": dw_opts.DilationWFactor(),
                "depth_multiplier": dm,
                "padding": dw_opts.Padding(),
            }
            macs = _depthwise_conv2d_macs(
                in_shapes[0],
                in_shapes[1],
                out_shapes[0] if out_shapes else [],
                dm,
                has_bias,
            )
            ops = 2 * macs
            total_params += _count_tensor_elements(sg, op.Inputs(1))
            if has_bias:
                total_params += _count_tensor_elements(sg, op.Inputs(2))

        # ---- FULLY_CONNECTED ----
        elif builtin == bo.FULLY_CONNECTED and len(in_shapes) >= 2:
            macs = _fully_connected_macs(in_shapes[0], in_shapes[1], has_bias)
            ops = 2 * macs
            total_params += _count_tensor_elements(sg, op.Inputs(1))
            if has_bias:
                total_params += _count_tensor_elements(sg, op.Inputs(2))

        # ---- TRANSPOSE_CONV ----
        elif builtin == bo.TRANSPOSE_CONV and len(in_shapes) >= 3:
            tc_opts = _schema.TransposeConvOptions()
            tc_opts.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
            params = {
                "stride_h": tc_opts.StrideH(),
                "stride_w": tc_opts.StrideW(),
                "padding": tc_opts.Padding(),
            }
            # TRANSPOSE_CONV inputs: [output_shape, weights, input]
            weight_idx = 1
            macs = _transpose_conv_macs(
                in_shapes[weight_idx] if len(in_shapes) > weight_idx else [],
                out_shapes[0] if out_shapes else [],
            )
            ops = 2 * macs
            total_params += _count_tensor_elements(sg, op.Inputs(weight_idx))
            if op.InputsLength() >= 4 and op.Inputs(3) >= 0:
                total_params += _count_tensor_elements(sg, op.Inputs(3))

        # ---- AVERAGE_POOL_2D / MAX_POOL_2D ----
        elif builtin in (bo.AVERAGE_POOL_2D, bo.MAX_POOL_2D):
            pool_opts = _schema.Pool2DOptions()
            pool_opts.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
            params = {
                "filter_h": pool_opts.FilterHeight(),
                "filter_w": pool_opts.FilterWidth(),
                "stride_h": pool_opts.StrideH(),
                "stride_w": pool_opts.StrideW(),
                "padding": pool_opts.Padding(),
            }
            # Pool: comparisons/additions per output element = filter_h * filter_w
            if out_shapes:
                out_elems = math.prod(out_shapes[0])
                ops = out_elems * pool_opts.FilterHeight() * pool_opts.FilterWidth()

        # ---- SOFTMAX ----
        elif builtin == bo.SOFTMAX:
            # ~5 ops per element (exp, sum, div, max, sub)
            if out_shapes:
                ops = 5 * math.prod(out_shapes[0])

        # ---- Element-wise ops ----
        elif builtin in _ELEMENTWISE_OPS:
            if out_shapes:
                ops = _elementwise_ops(out_shapes[0])

        # ---- Zero-cost ops ----
        elif builtin in _ZERO_OPS:
            ops = 0

        # ---- Unknown — log but don't crash ----
        else:
            log.debug("model_analysis: unhandled op %s (builtin=%d)", name, builtin)

        layers.append(
            LayerOps(
                id=i,
                op=name,
                macs=macs,
                ops=ops,
                input_shapes=in_shapes,
                output_shapes=out_shapes,
                params=params,
                original_id=i,
            )
        )

    total_macs = sum(l.macs for l in layers)
    total_ops = sum(l.ops for l in layers)

    return ModelAnalysis(
        layers=layers,
        total_macs=total_macs,
        total_ops=total_ops,
        num_parameters=total_params,
        engine="tflite",
    )


# ---------------------------------------------------------------------------
# AirModel analysis (heliaAOT post-transform graph)
# ---------------------------------------------------------------------------


def analyze_air_model(air_model: Any) -> ModelAnalysis | None:
    """Compute per-layer MACs from a heliaAOT :class:`AirModel`.

    The ``AirModel`` represents the *post-transform* graph after AOT
    fusions/optimizations — operator count and shapes may differ from the
    original ``.tflite``.

    Returns ``None`` if ``helia-aot`` is not installed.
    """
    if not _HAS_AOT:
        log.debug("helia-aot not installed — skipping AirModel analysis")
        return None

    layers: list[LayerOps] = []
    total_params = 0

    for i, op in enumerate(air_model.operators):
        op_name = op.op_type.name if hasattr(op.op_type, "name") else str(op.op_type)

        # Collect shapes from input/output tensor IDs
        in_shapes: list[list[int]] = []
        for tid in op.input_ids:
            t = air_model.get_tensor(tid)
            in_shapes.append(list(t.shape) if t is not None else [])

        out_shapes: list[list[int]] = []
        for tid in op.output_ids:
            t = air_model.get_tensor(tid)
            out_shapes.append(list(t.shape) if t is not None else [])

        macs = 0
        ops = 0
        params: dict[str, Any] = {}

        # Get weight tensor shape via named_tensors
        weight_shape: list[int] = []
        bias_shape: list[int] = []
        if "weights" in op.named_tensors:
            wt = air_model.get_tensor(op.named_tensors["weights"])
            if wt is not None:
                weight_shape = list(wt.shape)
                total_params += math.prod(wt.shape)
        if "bias" in op.named_tensors:
            bt = air_model.get_tensor(op.named_tensors["bias"])
            if bt is not None:
                bias_shape = list(bt.shape)
                total_params += math.prod(bt.shape)
        has_bias = len(bias_shape) > 0

        ot = op.op_type

        # ---- CONV_2D ----
        if ot == _AirOpType.CONV_2D and weight_shape and out_shapes:
            opts = op.options
            params = {
                "stride_h": getattr(opts, "stride_height", 1),
                "stride_w": getattr(opts, "stride_width", 1),
                "dilation_h": getattr(opts, "dilation_height", 1),
                "dilation_w": getattr(opts, "dilation_width", 1),
            }
            macs = _conv2d_macs(
                in_shapes[0] if in_shapes else [],
                weight_shape,
                out_shapes[0],
                params["stride_h"],
                params["stride_w"],
                params["dilation_h"],
                params["dilation_w"],
                has_bias,
            )
            ops = 2 * macs

        # ---- DEPTHWISE_CONV_2D ----
        elif ot == _AirOpType.DEPTHWISE_CONV_2D and weight_shape and out_shapes:
            opts = op.options
            dm = getattr(opts, "depth_multiplier", 1)
            params = {
                "stride_h": getattr(opts, "stride_height", 1),
                "stride_w": getattr(opts, "stride_width", 1),
                "dilation_h": getattr(opts, "dilation_height", 1),
                "dilation_w": getattr(opts, "dilation_width", 1),
                "depth_multiplier": dm,
            }
            macs = _depthwise_conv2d_macs(
                in_shapes[0] if in_shapes else [],
                weight_shape,
                out_shapes[0],
                dm,
                has_bias,
            )
            ops = 2 * macs

        # ---- FULLY_CONNECTED ----
        elif ot == _AirOpType.FULLY_CONNECTED and weight_shape:
            macs = _fully_connected_macs(
                in_shapes[0] if in_shapes else [],
                weight_shape,
                has_bias,
            )
            ops = 2 * macs

        # ---- TRANSPOSE_CONV ----
        elif ot == _AirOpType.TRANSPOSE_CONV and weight_shape and out_shapes:
            macs = _transpose_conv_macs(weight_shape, out_shapes[0])
            ops = 2 * macs

        # ---- AVERAGE_POOL_2D / MAX_POOL_2D ----
        elif ot in (_AirOpType.AVERAGE_POOL_2D, _AirOpType.MAX_POOL_2D):
            opts = op.options
            fh = getattr(opts, "filter_height", 1)
            fw = getattr(opts, "filter_width", 1)
            params = {"filter_h": fh, "filter_w": fw}
            if out_shapes:
                ops = math.prod(out_shapes[0]) * fh * fw

        # ---- SOFTMAX ----
        elif ot == _AirOpType.SOFTMAX:
            if out_shapes:
                ops = 5 * math.prod(out_shapes[0])

        # ---- Element-wise ops ----
        elif ot.name in {
            "RELU",
            "RELU6",
            "LEAKY_RELU",
            "PRELU",
            "ELU",
            "LOGISTIC",
            "TANH",
            "HARD_SWISH",
            "ADD",
            "SUB",
            "MUL",
            "DIV",
            "MAXIMUM",
            "MINIMUM",
            "QUANTIZE",
            "DEQUANTIZE",
        }:
            if out_shapes:
                ops = _elementwise_ops(out_shapes[0])

        # ---- Zero-cost / data-movement ops ----
        elif ot.name in {
            "RESHAPE",
            "SQUEEZE",
            "EXPAND_DIMS",
            "TRANSPOSE",
            "PAD",
            "PADV2",
            "CONCATENATION",
            "SPLIT",
            "SPLIT_V",
            "SLICE",
            "STRIDED_SLICE",
            "GATHER",
            "CAST",
            "PACK",
            "UNPACK",
        }:
            pass  # 0 ops

        else:
            log.debug("air_model_analysis: unhandled op %s", op_name)

        layers.append(
            LayerOps(
                id=i,
                op=op_name,
                macs=macs,
                ops=ops,
                input_shapes=in_shapes,
                output_shapes=out_shapes,
                params=params,
                original_id=int(op.id),
            )
        )

    total_macs = sum(l.macs for l in layers)
    total_ops = sum(l.ops for l in layers)

    return ModelAnalysis(
        layers=layers,
        total_macs=total_macs,
        total_ops=total_ops,
        num_parameters=total_params,
        engine="helia-aot",
    )
