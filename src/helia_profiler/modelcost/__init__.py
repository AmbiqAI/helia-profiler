"""Model-cost core: rough FLOPS/MACs/memory estimation and layer attribution.

The contained, extractable heart of "what does this network cost" (#229 D4):

* :mod:`.model_analysis` — per-op MAC/OPS formulas over litert or AIR
  graphs, degrading to ``None`` when the optional readers are absent.
* :mod:`.layer_attribution` — joins measured layers to analysis layers by
  ORIGINAL operator index (#218), never by position.
* :mod:`.softmax_preflight` — the no-extras int8/uint8 softmax scale check,
  built on the dependency-free :mod:`._tflite_reader`.

Deliberately free of engine, config, pipeline, and I/O imports — the
boundary is contract-tested, and engine dispatch lives in
:mod:`helia_profiler.evaluation.engine_analysis`.
"""

from .layer_attribution import (
    LayerAttribution,
    LayerAttributor,
    manifest_source_map,
    source_index_from_op,
)
from .model_analysis import (
    LayerOps,
    ModelAnalysis,
    analyze_air_model,
    analyze_model,
    is_aot_available,
    is_available,
)
from .softmax_preflight import aot_softmax_verdict, scan_softmax_scaling

__all__ = [
    "LayerAttribution",
    "LayerAttributor",
    "LayerOps",
    "ModelAnalysis",
    "analyze_air_model",
    "analyze_model",
    "aot_softmax_verdict",
    "is_aot_available",
    "is_available",
    "manifest_source_map",
    "scan_softmax_scaling",
    "source_index_from_op",
]
