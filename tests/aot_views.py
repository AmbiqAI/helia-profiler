"""Stand-ins for heliaAOT's ``ConversionResult`` views.

The unit matrix installs no extras, so tests build these instead of
``helia_aot.views`` objects; ``test_aot_views_match_helia_aot`` keeps their
fields in step with the installed heliaAOT.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OperatorView:
    layer_index: int
    symbol: str
    type: str
    source_indices: tuple[int, ...]
    input_ids: tuple[str, ...]
    output_ids: tuple[str, ...]
    local_tensor_ids: tuple[str, ...] = ()
    kernel_calls: Mapping[str, str] | None = None
    macs: int | None = None
    parameters: int | None = None


@dataclass(frozen=True)
class QuantizationView:
    scale: tuple[float, ...]
    zero_point: tuple[int, ...]
    axis: int = 0


@dataclass(frozen=True)
class TensorView:
    id: str
    name: str | None
    shape: tuple[int, ...]
    dtype: str = "int8"
    ctype: str = "int8_t"
    quantization: QuantizationView | None = None
    kind: str = "scratch"
    input_index: int | None = None
    output_index: int | None = None
    nbytes: int = 0


@dataclass(frozen=True)
class RegionView:
    id: int
    memory: str
    source_memory: str
    role: str
    offset: int = 0
    size: int = 0
    alignment: int = 16
    symbol: str | None = None
    source_symbol: str | None = None
    source_offset: int = 0
    sidecar_file: str | None = None


@dataclass(frozen=True)
class PlacementView:
    region: int | None
    offset: int
    size: int
    source_memory: str
    staged: bool = False


@dataclass(frozen=True)
class Result:
    """The view fields of a ``ConversionResult``."""

    operators: tuple[OperatorView, ...] = ()
    tensors: tuple[TensorView, ...] = ()
    regions: tuple[RegionView, ...] = ()
    placements: Mapping[str, PlacementView] = field(default_factory=dict)


def operator(
    index: int,
    op_type: str,
    inputs: tuple[str, ...] = (),
    outputs: tuple[str, ...] = (),
    *,
    sources: tuple[int, ...] | None = None,
    prefix: str = "hpx",
    **fields: object,
) -> OperatorView:
    """An operator parsed from ``.tflite`` index *index* unless *sources* says otherwise."""
    return OperatorView(
        layer_index=index,
        symbol=f"{prefix}_{op_type.lower()}_{index}",
        type=op_type,
        source_indices=(index,) if sources is None else sources,
        input_ids=inputs,
        output_ids=outputs,
        **fields,  # ty: ignore[invalid-argument-type]
    )
