"""Both explicit placement pairs render through the shared gate cases."""

import pytest

from helia_profiler.fixture import FixturePlacement, Placement

from .fixture_compile_cases import FIXTURE_ENGINES, FIXTURE_KINDS, FIXTURE_SCOPES, render_fixture


@pytest.mark.parametrize("kind", FIXTURE_KINDS)
@pytest.mark.parametrize("engine", FIXTURE_ENGINES)
@pytest.mark.parametrize("scope", FIXTURE_SCOPES)
@pytest.mark.parametrize(
    "placement", [FixturePlacement(), FixturePlacement(Placement.TCM, Placement.TCM)]
)
def test_shared_fixture_cases_render_explicit_placement(kind, engine, scope, placement):
    source, headers = render_fixture(kind, engine, scope, placement=placement)
    assert "volatile int32_t deployment_status" in source
    assert "fixed_fixture_clock.h" in headers
    if engine != "helia-aot":
        macro = "NSX_MEM_FAST_BSS" if placement.arena is Placement.TCM else "NSX_MEM_SRAM_BSS"
        assert f"{macro} alignas(16) static uint8_t arena[262144];" in source
    if kind == "typed":
        assert "deployment_output_1[8]" in source
        assert "std::memcpy(input_data_2, fixed_input_2, sizeof(fixed_input_2));" in source
