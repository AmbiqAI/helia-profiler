"""Decode image-bound diagnostic stack observations, without inferring heap use."""

import struct

from .image import require


def decode_ram(data: bytes, stack_bounds: tuple[int, int] | None) -> dict[str, object]:
    require(len(data) == 32 and stack_bounds is not None, "Missing stack diagnostic evidence")
    words = struct.unpack("<8I", data)
    magic, version, low, top, painted_end, used, complete, checksum = words
    check = 0
    for word in words[:7]:
        check = (check * 31 + word) & 0xFFFFFFFF
    require(
        (magic, version, complete) == (0x52414D31, 1, 1) and checksum == check,
        "Invalid stack diagnostic terminal",
    )
    require(
        (low, top) == stack_bounds
        and low < painted_end <= top
        and (low | top | painted_end | used) & 3 == 0
        and top - painted_end <= used <= top - low,
        "Stack diagnostic differs from linked bounds",
    )
    return {
        "schema_version": 1,
        "stack_reserved_bytes": top - low,
        "stack_written_high_water_lower_bound_bytes": used,
        "baseline_active_stack_bytes": top - painted_end,
        "measurement_window": "model initialization, warmups and invokes; includes diagnostic overhead and interrupts; excludes boot/system initialization",
        "limitations": "paint collisions can underestimate written depth; stack pointer reservations without writes are not measured",
        "heap_peak_bytes": None,
        "heap_peak_reason": "not_instrumented",
    }


def decode_heap(values: dict[str, bytes], capacity: int | None) -> dict[str, object]:
    result: dict[str, object] = {}
    for symbol, field in (
        ("__malloc_max_sbrked_mem", "heap_sbrk_backing_high_water_bytes"),
        ("__malloc_max_total_mem", "heap_system_memory_high_water_bytes"),
    ):
        data = values.get(symbol)
        if data is None:
            result[field] = None
            result[field + "_reason"] = "allocator_counter_not_linked"
        else:
            require(len(data) == 4 and capacity is not None, "Unbounded heap counter")
            assert capacity is not None
            value = struct.unpack("<I", data)[0]
            require(value <= capacity, "Heap counter exceeds linked backing")
            result[field] = value
    result["heap_counter_basis"] = (
        "newlib maximum backing obtained from system since reset; not live payload peak; counters overlap and must not be added"
    )
    return result
