import hashlib
import json
from contextlib import nullcontext
from pathlib import Path

import pytest
from click import unstyle
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

    validated = []
    monkeypatch.setattr(module, "_validate_plan", lambda path, *_: validated.append(path))

    def measure(config, plan, digest, root, attempt, references, force):
        assert len(validated) == len(plans)
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
    assert "reference-digest" in unstyle(result.output)


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


def test_incomplete_plan_with_matching_digest_is_rejected_before_capture(tmp_path, monkeypatch):
    pytest.importorskip("helia_aot")
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
    plan = tmp_path / "incomplete.json"
    plan.write_text(json.dumps({"model": {"sha256": module._sha(model.read_bytes())}}))
    monkeypatch.setattr(module, "_measure", lambda *args: pytest.fail("invalid plan reached build"))
    rows = module.sweep(config, [plan], tmp_path / "out", reference_outputs=("0" * 16,))
    assert rows[0].status == "failed"
    assert "schema" in (rows[0].error or "").lower()


def test_interruption_preserves_completed_index(tmp_path, monkeypatch):
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
    plans = [tmp_path / name for name in ("one.json", "two.json")]
    for plan in plans:
        plan.write_text(plan.stem)
    monkeypatch.setattr(module, "_validate_plan", lambda *_: None)
    monkeypatch.setattr(module, "reserve_probe", lambda _: nullcontext())

    def measure(config, plan, digest, root, attempt, references, force):
        if plan.read_text() == "two":
            raise KeyboardInterrupt()
        return module.SweepRow(digest, "measured")

    monkeypatch.setattr(module, "_measure", measure)
    with pytest.raises(KeyboardInterrupt):
        module.sweep(config, plans, tmp_path / "out", reference_outputs=("0" * 16,))
    (index,) = (tmp_path / "out/runs").glob("*/sweep.json")
    rows = json.loads(index.read_text())
    assert [(row["plan_sha256"], row["status"]) for row in rows] == [
        (module._sha(b"one"), "measured")
    ]


def test_cache_identity_includes_host_aggregation(tmp_path):
    from dataclasses import replace

    from helia_profiler.vocab import Aggregation

    config = load_config(None, {"model": {"path": tmp_path / "model.tflite"}})
    mean = replace(config, profiling=replace(config.profiling, aggregation=Aggregation.MEAN))

    def key(cfg):
        return module._measurement_key(
            cfg, "model", "requested", "converted", "firmware", "123", ("0" * 16,)
        )

    assert key(config) != key(mean)


@pytest.mark.parametrize(
    ("file_subgraph", "inline_subgraph", "expected"),
    [(None, None, 0), (1, None, 1), (1, "2", 2)],
)
def test_prevalidation_matches_configured_subgraph(
    tmp_path, monkeypatch, file_subgraph, inline_subgraph, expected
):
    pytest.importorskip("helia_aot")
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    engine = {"type": "helia-aot"}
    if file_subgraph is not None:
        compiler_config = tmp_path / "convert.yaml"
        compiler_config.write_text(f"model:\n  subgraph: {file_subgraph}\n")
        engine["config_path"] = str(compiler_config)
    if inline_subgraph is not None:
        engine["config"] = {"aot_args": {"model": {"subgraph": inline_subgraph}}}
    config = load_config(
        None, {"model": {"path": model}, "engine": engine, "target": {"jlink_serial": "123"}}
    )
    fixture = Path(__file__).parent / "fixtures/calibration/kws_int8_kernel_generic_plan.json"
    candidate = json.loads(fixture.read_text())
    candidate["model"]["sha256"] = module._sha(model.read_bytes())
    paths = []
    for index in [expected, expected + 1]:
        candidate["model"]["subgraph"] = index
        path = tmp_path / f"candidate-{index}.json"
        path.write_text(json.dumps(candidate))
        paths.append(path)
    measured = []

    def measure(config, plan, digest, root, attempt, references, force):
        measured.append(json.loads(plan.read_text())["model"]["subgraph"])
        return module.SweepRow(digest, "measured")

    monkeypatch.setattr(module, "reserve_probe", lambda _: nullcontext())
    monkeypatch.setattr(module, "_measure", measure)
    rows = module.sweep(config, paths, tmp_path / "out", reference_outputs=("0" * 16,))
    assert measured == [expected]
    assert [row.status for row in rows] == ["measured", "failed"]
    assert rows[1].error is not None and f"expected {expected}" in rows[1].error


@pytest.mark.parametrize("invalid", [False, [], "", 0])
def test_sweep_rejects_falsey_nonmapping_optimization(tmp_path, invalid):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    config = load_config(
        None,
        {
            "model": {"path": model},
            "target": {"jlink_serial": "123"},
            "engine": {"type": "helia-aot", "config": {"aot_args": {"optimization": invalid}}},
        },
    )
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    with pytest.raises(ConfigError, match="optimization settings must be a mapping"):
        module.sweep(config, [plan], tmp_path / "out", reference_outputs=("0" * 16,))
