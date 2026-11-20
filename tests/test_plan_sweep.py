from pathlib import Path

import pytest
from typer.testing import CliRunner

from helia_profiler import sweep as module
from helia_profiler.cli.app import app
from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError


def test_sweep_records_build_failure_and_continues(tmp_path, monkeypatch):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    config = load_config(
        None,
        {
            "model": {"path": model},
            "engine": {"type": "helia-aot"},
            "target": {"jlink_serial": "123"},
        },
    )
    plans = [tmp_path / name for name in ("a.json", "b.json")]
    for plan in plans:
        plan.write_text(plan.stem)
    seen = []
    monkeypatch.setattr(module, "hpx_cache_root", lambda: tmp_path)

    def measure(config, plan, digest, root, attempt, references, force):
        seen.append(plan.read_text())
        if len(seen) == 1:
            raise ConfigError("fixture compile failed")
        return module.SweepRow(digest, "measured")

    monkeypatch.setattr(module, "_measure", measure)
    rows = module.sweep(config, plans, tmp_path / "out", reference_outputs=("0" * 16,))
    assert seen == ["a", "b"]
    assert [row.status for row in rows] == ["failed", "measured"]
    assert rows[0].error is not None
    assert "fixture compile failed" in rows[0].error
    assert len(list((tmp_path / "out/runs").glob("*/sweep.json"))) == 1
    with pytest.raises(ConfigError, match="unique"):
        module.sweep(config, plans * 2, tmp_path / "out", reference_outputs=("0" * 16,))


def test_sweep_cli_requires_reference():
    result = CliRunner().invoke(
        app, ["sweep", "plan.json", "--output", "out", "--config", str(Path(__file__))]
    )
    assert result.exit_code == 2
    assert "reference-digest" in result.output
