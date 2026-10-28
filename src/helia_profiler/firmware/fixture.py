"""Fixed-input measurements composed into the normal firmware renderer."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ..engines import EngineType
from ..engines.base import ArenaRegion, HeliaAotArtifacts
from ..errors import ConfigError
from ..fixtures.analysis import PerTensorQuantization
from ..fixtures.placement import validate_fixture_regions
from ..fixtures.psram import MAX_PSRAM_PAYLOADS, FixturePsramPayload, payload_checksum
from ..fixtures.status import FixtureStage
from ..fixtures.target import fixture_app_region, fixture_cpu_hz, validate_fixture_placement
from ..fixtures.types import TypedFixture
from ..pipeline import PipelineContext
from ..placement import ArenaRole, Placement
from ..platform.memory_map import MemoryRegion
from .render import _jinja_env, _write_text

_TFLITE_TYPES = {
    "int8": ("kTfLiteInt8", "int8"),
    "int16": ("kTfLiteInt16", "i16"),
    "int32": ("kTfLiteInt32", "i32"),
    "float16": ("kTfLiteFloat16", "f16"),
    "float32": ("kTfLiteFloat32", "f"),
}


def _fixture_io(spec, *, inputs: bool) -> list[dict[str, object]]:
    """Normalize single-INT8 and typed fixtures into ordered IO template records."""
    if isinstance(spec.fixture, TypedFixture):
        entries = [
            (
                io.tensor.index,
                io.tensor.dtype,
                io.tensor.shape,
                io.tensor.quantization,
                io.tensor.size_bytes,
                io.data if inputs else None,
            )
            for io in (spec.fixture.inputs if inputs else spec.fixture.outputs)
        ]
    else:
        tensor = spec.model.input_tensor if inputs else spec.model.output_tensor
        quant = PerTensorQuantization(tensor.scale, tensor.zero_point)
        data = spec.fixture.input if inputs else spec.fixture.expected
        entries = [(tensor.tensor_index, "int8", tensor.shape, quant, tensor.size, data)]
    records = []
    for position, (index, dtype, shape, quant, size, data) in enumerate(entries):
        if inputs and data is None:
            raise ConfigError("Complete fixture input bytes required")
        suffix = "" if position == 0 else f"_{position}"
        tflite_type, member = _TFLITE_TYPES[dtype]
        per_tensor = quant if isinstance(quant, PerTensorQuantization) else None
        records.append(
            {
                "position": position,
                "tensor_index": index,
                "size": size,
                "shape": shape,
                "tflite_type": tflite_type,
                "member": member,
                "scale_hex": float(per_tensor.scale).hex() if per_tensor else None,
                "zero_point": per_tensor.zero_point if per_tensor else None,
                "tensor": ("input" if inputs else "output") + suffix,
                "pointer": ("input_data" if inputs else "output_data") + suffix,
                "buffer": "fixed_input" + suffix,
                "sink": "deployment_output" + suffix,
                "initializer": ",".join(str(b) for b in data.read())
                if inputs and data is not None
                else None,
            }
        )
    return records


def fixture_template_vars(ctx: PipelineContext, regions: list[ArenaRegion]) -> dict[str, object]:
    spec = ctx.fixture
    if spec is None:
        raise ConfigError("Fixture render specification missing")
    spec.fixture.verify()
    validate_fixture_placement(
        ctx.config.model.arena_location,
        ctx.config.model.weights_location,
        ctx.config.engine.type,
        board=ctx.config.target.board,
    )
    psram = ctx.config.model.weights_location is Placement.PSRAM
    external = (
        isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
        and not ctx.engine_artifacts.aot_allocate_arenas
    )
    if external and not psram:
        raise ConfigError("Fixture external AOT binding currently requires PSRAM weight backing")
    if any(
        r.placement not in (Placement.SRAM, Placement.MRAM, Placement.TCM, Placement.PSRAM)
        for r in regions
    ):
        raise ConfigError("Fixture AOT region has no implemented SRAM/MRAM/DTCM/PSRAM backing")
    # Only external binding reads a constant region's sidecar file.
    if any(
        r.placement == "mram"
        and (r.role is not ArenaRole.CONSTANT or (external and not r.blob_filename))
        for r in regions
    ):
        raise ConfigError("Writable AOT region cannot use MRAM fixture placement")
    validate_fixture_regions(ctx, regions)
    payloads = fixture_psram_payloads(ctx, regions) if psram else ()
    return {
        "fixture_observe_ram": spec.observe_ram,
        "fixture_dtcm": fixture_app_region(MemoryRegion.DTCM, ctx.config.target.board),
        "fixture_cpu_hz": fixture_cpu_hz(ctx.config.target.board),
        "fixture_power_sync": spec.method.power_sync,
        "fixture_psram": psram,
        "fixture_psram_base": fixture_app_region(MemoryRegion.PSRAM)[0] if psram else None,
        "fixture_psram_payloads": payloads,
        "fixture_status": {stage.name.lower(): int(stage) for stage in FixtureStage},
        "fixture_inputs": _fixture_io(spec, inputs=True),
        "fixture_outputs": _fixture_io(spec, inputs=False),
        "fixture_aot": ctx.config.engine.type is EngineType.HELIA_AOT,
        "fixture_resolver": spec.model.resolver,
        "fixture_subgraph_count": spec.model.subgraph_count,
        "fixture_resource_variables": spec.model.resource_variable_count,
        "fixture_registrations": tuple(
            x.removeprefix("r.").removesuffix(";") for x in spec.model.resolver.registrations
        ),
        "fixture_iterations": ctx.config.profiling.iterations,
        "fixture_warmups": ctx.config.profiling.warmup,
        "fixture_timing_scope": spec.method.timing_scope.value,
        "fixture_arena_size": ctx.config.model.arena_size,
        "fixture_arena_placement": cast(Placement, ctx.config.model.arena_location).value,
        "fixture_scan_arenas": [
            {"region_id": r.region_id, "size": r.size}
            for r in regions
            if r.role is ArenaRole.SCRATCH
        ]
        if spec.observe_aot_arenas
        else [],
    }


def write_fixture_headers(directory: Path, ctx: PipelineContext) -> None:
    if ctx.config.engine.type is not EngineType.HELIA_AOT:
        _write_text(
            directory / "fixed_fixture_memory.h",
            _jinja_env.get_template("fixed_fixture_memory.h.j2").render(),
        )
    _write_text(
        directory / "fixed_fixture_clock.h",
        '#pragma once\n#include "apollo510.h"\n#include "nsx_core.h"\n#include "am_hal_status.h"\n#include "am_hal_stimer.h"\n'
        + _jinja_env.get_template("_stimer_init.j2").render(),
    )


def fixture_psram_payloads(
    ctx: PipelineContext, regions: list[ArenaRegion]
) -> tuple[FixturePsramPayload, ...]:
    """Validate the complete backing payloads the PSRAM fixture must load."""
    if ctx.fixture is None:
        raise ConfigError("PSRAM fixture render specification missing")
    artifacts = ctx.engine_artifacts
    if isinstance(artifacts, HeliaAotArtifacts):
        if artifacts.aot_allocate_arenas:
            raise ConfigError("PSRAM fixture requires external AOT arenas")
        module = next(
            (m.path for m in artifacts.extra_modules if m.name == artifacts.aot_module_name), None
        )
        if module is None:
            raise ConfigError("PSRAM AOT constant module missing")
        if (
            not regions
            or len(regions) > 256
            or {r.region_id for r in regions} != set(range(len(regions)))
            or any(
                type(r.region_id) is not int
                or type(r.size) is not int
                or r.size <= 0
                or type(r.alignment) is not int
                or r.alignment <= 0
                or r.alignment > 64
                or r.alignment & (r.alignment - 1)
                for r in regions
            )
        ):
            raise ConfigError("Invalid PSRAM AOT region table")
        payloads = []
        for region in regions:
            if region.role is not ArenaRole.CONSTANT:
                continue
            if region.placement is not Placement.PSRAM or not region.blob_filename:
                raise ConfigError("PSRAM AOT constant requires a complete sidecar blob")
            blob = (module / region.blob_filename).resolve()
            if not blob.is_relative_to(module.resolve()):
                raise ConfigError("PSRAM constant blob is outside the generated module")
            if not 0 < region.size <= 64 * 1024 * 1024 or blob.stat().st_size != region.size:
                raise ConfigError("PSRAM AOT blob size differs from bounded region")
            with blob.open("rb") as stream:
                data = stream.read(region.size + 1)
            if (
                len(data) != region.size
                or region.alignment <= 0
                or region.alignment > 64
                or region.alignment & (region.alignment - 1)
            ):
                raise ConfigError("PSRAM AOT blob size or alignment differs from region")
            payloads.append(
                FixturePsramPayload(region.region_id, len(data), payload_checksum(data))
            )
    else:
        data = ctx.fixture.fixture.model.read()
        if len(data) < 4:
            raise ConfigError("PSRAM model backing is truncated")
        payloads = [FixturePsramPayload(0, len(data), payload_checksum(data))]
    if not 0 < len(payloads) <= MAX_PSRAM_PAYLOADS:
        raise ConfigError("Invalid PSRAM constant payload count")
    return tuple(payloads)
