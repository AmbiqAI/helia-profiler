import hashlib
import json
from contextlib import nullcontext
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
        plan.write_text(
            json.dumps(
                {"name": plan.stem, "model": {"sha256": hashlib.sha256(b"model").hexdigest()}}
            )
        )
    seen = []
    monkeypatch.setattr(module, "reserve_probe", lambda _: nullcontext())

    def measure(config, plan, digest, root, attempt, references, force):
        seen.append(json.loads(plan.read_text())["name"])
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


def test_swapped_cache_receipt_cannot_supply_another_plans_cycles(tmp_path):
    path = tmp_path / "measurement.json"
    record = dict(
        plan_sha256="A",
        converted_plan_sha256="C",
        cache_key="K",
        output_identical=True,
        dma_failures=0,
        cycles=102,
        pool_bytes=100,
        energy_uj=None,
    )
    path.write_text(json.dumps(record))
    entry = tmp_path / "entry.json"
    entry.write_text(
        json.dumps({"key": "K", "path": path.name, "sha256": module._sha(path.read_bytes())})
    )
    assert module._read_cache(entry, tmp_path, "K", "A", "C") is not None
    assert module._read_cache(entry, tmp_path, "K", "B", "C") is None
    assert module._read_cache(entry, tmp_path, "K", "A", "D") is None
    assert module._read_cache(entry, tmp_path, "L", "A", "C") is None
