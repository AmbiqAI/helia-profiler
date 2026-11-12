"""Cache policy remains explicit from requested intent to recorded conditions."""

from pathlib import Path

import pytest

from helia_profiler import Session
from helia_profiler.capture.parser import parse_firmware_output
from helia_profiler.config import ProfilingConfig, load_config
from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.pipeline import PipelineContext, serialize_config
from helia_profiler.report.conditions import run_conditions
from helia_profiler.report.metadata import _firmware_meta_to_dict
from helia_profiler.results import DeploymentRecord, FirmwareMeta, PmuResult
from helia_profiler.stages.resolve_platform import ResolvePlatformStage
from tests.contracts.test_firmware_render_snapshots import _render
from tests.pipeline_context_helpers import set_profile_firmware


def _context(tmp_path, mode="on", board="apollo510_evb"):
    model = tmp_path / "model.tflite"
    model.write_bytes((Path(__file__).parent / "fixtures/kws_ref_model.tflite").read_bytes())
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "litert"},
            "target": {"board": board},
            "profiling": {"dcache": mode},
        },
    )
    return PipelineContext(config=config, work_dir=tmp_path / "work")


@pytest.mark.parametrize(
    "value,expected", [("on", "on"), ("off", "off"), (True, "on"), (False, "off")]
)
def test_mode_normalizes_yaml_aliases(value, expected):
    assert ProfilingConfig(dcache=value).dcache == expected


@pytest.mark.parametrize("value", [None, 0, 1, "disabled", "ON", []])
def test_invalid_mode_is_rejected(tmp_path, value):
    with pytest.raises(ConfigError):
        _context(tmp_path, value)


def test_default_snapshot_stays_compatible_and_off_survives_plan_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "helia_profiler.hostenv._proc.tool_output",
        lambda *args, **kwargs: "arm-none-eabi-gcc 13.2.0",
    )
    ctx = _context(tmp_path)
    assert "dcache" not in serialize_config(ctx.config)["profiling"]
    base = Session.from_dict(
        {"model": {"path": str(ctx.config.model.path)}, "engine": {"type": "litert"}}
    )
    before = base.freeze()
    assert base.with_profiling(dcache="on").freeze().intent_hash == before.intent_hash
    off = base.with_profiling(dcache="off").freeze()
    assert off.intent_hash != before.intent_hash
    assert off.config().profiling.dcache == "off"
    assert off.to_dict()["settings"]["profiling"]["dcache"] == "off"


@pytest.mark.parametrize("board", ["apollo3p_evb", "apollo4p_evb"])
def test_unsupported_target_refuses_off_before_build(tmp_path, board):
    ctx = _context(tmp_path, "off", board)
    with pytest.raises(ConfigError, match="unsupported"):
        ResolvePlatformStage().run(ctx)
    assert not ctx.work_dir.exists()


def _stream(mode):
    lines = [
        "--- HPX_START ---",
        "--- HPX_PRESET cpu ---",
        "--- HPX_ITER 0 ---",
        '"Layer","Op","ARM_PMU_CPU_CYCLES","overflow"',
        "0,ADD,10,0",
        "--- HPX_END ---",
    ]
    if mode is not None:
        lines.insert(1, f"HPX_DCACHE={mode}")
    return lines


@pytest.mark.parametrize("mode", ["on", "off", None])
def test_reported_policy_round_trips(mode):
    result = parse_firmware_output(_stream(mode))
    assert result.meta.dcache == mode
    data = _firmware_meta_to_dict(result.meta)
    assert data.get("dcache") == mode


def test_invalid_reported_mode_is_not_accepted():
    with pytest.raises(CaptureError, match="HPX_DCACHE"):
        parse_firmware_output(_stream("unknown"))


@pytest.mark.parametrize(
    "requested,reported,valid",
    [
        ("on", None, True),
        ("on", "on", True),
        ("off", "off", True),
        ("off", None, False),
        ("off", "on", False),
        ("on", "off", False),
    ],
)
def test_capture_cannot_publish_a_different_policy(tmp_path, requested, reported, valid):
    ctx = _context(tmp_path, requested)
    ResolvePlatformStage().run(ctx)
    set_profile_firmware(ctx)
    assert ctx.profile_run is not None
    ctx.publish_profile_deployment(
        DeploymentRecord(firmware=ctx.profile_run.firmware, target_id="test", deployed_at="test")
    )
    result = PmuResult(meta=FirmwareMeta(dcache=reported))
    if valid:
        ctx.publish_profile_result(result)
        assert run_conditions(ctx).weights_cache == (reported or "on")
    else:
        with pytest.raises(CaptureError, match="data-cache"):
            ctx.publish_profile_result(result)
        assert ctx.profile_run.result is None


@pytest.mark.parametrize("engine", ["litert", "helia-rt", "helia-aot", "executorch"])
def test_policy_is_applied_once_before_measurement_without_touching_icache(engine):
    source = _render("apollo510", "rtt", engine, overrides={"dcache": "off"})
    disable = source.index("cache_status = am_hal_cachectrl_dcache_disable();")
    assert source.count("cache_status = am_hal_cachectrl_dcache_disable();") == 1
    assert source.index("uint32_t cache_status = nsx_cache_publish_writes();") < disable
    assert (
        disable < source.index("HPX_DCACHE=off") < source.index('hpx_printf("HPX_CLEAN_INFER_COUNT')
    )
    assert "am_hal_cachectrl_icache_disable(" not in source
    assert "nsx_cache_disable(" not in source
    normal = _render("apollo510", "rtt", engine)
    assert "am_hal_cachectrl_dcache_disable(" not in normal
    assert "HPX_DCACHE=on" in normal


@pytest.mark.parametrize("engine", ["litert", "helia-rt", "helia-aot"])
def test_power_policy_failure_uses_terminal_failure_path(engine):
    source = _render("apollo510", "rtt", engine, power_only=True, overrides={"dcache": "off"})
    assert 'hpx_power_terminal_fail("dcache_config", cache_status)' in source
    assert "HPX_DCACHE=" not in source
