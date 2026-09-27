"""Fixed-input measurements composed into the normal firmware renderer."""

from __future__ import annotations
from pathlib import Path
from ..engines import EngineType
from ..engines.base import ArenaRegion, HeliaAotArtifacts
from ..errors import ConfigError
from ..pipeline import PipelineContext
from ..placement import ArenaRole
from .render import _jinja_env, _write_text


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
    if any(r.placement not in ("sram", "mram") for r in regions):
        raise ConfigError("Fixture supports SRAM/MRAM AOT regions only")
    if any(
        r.placement == "mram" and (not r.blob_filename or r.role is not ArenaRole.CONSTANT)
        for r in regions
    ):
        raise ConfigError("Writable AOT region cannot use MRAM fixture placement")
    return {
        "input_tensor": spec.model.input_tensor,
        "output_tensor": spec.model.output_tensor,
        "input_values": ",".join(str(b) for b in spec.fixture.input.read()),
        "fixture_aot": ctx.config.engine.type is EngineType.HELIA_AOT,
        "fixture_resolver": spec.model.resolver,
        "fixture_registrations": tuple(
            x.removeprefix("r.").removesuffix(";") for x in spec.model.resolver.registrations
        ),
        "fixture_iterations": ctx.config.profiling.iterations,
        "fixture_warmups": ctx.config.profiling.warmup,
        "fixture_timing_scope": spec.method.timing_scope.value,
        "fixture_arena_size": ctx.config.model.arena_size,
    }


def write_fixture_headers(directory: Path, ctx: PipelineContext) -> None:
    if ctx.config.engine.type is EngineType.TFLM:
        _write_text(
            directory / "fixed_fixture_memory.h",
            _jinja_env.get_template("fixed_fixture_memory.h.j2").render(),
        )
    _write_text(
        directory / "fixed_fixture_clock.h",
        '#pragma once\n#include "apollo510.h"\n#include "nsx_core.h"\n#include "am_hal_status.h"\n#include "am_hal_stimer.h"\n'
        + _jinja_env.get_template("_stimer_init.j2").render(),
    )
