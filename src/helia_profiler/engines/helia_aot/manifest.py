"""heliaAOT operator manifest and memory-plan extraction (from CodeGenContext).

heliaAOT transforms/fuses/removes operators compared to the original TFLite
flatbuffer; this module extracts the post-transform operator graph and
concrete arena layout directly from the AOT compiler's ``CodeGenContext``
(``codegen_ctx.operators`` / ``codegen_ctx.render_plan``) rather than
parsing generated C source.

The extracted operator manifest is intentionally a list of loosely-typed
dicts (``list[dict[str, Any]]``) rather than a frozen dataclass: heliaAOT's
per-tensor metadata is dynamic (older/newer heliaAOT versions expose
different optional fields — see ``_tensor_metadata``'s ``getattr(..., None)``
probing), and ``report/aot.py`` consumes the manifest generically (JSON dump,
plus an ad-hoc CSV flattening keyed by whichever fields happen to be
present). Typing this at the source would require mirroring heliaAOT's own
evolving tensor-attribute surface in HPX; the loose dict contract is the
documented exception mirroring ``LayerResult.counters: dict[str, float]``
(see ``firmware/context.py``'s ``AotOpContext``, which extracts only the
two fields — ``id`` and ``op_type`` — actually needed by firmware
templates).
"""

from __future__ import annotations

import logging
from typing import Any

from ...placement import ArenaRole, Placement
from ...results import MemoryConsumer, MemoryPlan, MemoryRegionUsage
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


def _tensor_metadata(
    tensor: Any,
    allocations: dict[str, Any] | None = None,
    arena_region_ids: dict[tuple[str, str, str], int] | None = None,
) -> dict[str, Any]:
    """Extract a JSON-serialisable summary from an ``AirTensor``.

    Returns only the fields useful for post-run analysis; silently omits
    any attribute that the tensor does not expose (older heliaAOT
    versions may not expose every field).
    """
    meta: dict[str, Any] = {}
    for key in ("name", "dtype", "ctype", "kind"):
        val = getattr(tensor, key, None)
        if val is not None:
            meta[key] = str(val)
    for key in ("id", "nbytes", "size", "ndim", "buffer_index"):
        val = getattr(tensor, key, None)
        if isinstance(val, (int, float)):
            meta[key] = int(val)
    shape = getattr(tensor, "shape", None)
    if shape is not None:
        try:
            meta["shape"] = [int(d) for d in shape]
        except (TypeError, ValueError):
            pass
    for flag in ("is_constant", "is_persistent", "is_scratch"):
        val = getattr(tensor, flag, None)
        if isinstance(val, bool):
            meta[flag] = val
    if allocations:
        tensor_keys = [
            str(v)
            for v in (
                getattr(tensor, "id", None),
                getattr(tensor, "name", None),
                meta.get("id"),
                meta.get("name"),
            )
            if v is not None
        ]
        alloc = next((allocations[k] for k in tensor_keys if k in allocations), None)
        if alloc is not None:
            binding = getattr(alloc, "binding", None)
            memory = getattr(binding, "memory", getattr(alloc, "memory", None))
            source_memory = getattr(binding, "source_memory", None)
            role = getattr(binding, "role", None)
            offset = getattr(binding, "offset", getattr(alloc, "offset", None))
            size = getattr(alloc, "size", None)

            if memory is not None:
                meta["memory"] = str(memory).lower()
            if source_memory is not None:
                meta["source_memory"] = str(source_memory).lower()
            if role is not None:
                meta["arena_role"] = str(role).lower()
            if isinstance(offset, (int, float)):
                meta["offset"] = int(offset)
            if isinstance(size, (int, float)):
                meta["allocation_size"] = int(size)

            if memory is not None and role is not None:
                key = (
                    str(role).lower(),
                    str(memory).lower(),
                    str(source_memory or memory).lower(),
                )
                if arena_region_ids and key in arena_region_ids:
                    meta["arena_region_id"] = arena_region_ids[key]
            source = meta.get("source_memory")
            runtime = meta.get("memory")
            if source is not None and runtime is not None:
                meta["staged"] = source != runtime
    return meta


def _extract_arena_regions(codegen_ctx: Any, prefix: str) -> list[ArenaRegion]:
    """Extract arena region info from the CodeGenContext's render_plan.

    Returns a list of :class:`ArenaRegion` instances — one per AOT
    scratch / persistent / constant arena.  Used by the firmware
    template to emit ``bind_arena()`` calls in external-arena mode
    (``allocate_arenas=false``).
    """
    render_plan = getattr(codegen_ctx, "render_plan", None)
    if render_plan is None:
        return []

    # Build a lookup from constant arena memory → sidecar blob filename.
    # constant_blobs is ordered to match constant_arenas.
    const_blob_filenames: dict[int, str] = {}
    constant_blobs = getattr(render_plan, "constant_blobs", ())
    constant_arenas = getattr(render_plan, "constant_arenas", ())
    for arena, blob in zip(constant_arenas, constant_blobs):
        const_blob_filenames[arena.region_id] = blob.sidecar_filename

    regions: list[ArenaRegion] = []
    for arena_list in (
        render_plan.scratch_arenas,
        render_plan.persistent_arenas,
        render_plan.constant_arenas,
    ):
        for arena in arena_list:
            mem_str = str(arena.memory).lower()
            placement = _AOT_MEMORY_TO_PLACEMENT.get(mem_str)
            if placement is None:
                # Unknown physical memory — skip rather than silently
                # mis-placing the buffer.  Surfaces upstream as an
                # arena binding gap during firmware build.
                log.warning(
                    "AOT planner emitted unrecognised memory %r — skipping arena %d",
                    mem_str,
                    arena.region_id,
                )
                continue
            try:
                role = ArenaRole(str(arena.role).lower())
            except ValueError:
                log.warning(
                    "AOT planner emitted unrecognised arena role %r — defaulting to scratch",
                    arena.role,
                )
                role = ArenaRole.SCRATCH
            name = f"{prefix}_arena_{mem_str}"
            blob_fn = const_blob_filenames.get(arena.region_id)
            regions.append(
                ArenaRegion(
                    region_id=arena.region_id,
                    name=name,
                    enum_name=name,
                    size=int(arena.size),
                    alignment=int(arena.alignment),
                    role=role,
                    memory=mem_str,
                    placement=placement,
                    blob_filename=blob_fn,
                )
            )

    # Sort by region_id to match the generated enum ordering
    regions.sort(key=lambda r: r.region_id)
    return regions


def _extract_operator_manifest(
    codegen_ctx: Any,
) -> list[dict[str, Any]]:
    """Build the operator manifest from the ``CodeGenContext``.

    heliaAOT may transform, fuse, or remove operators compared to the
    original TFLite flatbuffer.  ``codegen_ctx.operators`` is the
    authoritative post-transform list of ``AotOperator`` objects — each
    with a stable ``.TYPE`` (``AirOpType``) and ``.id`` (original TFLite
    operator index, preserved through transforms).

    Returns a list of dicts ordered by execution sequence::

        [
            {
                "idx": 0, "id": 0, "op_type": "CONV_2D", "name": "conv_2d_0",
                "inputs": [{"name": "x", "shape": [1, 49, 10, 1], ...}],
                "outputs": [{"name": "y", "shape": [1, 25, 5, 8], ...}],
            },
            ...
        ]

    Where:
    - ``idx``     — sequential execution index (matches firmware CSV "Layer")
    - ``id``      — AIR operator ID passed to the callback
    - ``op_type`` — operator type string (from ``AirOpType``)
    - ``name``    — full operator name as emitted by heliaAOT
    - ``inputs``  — list of input tensor metadata (shape/dtype/size)
    - ``outputs`` — list of output tensor metadata
    """
    operators = getattr(codegen_ctx, "operators", None)
    if not operators:
        return []

    memory_plan = getattr(codegen_ctx, "memory_plan", None)
    allocations = getattr(memory_plan, "tensor_allocs", None) or {}
    arena_region_ids = _arena_region_id_lookup(codegen_ctx)

    manifest: list[dict[str, Any]] = []
    for idx, aot_op in enumerate(operators):
        entry: dict[str, Any] = {
            "idx": idx,
            "id": int(aot_op.id),
            "op_type": str(aot_op.TYPE),
            "name": aot_op.name,
        }
        try:
            entry["inputs"] = [
                _tensor_metadata(t, allocations, arena_region_ids)
                for t in (aot_op.input_tensors or [])
            ]
        except Exception:  # noqa: BLE001 — defensive for older heliaAOT
            pass
        try:
            entry["outputs"] = [
                _tensor_metadata(t, allocations, arena_region_ids)
                for t in (aot_op.output_tensors or [])
            ]
        except Exception:  # noqa: BLE001
            pass
        try:
            local_tensors = [_tensor_metadata(t, allocations, arena_region_ids) for t in (aot_op.local_tensors or [])]
            if local_tensors:
                entry["local_tensors"] = local_tensors
        except Exception:  # noqa: BLE001
            pass
        manifest.append(entry)
    return manifest


def _arena_region_id_lookup(codegen_ctx: Any) -> dict[tuple[str, str, str], int]:
    """Return ``(role, runtime_memory, source_memory) → region_id``."""

    render_plan = getattr(codegen_ctx, "render_plan", None)
    if render_plan is None:
        return {}
    lookup: dict[tuple[str, str, str], int] = {}
    for arena_list in (
        getattr(render_plan, "scratch_arenas", ()),
        getattr(render_plan, "persistent_arenas", ()),
        getattr(render_plan, "constant_arenas", ()),
    ):
        for arena in arena_list:
            role = str(getattr(arena, "role", "")).lower()
            memory = str(getattr(arena, "memory", "")).lower()
            raw_source_memory = getattr(arena, "source_memory", None)
            source_memory = str(raw_source_memory if raw_source_memory is not None else getattr(arena, "memory", "")).lower()
            region_id = getattr(arena, "region_id", None)
            if role and memory and source_memory and isinstance(region_id, (int, float)):
                lookup[(role, memory, source_memory)] = int(region_id)
    return lookup


# ---------------------------------------------------------------------------
# Memory-plan extraction (from CodeGenContext)
# ---------------------------------------------------------------------------


def _extract_memory_plan(codegen_ctx: Any) -> MemoryPlan | None:
    """Build a ``MemoryPlan`` from the heliaAOT ``CodeGenContext``.

    The AOT render plan is the source of truth for runtime memory: generated C
    allocates one buffer per scratch / persistent / constant arena.  The lower
    level ``memory_plan.tensor_allocs`` entries describe tensor assignments into
    those shared arenas and must not be summed as independent RAM usage.

    Returns ``None`` when the context does not expose both a memory plan and a
    render plan.  In that case HPX should avoid presenting a precise AOT memory
    accounting table rather than falling back to misleading tensor sums.
    """
    aot_plan = getattr(codegen_ctx, "memory_plan", None)
    render_plan = getattr(codegen_ctx, "render_plan", None)
    if aot_plan is None or render_plan is None:
        return None

    arena_usages = getattr(aot_plan, "arena_usages", None) or {}
    return _extract_memory_plan_from_render_plan(
        render_plan,
        arena_usages,
    )


def _extract_memory_plan_from_render_plan(
    render_plan: Any,
    arena_usages: dict[Any, Any],
) -> MemoryPlan | None:
    """Build a MemoryPlan from the AOT render plan's concrete arenas.

    ``memory_plan.tensor_allocs`` lists every tensor assignment, including
    transient tensors that share arena storage.  Summing those records inflates
    runtime RAM.  The render plan is the source of truth for what generated C
    actually allocates: one buffer per scratch/persistent/constant arena.
    """

    buckets: dict[str, list[MemoryConsumer]] = {}
    total_weights = 0

    for arena_list_name in (
        "scratch_arenas",
        "persistent_arenas",
        "constant_arenas",
    ):
        for arena in getattr(render_plan, arena_list_name, ()):
            runtime_key = _aot_memory_region_key(getattr(arena, "memory", None))
            if runtime_key is None:
                continue

            size = int(getattr(arena, "size", 0))
            if size <= 0:
                continue

            role = str(getattr(arena, "role", arena_list_name.removesuffix("_arenas"))).lower()
            region_id = int(getattr(arena, "region_id", len(buckets)))
            kind = "weights" if role == ArenaRole.CONSTANT.value else "arena"
            buckets.setdefault(runtime_key, []).append(
                MemoryConsumer(
                    name=f"{runtime_key.lower()}_{role}_arena_{region_id}",
                    size=size,
                    kind=kind,
                )
            )
            if role == ArenaRole.CONSTANT.value:
                total_weights += size
                source_key = _aot_memory_region_key(
                    getattr(arena, "source_memory", None)
                )
                if source_key is not None and source_key != runtime_key:
                    buckets.setdefault(source_key, []).append(
                        MemoryConsumer(
                            name=f"{source_key.lower()}_{role}_source_{region_id}",
                            size=size,
                            kind="weights",
                        )
                    )

    if not buckets:
        return None

    capacities = {
        key: int(getattr(usage, "total_size", 0))
        for mem_type, usage in arena_usages.items()
        if (key := _aot_memory_region_key(mem_type)) is not None
    }
    ordered_keys = ["MRAM", "SRAM", "DTCM", "ITCM", "PSRAM"]
    keys = [key for key in ordered_keys if key in buckets or key in capacities]
    keys.extend(sorted((set(buckets) | set(capacities)) - set(keys)))

    regions = tuple(
        MemoryRegionUsage(
            region=key,
            capacity=capacities.get(key, 0),
            used=sum(c.size for c in buckets.get(key, ())),
            consumers=tuple(buckets.get(key, ())),
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
