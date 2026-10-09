"""Fixed-input measurements composed into the normal firmware renderer."""

from __future__ import annotations

from pathlib import Path

from ..engines import EngineType
from ..engines.base import ArenaRegion, HeliaAotArtifacts
from ..errors import ConfigError
from ..fixture import TypedFixture
from ..fixture_analysis import PerTensorQuantization
from ..fixture_stage import FixtureStage
from ..pipeline import PipelineContext
from ..placement import ArenaRole, Placement
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
                io.data,
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
                "initializer": ",".join(str(b) for b in data.read()) if inputs else None,
            }
        )
    return records


def fixture_template_vars(ctx: PipelineContext, regions: list[ArenaRegion]) -> dict[str, object]:
    spec = ctx.fixture
    if spec is None:
        raise ConfigError("Fixture render specification missing")
    spec.fixture.verify()
    if (
        isinstance(ctx.engine_artifacts, HeliaAotArtifacts)
        and not ctx.engine_artifacts.aot_allocate_arenas
    ):
        raise ConfigError("External AOT arenas are not qualified for fixed fixtures")
    if any(r.placement not in (Placement.SRAM, Placement.MRAM, Placement.TCM) for r in regions):
        raise ConfigError("Fixture supports SRAM, MRAM or TCM AOT regions only")
    if any(
        r.placement == "mram" and (not r.blob_filename or r.role is not ArenaRole.CONSTANT)
        for r in regions
    ):
        raise ConfigError("Writable AOT region cannot use MRAM fixture placement")
    for region in regions:
        requested = (
            ctx.config.model.weights_location
            if region.role is ArenaRole.CONSTANT
            else ctx.config.model.arena_location
        )
        if region.placement != requested:
            raise ConfigError("AOT region placement differs from fixture request")
        if requested is Placement.TCM and region.memory != "dtcm":
            raise ConfigError("TCM fixture requires DTCM runtime regions")
    return {
        "fixture_status": {stage.name.lower(): int(stage) for stage in FixtureStage},
        "fixture_inputs": _fixture_io(spec, inputs=True),
        "fixture_outputs": _fixture_io(spec, inputs=False),
        "fixture_aot": ctx.config.engine.type is EngineType.HELIA_AOT,
        "fixture_resolver": spec.model.resolver,
        "fixture_registrations": tuple(
            x.removeprefix("r.").removesuffix(";") for x in spec.model.resolver.registrations
        ),
        "fixture_iterations": ctx.config.profiling.iterations,
        "fixture_warmups": ctx.config.profiling.warmup,
        "fixture_timing_scope": spec.method.timing_scope.value,
        "fixture_arena_size": ctx.config.model.arena_size,
        "fixture_arena_placement": ctx.config.model.arena_location.value,
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
