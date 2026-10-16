"""Optional stack diagnostics keep bounds, identity and observation limits explicit."""

import json
import struct
from dataclasses import replace

import pytest
from test_fixture_record import _build
from test_fixture_request import _request
from test_typed_fixture import METHOD, analysis_of, config_for, typed

from helia_profiler._fixture_build import FixtureRenderSpec
from helia_profiler._fixture_ram import decode_ram
from helia_profiler._fixture_record import compiled_fixture_identity
from helia_profiler.engines import EngineType
from helia_profiler.firmware.fixture import fixture_template_vars
from helia_profiler.firmware.render import _jinja_env
from helia_profiler.fixture import dump_fixture_build, load_fixture_build
from helia_profiler.fixture_image import ContractError, fixture_sink_sizes
from helia_profiler.pipeline import PipelineContext


def terminal(**changes):
    values = dict(
        magic=0x52414D31,
        version=1,
        low=0x20000000,
        top=0x20004000,
        painted_end=0x20003F00,
        used=2048,
        complete=1,
    )
    values.update(changes)
    words = list(values.values())
    check = 0
    for word in words:
        check = (check * 31 + word) & 0xFFFFFFFF
    return struct.pack("<8I", *words, check)


def test_stack_watermark_is_not_heap_or_total_peak():
    result = decode_ram(terminal(), (0x20000000, 0x20004000))
    assert result["stack_written_high_water_lower_bound_bytes"] == 2048
    assert result["baseline_active_stack_bytes"] == 256
    assert result["heap_peak_bytes"] is None
    assert "stack_peak_bytes" not in result


@pytest.mark.parametrize(
    "fault",
    [
        {"magic": 0},
        {"version": 2},
        {"complete": 0},
        {"low": 0x20000004},
        {"painted_end": 0x20005000},
        {"used": 128},
        {"used": 0x5000},
        {"used": 257},
    ],
)
def test_invalid_or_mismatched_stack_terminal_refused(fault):
    with pytest.raises(ContractError):
        decode_ram(terminal(**fault), (0x20000000, 0x20004000))


def test_stack_checksum_and_absent_image_bounds_refused():
    with pytest.raises(ContractError):
        decode_ram(terminal()[:-1] + b"\xff", (0x20000000, 0x20004000))
    with pytest.raises(ContractError):
        decode_ram(terminal(), None)


def test_ram_option_is_identity_bound_and_round_trips(tmp_path):
    request = _request(tmp_path)
    assert replace(request, observe_ram=True).intent_identity != request.intent_identity
    build = _build(tmp_path)
    changed = replace(build, observe_ram=True)
    changed = replace(changed, build_identity=compiled_fixture_identity(changed))
    assert changed.build_identity != build.build_identity
    record = dump_fixture_build(changed, roots={"run": tmp_path})
    assert load_fixture_build(record, roots={"run": tmp_path}) == changed
    body = json.loads(record)
    body["fixture_api"] = [5, 3]
    with pytest.raises(ValueError, match="RAM diagnostic"):
        load_fixture_build(json.dumps(body), roots={"run": tmp_path})


@pytest.mark.parametrize("engine", [EngineType.HELIA_AOT, EngineType.TFLM])
def test_stack_diagnostic_wraps_initialization_and_invokes_only_when_selected(tmp_path, engine):
    fixture = typed(tmp_path)
    ctx = PipelineContext(config=config_for(tmp_path, fixture, engine), work_dir=tmp_path)
    ctx.fixture = FixtureRenderSpec(fixture, METHOD, analysis_of(fixture), observe_ram=True)
    source = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **fixture_template_vars(ctx, []), aot_prefix="hpx", allocate_arenas=True, arena_regions=[]
    )
    main = source.split("int main()", 1)[1]
    assert (
        main.index("nsx_system_init")
        < main.index("hpx_ram_begin()")
        < main.index("infer_fixture()")
        < main.index("hpx_ram_finish()")
    )
    assert "p < sp" in source and "__get_MSPLIM()" in source
    assert "__disable_irq()" in source and "__set_PRIMASK(mask)" in source
    ctx.fixture = replace(ctx.fixture, observe_ram=False)
    normal = _jinja_env.get_template("fixed_fixture.cc.j2").render(
        **fixture_template_vars(ctx, []), aot_prefix="hpx", allocate_arenas=True, arena_regions=[]
    )
    assert "deployment_ram" not in normal and "hpx_ram_begin" not in normal
    assert (
        fixture_sink_sizes((4,), power=False, memory=False, arena_count=0, psram_size=0, ram=True)[
            "deployment_ram"
        ]
        == 32
    )


def test_heap_counters_are_overlapping_backing_not_live_peak():
    from helia_profiler._fixture_ram import decode_heap

    values = {
        name: struct.pack("<I", 3072)
        for name in ("__malloc_max_sbrked_mem", "__malloc_max_total_mem")
    }
    result = decode_heap(values, 4096)
    assert result["heap_sbrk_backing_high_water_bytes"] == 3072
    assert result["heap_system_memory_high_water_bytes"] == 3072
    assert "heap_peak_bytes" not in result
    assert decode_heap({}, None)["heap_sbrk_backing_high_water_bytes"] is None
    with pytest.raises(ContractError):
        decode_heap(values, 2048)
    with pytest.raises(ContractError):
        decode_heap(values, None)


def test_ram_image_binds_stack_and_optional_heap_symbols(tmp_path):
    from test_fixture_capture import image_files

    from helia_profiler.fixture_image import inspect_elf

    elf, binary, sizes = image_files(
        tmp_path,
        {
            "deployment_ram": 32,
            "__malloc_max_sbrked_mem": 4,
            "__malloc_max_total_mem": 4,
        },
    )
    raw = bytearray(elf.read())
    headers = raw[128:288]
    names = b"\0.stack\0.heap\0"
    names_at = len(raw)
    raw.extend(names)
    table_at = len(raw)
    raw.extend(headers)
    raw.extend(struct.pack("<10I", 1, 8, 3, 0x20001000, 0, 4096, 0, 0, 4, 0))
    raw.extend(struct.pack("<10I", 8, 8, 3, 0x20002000, 0, 4096, 0, 0, 4, 0))
    raw.extend(struct.pack("<10I", 0, 3, 0, 0, names_at, len(names), 0, 0, 1, 0))
    struct.pack_into("<I", raw, 32, table_at)
    struct.pack_into("<HH", raw, 48, 7, 6)
    struct.pack_into("<I", raw, 84 + 20, 0x3000)
    image = inspect_elf(bytes(raw), binary.read(), 0x410000, {"deployment_ram": 32})
    assert image.stack_bounds == (0x20001000, 0x20002000)
    assert image.heap_capacity == 4096
    assert {s.name for s in image.sinks} == set(sizes)
    struct.pack_into("<I", raw, table_at + 4 * 40 + 8, 0)
    with pytest.raises(ContractError, match="stack extent"):
        inspect_elf(bytes(raw), binary.read(), 0x410000, {"deployment_ram": 32})


@pytest.mark.parametrize("observe", [False, True])
def test_public_config_wrapper_preserves_config_and_ram_option(monkeypatch, observe):
    from helia_profiler import _fixture_build as module
    from helia_profiler.fixture import build_fixed_fixture

    config, fixture, runtime, expected = object(), object(), object(), object()
    received = {}

    def build(actual_config, actual_fixture, **kwargs):
        assert actual_config is config and actual_fixture is fixture
        received.update(kwargs)
        return expected

    monkeypatch.setattr(module, "_build", build)
    options = {"observe_ram": True} if observe else {}
    assert (
        build_fixed_fixture(
            config, fixture, method=METHOD, runtime=runtime, compile=False, **options
        )
        is expected
    )
    assert received["observe_ram"] is observe
    assert received["runtime"] is runtime
    assert received["method"] is METHOD
    assert received["compile"] is False
    assert received["observe_aot_arenas"] is False
