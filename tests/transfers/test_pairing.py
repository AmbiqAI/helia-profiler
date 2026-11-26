from copy import deepcopy
from dataclasses import replace

import pytest

from helia_profiler.calibration.setup import _setup
from helia_profiler.evaluation.compare import load_run_artifacts
from tests.calibration_bundles import pair

pytest.importorskip("helia_aot")


def test_probe_only_settings_pair_but_hardware_and_toolchain_are_held(tmp_path):
    slow, fast = pair(tmp_path, "psram")
    run = load_run_artifacts(slow)
    digest = _setup(run, str(slow)).pairing_digest
    assert digest and digest == _setup(load_run_artifacts(fast), str(fast)).pairing_digest
    metadata = deepcopy(run.metadata)
    metadata["config"].setdefault("profiling", {}).update(
        transfer_costs=True, transfer_provider_plan="probe.json"
    )
    assert _setup(replace(run, metadata=metadata), str(slow)).pairing_digest == digest

    for path, value in (
        (("config", "target", "clock", "cpu"), "lp"),
        (("config", "target", "psram", "clock_hz"), 96000000),
        (("config", "engine", "config", "aot_args", "memory", "stream_pool", "bytes"), 8192),
        (("toolchain", "compiler_version"), "different compiler"),
    ):
        changed = deepcopy(metadata)
        parent = changed
        for key in path[:-1]:
            parent = parent.setdefault(key, {})
        parent[path[-1]] = value
        assert _setup(replace(run, metadata=changed), str(slow)).pairing_digest != digest, path


def test_retains_the_model_providers_resolved_module_and_prefix(tmp_path):
    from unittest.mock import Mock

    from helia_profiler.engines.base import HeliaAotArtifacts
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.transfers.capture import _RetainProvider

    module = tmp_path / "modules" / "custom_model"
    (module / "src").mkdir(parents=True)
    (module / "src/custom_nsx_transfer.c").write_text("provider")
    (module / "custom_plan.json").write_text("{}")
    ctx = Mock(spec=PipelineContext)
    ctx.prepared_artifacts = Mock(
        spec=HeliaAotArtifacts, aot_module_name="custom_model", aot_prefix="custom"
    )
    ctx.resolved_firmware_dir = tmp_path
    ctx.config = Mock()
    ctx.config.profiling.transfer_provider_plan = None
    evidence = _RetainProvider(tmp_path / "retained")
    evidence.run(ctx)
    assert evidence.source.read_text() == "provider"
    assert evidence.plan.read_text() == "{}"
