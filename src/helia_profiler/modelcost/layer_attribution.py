"""Join measured layers to model-analysis layers by ORIGINAL operator index.

The defect this module exists to prevent (#218): ``model_analysis.layers``
describes the ORIGINAL tflite graph, while an AOT firmware reports layers by
their POST-COMPILATION execution position — helia-aot skips operators
(``VAR_HANDLE``, ``CALL_ONCE``, ...) without renumbering, so the two index
spaces drift apart at the first skipped op and a positional join attributes
every later MAC count to the wrong operator, in-range and plausible-looking.

The join key is the original tflite operator index, resolved in order of
authority:

1. the AOT operator manifest (``idx`` = execution position -> ``id`` =
   original index), when the run has one;
2. the integer ``:N`` suffix helia-aot firmware appends to the op label
   (``"FULLY_CONNECTED:43"``) — the artifact-replay fallback;
3. for engines whose execution order IS the original graph order (TFLM,
   helia-rt: plain op labels, no manifest), the layer id itself.

A layer whose source cannot be resolved gets NO attribution — an honest
dash, never a positional guess (the #206 principle).

CONTAINMENT NOTE: this module is deliberately self-contained — pure
functions over plain data, siblings only. Together with ``model_analysis``
it forms the layer-cost core packaged as :mod:`helia_profiler.modelcost`;
keep pipeline, config, and I/O imports out of it (contract-tested).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .model_analysis import LayerOps, ModelAnalysis


__all__ = [
    "LayerAttribution",
    "LayerAttributor",
    "manifest_source_map",
    "source_index_from_op",
]


def source_index_from_op(op: str) -> int | None:
    """Original tflite operator index from an op label's ``:N`` suffix.

    helia-aot firmware labels layers ``"<TYPE>:<original tflite index>"``
    (``"FULLY_CONNECTED:43"``). Only a strict integer suffix counts —
    ExecuTorch's ``"OPERATOR_CALL:c3i12"`` names no tflite operator and
    must stay ``None`` (#218: never guess a source index).
    """
    _, sep, suffix = str(op).rpartition(":")
    if not sep or not suffix.isdigit():
        return None
    return int(suffix)


@dataclass(frozen=True)
class LayerAttribution:
    """Resolved analysis facts for one measured layer.

    ``explicit`` records that ``source_index`` came from an authoritative
    carrier (manifest or op-label suffix) rather than the sequential-engine
    identity fallback — only explicit sources are worth persisting into
    artifacts, where an implicit one would merely duplicate the id column.
    """

    source_index: int | None
    explicit: bool
    macs: int | None = None
    ops: int | None = None


def manifest_source_map(manifest: Sequence[Any]) -> dict[int, int]:
    """Execution position -> original tflite index, from the AOT op manifest.

    Tolerant of malformed entries (skipped, never guessed): the manifest is
    an artifact that may come from other tool versions.
    """
    mapping: dict[int, int] = {}
    for entry in manifest:
        if not isinstance(entry, Mapping):
            continue
        idx = entry.get("idx")
        source = entry.get("id")
        if isinstance(idx, bool) or isinstance(source, bool):
            continue
        if isinstance(idx, int) and isinstance(source, int):
            mapping.setdefault(idx, source)
    return mapping


def _analysis_by_source(analysis: ModelAnalysis) -> dict[int, LayerOps]:
    """Analysis layers keyed by original tflite index.

    Engine-specific analysers set ``LayerOps.original_id``; the generic
    tflite analyser numbers ``id`` by original graph position, so it is the
    same key. First entry wins on a (malformed) duplicate.
    """
    by_source: dict[int, LayerOps] = {}
    for layer in analysis.layers:
        key = layer.original_id if layer.original_id is not None else layer.id
        by_source.setdefault(key, layer)
    return by_source


class LayerAttributor:
    """Resolves per-layer analysis attribution for one run's layers."""

    def __init__(
        self,
        analysis: ModelAnalysis | None,
        aot_op_manifest: Sequence[Any] | None = None,
    ) -> None:
        self._by_source = _analysis_by_source(analysis) if analysis is not None else {}
        self._source_map = (
            manifest_source_map(aot_op_manifest) if aot_op_manifest is not None else None
        )

    def attribute(
        self, layer_id: int | str, op: str, source_index: int | None = None
    ) -> LayerAttribution:
        """``source_index`` is a caller-carried original index (e.g.
        ``LayerResult.source_index``); it outranks re-parsing the label but
        never the manifest."""
        position = (
            layer_id if isinstance(layer_id, int) and not isinstance(layer_id, bool) else None
        )
        suffix = source_index if source_index is not None else source_index_from_op(op)
        explicit = True
        if self._source_map is not None:
            # The manifest is authoritative for the whole run: a position it
            # does not name stays unresolved even if a label suffix exists —
            # a disagreement between the two is not a licence to guess.
            source = self._source_map.get(position) if position is not None else None
        elif suffix is not None:
            source = suffix
        elif ":" not in str(op):
            # Sequential engines (TFLM, helia-rt): execution order is the
            # original graph order, so the id IS the source index.
            source = position
            explicit = False
        else:
            # A non-integer suffix (e.g. ExecuTorch "OPERATOR_CALL:c3i12")
            # names no tflite operator.
            source = None
        found = self._by_source.get(source) if source is not None else None
        return LayerAttribution(
            source_index=source,
            explicit=explicit,
            macs=found.macs if found is not None else None,
            ops=found.ops if found is not None else None,
        )
