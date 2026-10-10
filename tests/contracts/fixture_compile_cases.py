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
    FixtureTimingScope,
    Int8Tensor,
    TypedFixture,
)
from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.base import ArenaRegion
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

FIXTURE_KINDS = ("tcn", "kws", "typed")
FIXTURE_ENGINES = ("tflm", "helia-rt", "helia-aot")
FIXTURE_SCOPES = ("restore_and_invoke", "invoke_only")


def render_fixture(
    kind: str,
    engine: str,
    scope: str = "restore_and_invoke",
    *,
    aot_prefix: str = "fake",
    placement: FixturePlacement = FixturePlacement(),
) -> tuple[str, dict[str, str]]:
    """Render production fixture sources from representative tensor metadata."""
    if kind == "typed":
        return _render_typed(engine, scope, aot_prefix=aot_prefix, placement=placement)
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
    engine: str, scope: str, *, aot_prefix: str, placement: FixturePlacement
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
            FixtureMethod(FixtureTimingScope(scope)),
            TypedFixtureModelAnalysis(
                _TYPED_INPUTS,
                _TYPED_OUTPUTS,
                False,
                ModelAnalysis([], 0, 0, 0),
                ResolverPlan("auto", ("r.AddFullyConnected();", "r.AddSoftmax();")),
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
        values = fixture_template_vars(ctx, regions)
        text = _jinja_env.get_template("fixed_fixture.cc.j2").render(
            **values, aot_prefix=aot_prefix, allocate_arenas=True, arena_regions=[]
        )
        write_fixture_headers(directory, ctx)
        headers = {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.h")}
    return text, headers
