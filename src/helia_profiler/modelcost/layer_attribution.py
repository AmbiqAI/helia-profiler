"""Attribute costs by the analysis index space; retain original IDs as provenance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .model_analysis import LayerOps, ModelAnalysis

__all__ = [
    "LayerAttribution",
    "LayerAttributor",
    "manifest_kernel_calls",
    "manifest_source_map",
    "source_index_from_op",
]


def source_index_from_op(op: str) -> int | None:
    """Original tflite operator index from an op label's ``:N`` suffix.

    helia-aot firmware labels layers ``"<TYPE>:<original tflite index>"``
    (``"FULLY_CONNECTED:43"``). Only a strict integer suffix counts —
    ExecuTorch's chain/instruction suffix (``"aten::add.out:c3i12"``, or
    ``"OPERATOR_CALL:c3i12"`` in older artifacts) names no tflite operator
    and must stay ``None`` (#218: never guess a source index).
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
    #: CORE entries the compiler recorded the operator calling: ``()`` for none,
    #: ``None`` when unrecorded or when no compiler record exists for the run.
    kernel_calls: tuple[str, ...] | None = None


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


def manifest_kernel_calls(manifest: Sequence[Any]) -> dict[int, tuple[str, ...]]:
    """Execution position -> distinct CORE call symbols, for recorded entries only.

    An entry whose ``kernel_calls`` is absent, ``None`` or malformed is not a
    recorded fact and stays out of the map; ``{}`` records that the operator
    calls no CORE entry.
    """
    calls: dict[int, tuple[str, ...]] = {}
    for entry in manifest:
        if not isinstance(entry, Mapping):
            continue
        idx, recorded = entry.get("idx"), entry.get("kernel_calls")
        if (
            isinstance(idx, bool)
            or not isinstance(idx, int)
            or not isinstance(recorded, Mapping)
            or not all(isinstance(symbol, str) for symbol in recorded.values())
        ):
            continue
        calls.setdefault(idx, tuple(sorted(set(recorded.values()))))
    return calls


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
        self._by_position = (
            {layer.id: layer for layer in analysis.layers}
            if analysis is not None and analysis.index_space == "execution"
            else None
        )
        self._by_source = _analysis_by_source(analysis) if analysis is not None else {}
        self._source_map = (
            manifest_source_map(aot_op_manifest) if aot_op_manifest is not None else None
        )
        self._kernel_calls = (
            manifest_kernel_calls(aot_op_manifest) if aot_op_manifest is not None else {}
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
            # A non-integer suffix (e.g. ExecuTorch "aten::add.out:c3i12")
            # names no tflite operator.
            source = None
        if self._by_position is not None:
            found = self._by_position.get(position) if position is not None else None
        else:
            found = self._by_source.get(source) if source is not None else None
        return LayerAttribution(
            source_index=source,
            explicit=explicit,
            macs=found.macs if found is not None else None,
            ops=found.ops if found is not None else None,
            kernel_calls=self._kernel_calls.get(position) if position is not None else None,
        )
