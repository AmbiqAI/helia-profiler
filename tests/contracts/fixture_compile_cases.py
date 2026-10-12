"""Shared fixed-fixture renders for the host and real-toolchain compile gates."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from helia_profiler._fixture_build import (
    FixedFixture,
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureRenderSpec,
    FixtureRole,
    FixtureStatePolicy,
    FixtureTimingScope,
    Int8Tensor,
    TypedFixture,
)
from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ArenaRegion, HeliaAotArtifacts
from helia_profiler.firmware.fixture import fixture_template_vars, write_fixture_headers
from helia_profiler.firmware.op_resolver import ResolverPlan
from helia_profiler.firmware.render import _jinja_env
from helia_profiler.fixture import FixturePlacement
from helia_profiler.fixture_analysis import (
    FixtureModelAnalysis,
    FixtureTensor,
    PerTensorQuantization,
    TypedFixtureModelAnalysis,
)
from helia_profiler.modelcost import ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import ArenaRole, Placement
from helia_profiler.results.models import NsxModuleRef

FIXTURE_KINDS = ("tcn", "kws", "typed")
FIXTURE_ENGINES = ("tflm", "helia-rt", "helia-aot")
FIXTURE_SCOPES = ("restore_and_invoke", "invoke_only")
FIXTURE_KIND_ENGINES = tuple(
    (kind, engine) for kind in FIXTURE_KINDS for engine in FIXTURE_ENGINES
) + tuple(
    (kind, engine)
    for kind in ("resource", "psram", "psram-resource", "psram-tcm", "psram-tcm-resource")
    for engine in ("tflm", "helia-aot")
)


def render_fixture(
    kind: str,
    engine: str,
    scope: str = "restore_and_invoke",
    *,
    aot_prefix: str = "fake",
    placement: FixturePlacement = FixturePlacement(),
) -> tuple[str, dict[str, str]]:
    """Render production fixture sources from representative tensor metadata."""
    if kind in ("typed", "resource", "psram", "psram-resource", "psram-tcm", "psram-tcm-resource"):
        if kind.startswith("psram"):
            placement = FixturePlacement(
                Placement.TCM if "tcm" in kind else Placement.SRAM, Placement.PSRAM
            )
        return _render_typed(
            engine,
            scope,
            aot_prefix=aot_prefix,
            placement=placement,
            resource=kind.endswith("resource"),
        )
    if kind == "tcn":
        inp = Int8Tensor((1, 240, 14), 0.007843011990189552, -1, 0)
        out = Int8Tensor((1, 240, 2), 0.003640471724793315, -31, 72)
        registrations = ("r.AddConv2D();", "r.AddReshape();")
    else:
        assert kind == "kws"
        inp = Int8Tensor((1, 49, 10, 1), 0.125, -4, 0)
        out = Int8Tensor((1, 12), 0.0625, -128, 4)
        registrations = ("r.AddFullyConnected();", "r.AddSoftmax();")
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)

        def pin(name: str, data: bytes) -> FixtureFile:
            path = directory / name
            path.write_bytes(data)
            return FixtureFile(path, sha256(data).hexdigest())

        fixture = FixedFixture(
            pin("model.tflite", b"model"),
            pin("input.bin", bytes(inp.size)),
            pin("output.bin", bytes(out.size)),
            inp,
            out,
        )
        ctx = PipelineContext(
            config=load_config(
                None,
                {
                    "model": {
                        "path": str(fixture.model.path),
                        "arena_size": 262144,
                        "arena_location": placement.arena.value,
                        "weights_location": placement.weights.value,
                    },
                    "engine": {"type": EngineType(engine).value},
                    "profiling": {"iterations": 17, "warmup": 3},
                },
            ),
            work_dir=directory,
        )
        ctx.fixture = FixtureRenderSpec(
            fixture,
            FixtureMethod(FixtureTimingScope(scope)),
            FixtureModelAnalysis(
                inp, out, ModelAnalysis([], 0, 0, 0), ResolverPlan("auto", registrations)
            ),
        )
        values = fixture_template_vars(ctx, [])
        text = _jinja_env.get_template("fixed_fixture.cc.j2").render(
            **values, aot_prefix=aot_prefix, allocate_arenas=True, arena_regions=[]
        )
        write_fixture_headers(directory, ctx)
        headers = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.h")}
    return text, headers


_TYPED_INPUTS = (
    FixtureTensor("signal", 0, "int16", (1, 16), PerTensorQuantization(0.0009765625, 0)),
    FixtureTensor("gain", 1, "float32", (1, 1), None),
    FixtureTensor("tokens", 2, "int32", (1, 256), None),
)
_TYPED_OUTPUTS = (
    FixtureTensor("label", 3, "int8", (1, 4), PerTensorQuantization(0.00390625, -128)),
    FixtureTensor("level", 4, "float32", (1, 2), None),
)


def _render_typed(
    engine: str, scope: str, *, aot_prefix: str, placement: FixturePlacement, resource: bool = False
) -> tuple[str, dict[str, str]]:
    """Render mixed-dtype inputs and outputs; heliaAOT also scans its scratch arena."""
    aot = EngineType(engine) is EngineType.HELIA_AOT
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)

        def pin(name: str, data: bytes) -> FixtureFile:
            path = directory / name
            path.write_bytes(data)
            return FixtureFile(path, sha256(data).hexdigest())

        fixture = TypedFixture(
            pin("model.tflite", b"model"),
            tuple(
                FixtureIO(
                    t,
                    pin(f"{t.name}.bin", bytes(t.size_bytes)),
                    FixtureRole.AUX if t.name == "gain" else FixtureRole.SIGNAL,
                )
                for t in _TYPED_INPUTS
            ),
            tuple(FixtureIO(t, pin(f"{t.name}.bin", bytes(t.size_bytes))) for t in _TYPED_OUTPUTS),
        )
        ctx = PipelineContext(
            config=load_config(
                None,
                {
                    "model": {
                        "path": str(fixture.model.path),
                        "arena_size": 262144,
                        "arena_location": placement.arena.value,
                        "weights_location": placement.weights.value,
                    },
                    "engine": {"type": EngineType(engine).value},
                    "profiling": {"iterations": 17, "warmup": 3},
                },
            ),
            work_dir=directory,
        )
        ctx.fixture = FixtureRenderSpec(
            fixture,
            FixtureMethod(
                FixtureTimingScope(scope), FixtureStatePolicy.CARRY if resource else None
            ),
            TypedFixtureModelAnalysis(
                _TYPED_INPUTS,
                _TYPED_OUTPUTS,
                False,
                ModelAnalysis([], 0, 0, 0),
                ResolverPlan(
                    "auto",
                    (
                        "r.AddFullyConnected();",
                        "r.AddSoftmax();",
                        "r.AddVarHandle();",
                        "r.AddCallOnce();",
                        "r.AddReadVariable();",
                        "r.AddAssignVariable();",
                    )
                    if resource
                    else ("r.AddFullyConnected();", "r.AddSoftmax();"),
                ),
                has_internal_state=resource,
                has_initializer=resource,
                subgraph_count=2 if resource else 1,
                resource_variable_count=20 if resource else 0,
            ),
            observe_aot_arenas=aot,
        )
        regions = (
            [
                ArenaRegion(
                    0,
                    "scratch",
                    4096,
                    16,
                    ArenaRole.SCRATCH,
                    "dtcm" if placement.arena is Placement.TCM else "sram",
                    placement.arena,
                ),
                ArenaRegion(
                    1,
                    "persistent",
                    256,
                    16,
                    ArenaRole.PERSISTENT,
                    "dtcm" if placement.arena is Placement.TCM else "sram",
                    placement.arena,
                ),
            ]
            if aot
            else []
        )
        if aot and placement.weights is Placement.PSRAM:
            (directory / "constant.bin").write_bytes(b"abcd")
            regions.append(
                ArenaRegion(
                    2,
                    "constant",
                    4,
                    16,
                    ArenaRole.CONSTANT,
                    "psram",
                    Placement.PSRAM,
                    "constant.bin",
                )
            )
            ctx.engine_artifacts = HeliaAotArtifacts(
                engine_header=f"{aot_prefix}_model.h",
                aot_prefix=aot_prefix,
                aot_module_name="fake",
                aot_cmake_target="nsx::fake",
                helia_aot_version="test",
                aot_allocate_arenas=False,
                aot_arena_regions=regions,
                extra_modules=[NsxModuleRef("fake", directory)],
            )
        values = fixture_template_vars(ctx, regions)
        text = _jinja_env.get_template("fixed_fixture.cc.j2").render(
            **values,
            aot_prefix=aot_prefix,
            allocate_arenas=placement.weights is not Placement.PSRAM,
            arena_regions=regions if placement.weights is Placement.PSRAM else [],
            psram_clock_hz=ctx.config.target.psram.clock_hz,
        )
        write_fixture_headers(directory, ctx)
        headers = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.h")}
        if aot and placement.weights is Placement.PSRAM:
            from helia_profiler.firmware.headers import _blob_to_header

            headers["hpx_const_blob_2.h"] = _blob_to_header(
                directory / "constant.bin", "hpx_const_blob_2"
            )
    return text, headers
