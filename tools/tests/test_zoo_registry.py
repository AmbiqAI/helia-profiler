"""Build Zoo registries offline with a fake resolver."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from helia_profiler.validation.matrix import load_model_file

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("zoo_registry", ROOT / "tools" / "zoo_registry.py")
assert _SPEC is not None and _SPEC.loader is not None
zoo_registry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(zoo_registry)

REV = "0" * 40


def _entry(precision: str, payload: bytes, arena: int = 4096) -> dict:
    return {
        "zoo": "tiny-kws",
        "precision": precision,
        "rev": REV,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "arena_size": arena,
    }


def _write_list(path: Path, entries: list[dict]) -> Path:
    path.write_text(yaml.safe_dump({"models": entries}), encoding="utf-8")
    return path


def _fake_zoo(tmp_path: Path, payloads: dict[str, bytes]):
    calls: list[str] = []

    def resolve(source: str):
        calls.append(source)
        precision = source.split("/")[-1].split("@")[0]
        model = tmp_path / "cache" / precision / "model.tflite"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(payloads[precision])
        return SimpleNamespace(model=model, record=SimpleNamespace(title="Tiny KWS"))

    return resolve, calls


def test_registry_loads_as_models_file(tmp_path):
    payloads = {"a8w8": b"int8 graph", "fp32": b"float graph"}
    entries = [_entry("a8w8", payloads["a8w8"]), _entry("fp32", payloads["fp32"], 8192)]
    models_list = _write_list(tmp_path / "zoo.yaml", entries)
    resolve, calls = _fake_zoo(tmp_path, payloads)
    output = tmp_path / "out" / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)], resolve) == 0

    assert calls == [f"zoo://tiny-kws/a8w8@{REV}", f"zoo://tiny-kws/fp32@{REV}"]
    models = load_model_file(output)
    assert list(models) == ["tiny-kws-a8w8", "tiny-kws-fp32"]
    fp32 = models["tiny-kws-fp32"]
    assert fp32.arena_size == 8192
    assert fp32.name == "Tiny KWS (fp32)"
    assert fp32.comparison_group == "tiny-kws-fp32"
    assert (fp32.family, fp32.precision) == ("tiny-kws", "fp32")
    assert fp32.source == f"zoo://tiny-kws/fp32@{REV}"
    assert Path(fp32.fixture_path).read_bytes() == payloads["fp32"]


def test_sha256_mismatch_refuses(tmp_path, capsys):
    models_list = _write_list(tmp_path / "zoo.yaml", [_entry("a8w8", b"pinned bytes")])
    resolve, _ = _fake_zoo(tmp_path, {"a8w8": b"other bytes"})
    output = tmp_path / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)], resolve) == 1

    assert "differs from pin" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"rev": "main"}, "40-hex commit"),
        ({"sha256": "ABC"}, "64 lowercase hex"),
        ({"arena_size": 0}, "positive integer"),
        ({"arena_size": True}, "positive integer"),
    ],
)
def test_invalid_entry_refuses(tmp_path, change, message):
    models_list = _write_list(tmp_path / "zoo.yaml", [{**_entry("a8w8", b"x"), **change}])
    with pytest.raises(ValueError, match=message):
        zoo_registry.load_model_list(models_list)


def test_missing_key_refuses(tmp_path):
    entry = _entry("a8w8", b"x")
    del entry["arena_size"]
    with pytest.raises(ValueError, match="lacks 'arena_size'"):
        zoo_registry.load_model_list(_write_list(tmp_path / "zoo.yaml", [entry]))


def test_duplicate_model_refuses(tmp_path):
    entry = _entry("a8w8", b"x")
    with pytest.raises(ValueError, match="duplicate model tiny-kws-a8w8"):
        zoo_registry.load_model_list(_write_list(tmp_path / "zoo.yaml", [entry, entry]))


def test_checked_in_list_is_valid():
    entries = zoo_registry.load_model_list(zoo_registry.DEFAULT_LIST)
    assert {(e["zoo"], e["precision"]) for e in entries} == {
        (f"mlperf-tiny-{model}", precision)
        for model in ("kws", "vww", "resnet8", "ad01")
        for precision in ("a8w8", "fp32", "fp16")
    }


def test_malformed_list_reports(tmp_path, capsys):
    models_list = tmp_path / "zoo.yaml"
    models_list.write_text("models: [", encoding="utf-8")
    output = tmp_path / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)]) == 1

    assert capsys.readouterr().err.startswith("zoo_registry: ")
