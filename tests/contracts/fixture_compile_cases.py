"""Shared fixed-fixture renders for the host and real-toolchain compile gates."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.fixture import (
    FixedFixture,
    FixtureFile,
    Int8Tensor,
    FixtureRenderSpec,
    FixtureMethod,
    FixtureTimingScope,
)
from helia_profiler.fixture_analysis import FixtureModelAnalysis
from helia_profiler.firmware.op_resolver import ResolverPlan
from helia_profiler.modelcost import ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.firmware.fixture import fixture_template_vars, write_fixture_headers
from helia_profiler.firmware.render import _jinja_env

FIXTURE_KINDS = ("tcn", "kws")
FIXTURE_ENGINES = ("tflm", "helia-aot")
FIXTURE_SCOPES = ("restore_and_invoke", "invoke_only")


def render_fixture(
    kind: str, engine: str, scope: str = "restore_and_invoke", *, aot_prefix: str = "fake"
) -> tuple[str, dict[str, str]]:
    """Render production fixture sources from representative tensor metadata."""
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
                    "model": {"path": str(fixture.model.path), "arena_size": 262144},
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
