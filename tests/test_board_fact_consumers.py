from dataclasses import replace

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import PlatformError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.platform import get_soc_for_board
from helia_profiler.platform.soc import ClockDomain, ClockSpeed, PerfTier
from helia_profiler.stages.plan_memory import _resolve_placement

from .contracts.fixture_compile_cases import render_fixture

_APOLLO4_WITHOUT_PSRAM = (
    "apollo4p_evb",
    "apollo4l_evb",
    "apollo4l_blue_evb",
    "apollo4p_blue_kbr_evb",
    "apollo4p_blue_kxr_evb",
)


@pytest.mark.parametrize("board", _APOLLO4_WITHOUT_PSRAM)
@pytest.mark.parametrize("field", ("arena_location", "weights_location"))
def test_unpopulated_psram_refused_by_placement(tmp_path, board, field):
    config = load_config(
        None,
        {
            "model": {"path": str(tmp_path / "model.tflite"), field: "psram"},
            "target": {"board": board},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    ctx.soc = get_soc_for_board(board)
    with pytest.raises(PlatformError, match="has no PSRAM") as exc:
        _resolve_placement(ctx)
    assert exc.value.hint is not None
    assert "PSRAM-capable board" in exc.value.hint


def test_atomiq_trace_reference_is_independent_of_cpu():
    soc = get_soc_for_board("atomiq110_fpga_turbo")
    speed = soc.cpu_clock.speed("lp")
    assert speed is not None and speed.mhz == 25
    assert soc.capabilities.transport.swo_trace_clock_mhz == 48
    assert not soc.capabilities.transport.swo_core_clocked


@pytest.mark.parametrize("engine", ("tflm", "helia-rt", "helia-aot"))
def test_fixture_clock_reads_fixed_low_platform_fact(monkeypatch, engine):
    from helia_profiler import fixture_target

    soc = get_soc_for_board(fixture_target.FIXTURE_BOARD)
    low = ClockSpeed("lp", 125, PerfTier.LOW)
    changed = replace(soc, clocks=(ClockDomain("cpu", (low,), default="lp"),))
    monkeypatch.setattr(fixture_target, "get_soc_for_board", lambda _board: changed)
    source, _ = render_fixture("kws", engine)
    assert "sys_cfg.perf_mode = NSX_PERF_LOW;  // 125 MHz" in source
    assert "SystemCoreClock = 125000000U;" in source
    assert "deployment_timing[5] = SystemCoreClock;" in source


@pytest.mark.parametrize("board", ("apollo3p_evb", "apollo330mP_evb", "apollo3p_evb_cygnus"))
def test_ambiguous_sibling_psram_facts_retained(board):
    assert get_soc_for_board(board).memory.psram_kb > 0
