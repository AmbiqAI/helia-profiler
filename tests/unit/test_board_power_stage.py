"""Pure shared supply-selection controls; no device enumeration or driver imports."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from helia_profiler import power
from helia_profiler.errors import PowerError
from helia_profiler.stages.ensure_powered import EnsureBoardPoweredStage

if TYPE_CHECKING:
    from helia_profiler.pipeline import PipelineContext


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("available", [False, True])
def test_stage_uses_shared_driver_selection(monkeypatch, required, available):
    calls = []

    def driver(name, *, serial):
        calls.append((name, serial))
        if not available:
            raise PowerError("unavailable")
        return SimpleNamespace(ensure_target_powered=lambda *, required: True)

    monkeypatch.setattr(power, "get_driver", driver)
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            power=SimpleNamespace(driver="supply", serial="exact", enabled=required)
        ),
        passthrough_skipped=False,
        target_power_ensured=False,
    )
    if required and not available:
        with pytest.raises(PowerError, match="unavailable"):
            EnsureBoardPoweredStage().run(cast("PipelineContext", ctx))
    else:
        EnsureBoardPoweredStage().run(cast("PipelineContext", ctx))
        assert ctx.target_power_ensured is available
        assert ctx.passthrough_skipped is (not available)
    assert calls == [("supply", "exact")]
