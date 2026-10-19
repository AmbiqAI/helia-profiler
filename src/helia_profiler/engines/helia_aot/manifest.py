"""heliaAOT operator manifest and memory-plan extraction from ``ConversionResult`` views.

heliaAOT transforms, fuses and removes operators relative to the original
``.tflite``. Its ``ConversionResult`` views (``operators``, ``tensors``,
``regions``, ``placements``) describe the generated module, so hpx reads the
post-transform graph and arena layout from them rather than parsing C.

The operator manifest stays a list of plain dicts: ``report/aot.py`` dumps it
generically and ``firmware/context.py``'s ``AotOpContext`` reads only ``id``
and ``op_type``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ...errors import EngineError
from ...placement import ArenaRole, MemoryRegion, Placement
from ...results import ConsumerKind, MemoryConsumer, MemoryPlan, MemoryRegionUsage
from .. import EngineType
from ..base import ArenaRegion
from .compile import _AOT_MEMORY_ALIASES, _PLACEMENT_TO_AOT_MEMTYPE

log = logging.getLogger("hpx")

# Map AOT planner physical memory names → logical placement names used
# by firmware templates and the rest of the profiler pipeline.  Keeps the
# template conditionals simple and avoids brittle string mismatches
# (e.g. "dtcm" vs "tcm"). Mechanically derived from _PLACEMENT_TO_AOT_MEMTYPE
# (the canonical Placement -> string mapping) plus _AOT_MEMORY_ALIASES, so the
# two directions cannot silently drift out of sync with each other.
_AOT_MEMORY_TO_PLACEMENT: dict[str, Placement] = {
    **{mem_str: placement for placement, mem_str in _PLACEMENT_TO_AOT_MEMTYPE.items()},
    **_AOT_MEMORY_ALIASES,
}


def _name(value: Any) -> str:
    """An enum's value, or a plain string, lowercased."""
    return str(getattr(value, "value", value)).lower()


def _tensor_metadata(tensor: Any, placement: Any, regions: Mapping[int, Any]) -> dict[str, Any]:
    """A JSON-serialisable summary of one ``TensorView`` and its ``PlacementView``."""
    meta: dict[str, Any] = {"id": tensor.id}
    if tensor.name is not None:
        meta["name"] = tensor.name
    meta.update(
        dtype=tensor.dtype,
        ctype=tensor.ctype,
        kind=_name(tensor.kind),
        nbytes=tensor.nbytes,
        shape=list(tensor.shape),
    )
    if placement is None or placement.region is None:
        return meta
    region = regions[placement.region]
    meta.update(
        memory=_name(region.memory),
        source_memory=_name(placement.source_memory),
        arena_role=_name(region.role),
        offset=placement.offset,
        allocation_size=placement.size,
        arena_region_id=placement.region,
        staged=placement.staged,
    )
    return meta


def _extract_arena_regions(result: Any, prefix: str) -> list[ArenaRegion]:
    """One :class:`ArenaRegion` per region, ordered by region id.

    The firmware template emits ``bind_arena()`` calls from these in
    external-arena mode (``allocate_arenas=false``).
    """
    regions: list[ArenaRegion] = []
    for region in result.regions:
        mem_str = _name(region.memory)
        placement = _AOT_MEMORY_TO_PLACEMENT.get(mem_str)
        if placement is None:
            raise EngineError(f"AOT arena {region.id} has unsupported physical memory {mem_str!r}")
        try:
            role = ArenaRole(_name(region.role))
        except ValueError as exc:
            raise EngineError(
                f"AOT arena {region.id} has unsupported role {region.role!r}"
            ) from exc
        regions.append(
            ArenaRegion(
                region_id=region.id,
                name=f"{prefix}_arena_{mem_str}",
                size=region.size,
                alignment=region.alignment,
                role=role,
                memory=mem_str,
                placement=placement,
                blob_filename=region.sidecar_file,
            )
        )
    return regions


def _extract_operator_manifest(result: Any, prefix: str) -> list[dict[str, Any]]:
    """The operator manifest, one entry per generated operator in execution order.

    Each entry::

        {
            "idx": 0, "id": 0, "source_indices": [0], "op_type": "CONV_2D",
            "name": "conv_2d_0", "kernel_calls": {"run": "arm_convolve_s8"},
            "inputs": [...], "outputs": [...], "local_tensors": [...],
        }

    - ``idx`` — execution position, the generated callback's ``op`` argument
      and the firmware CSV "Layer"
    - ``id`` — the operator's ``.tflite`` index, or ``None`` when it has none
      or several (a transform created it, or a hook fused several)
    - ``source_indices`` — every ``.tflite`` index the operator came from
    - ``name`` — the operator's generated symbol without the module prefix
    - ``kernel_calls`` — CORE entries the generated source calls, keyed by
      role; ``{}`` calls none, ``None`` is unrecorded (see :func:`_kernel_calls`)
    """
    tensors = {tensor.id: tensor for tensor in result.tensors}
    regions = {region.id: region for region in result.regions}
    placements = result.placements

    def describe(ids: tuple[str, ...]) -> list[dict[str, Any]]:
        return [_tensor_metadata(tensors[tid], placements.get(tid), regions) for tid in ids]

    manifest: list[dict[str, Any]] = []
    for op in result.operators:
        sources = list(op.source_indices)
        entry: dict[str, Any] = {
            "idx": op.layer_index,
            "id": sources[0] if len(sources) == 1 else None,
            "source_indices": sources,
            "op_type": op.type,
            "name": op.symbol.removeprefix(f"{prefix}_"),
            "kernel_calls": _kernel_calls(op),
            "inputs": describe(op.input_ids),
            "outputs": describe(op.output_ids),
        }
        if op.local_tensor_ids:
            entry["local_tensors"] = describe(op.local_tensor_ids)
        manifest.append(entry)
    return manifest


def _kernel_calls(op: Any) -> dict[str, str] | None:
    """The CORE entries the generated source calls directly, keyed by role.

    This is the compiler's own record of the selected route; an ``init`` role
    runs in the operator's initialisation, outside the measured window. ``None``
    is unrecorded: heliaAOT does not record calls for this operator, or reported
    something malformed. A symbol linked into the image is not a call.
    """
    calls = op.kernel_calls
    if calls is None:
        return None
    if not all(_is_call_role(role) and _is_call_symbol(symbol) for role, symbol in calls.items()):
        log.warning("heliaAOT reported malformed CORE calls for %s", op.symbol)
        return None
    return dict(calls)


def _is_call_role(role: object) -> bool:
    return isinstance(role, str) and bool(role) and not set(role) & {";", "="}


def _is_call_symbol(symbol: object) -> bool:
    return isinstance(symbol, str) and symbol.isidentifier()


def _extract_memory_plan(
    result: Any, prefix: str = "hpx", *, allocate_arenas: bool = True
) -> MemoryPlan | None:
    """Build a ``MemoryPlan`` from the result's arena regions.

    Generated C allocates one buffer per region; tensor placements share those
    buffers and are never summed as independent RAM. Capacities are left 0:
    the ``plan_memory`` stage fills them from the SoC layout.

    Returns ``None`` when a region names a memory hpx does not model, rather
    than presenting a partial accounting table.
    """
    buckets: dict[str, list[MemoryConsumer]] = {}
    total_weights = 0

    for region in result.regions:
        if region.size <= 0:
            continue
        runtime_key = _aot_memory_region_key(_name(region.memory))
        source_key = _aot_memory_region_key(_name(region.source_memory))
        if runtime_key is None or source_key is None:
            log.warning("AOT memory plan unavailable: unsupported memory mapping")
            return None
        role = _name(region.role)
        constant = role == ArenaRole.CONSTANT.value
        symbol = region.symbol
        if not allocate_arenas and not constant:
            # hpx's main_aot.cc.j2 owns external scratch/persistent storage (#179).
            symbol = f"hpx_arena_buf_storage_{region.id}"
        buckets.setdefault(runtime_key, []).append(
            MemoryConsumer(
                name=f"{runtime_key.lower()}_{role}_arena_{region.id}",
                size=region.size,
                kind=ConsumerKind.WEIGHTS if constant else ConsumerKind.ARENA,
                symbol=symbol,
            )
        )
        if constant:
            total_weights += region.size
            if source_key != runtime_key:
                buckets.setdefault(source_key, []).append(
                    MemoryConsumer(
                        name=f"{source_key.lower()}_{role}_source_{region.id}",
                        size=region.size,
                        kind=ConsumerKind.WEIGHTS,
                        symbol=region.source_symbol,
                    )
                )

    if not buckets:
        return None

    ordered_keys = ["MRAM", "SRAM", "DTCM", "ITCM", "PSRAM"]
    keys = [key for key in ordered_keys if key in buckets]
    keys.extend(sorted(set(buckets) - set(keys)))

    regions = tuple(
        MemoryRegionUsage(
            region=MemoryRegion(key),
            capacity=0,
            used=sum(c.size for c in buckets[key]),
            consumers=tuple(buckets[key]),
        )
        for key in keys
    )

    return MemoryPlan(
        engine=EngineType.HELIA_AOT,
        regions=regions,
        model_weight_bytes=total_weights,
        has_overflow=any(r.overflow for r in regions),
    )


def _aot_memory_region_key(memory: Any) -> str | None:
    if memory is None:
        return None
    key = str(memory).upper()
    if key == "TCM":
        return "DTCM"
    if key == "DRAM":
        return "SRAM"
    if key in {"DTCM", "ITCM", "SRAM", "MRAM", "PSRAM"}:
        return key
    log.warning("AOT planner emitted unrecognised memory %r — skipping", memory)
    return None
