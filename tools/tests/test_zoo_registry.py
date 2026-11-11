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


def test_skip_reaches_the_registry(tmp_path):
    entry = {**_entry("a8w8", b"int8 graph"), "skip": {"helia-aot": "lacks ROUND"}}
    models_list = _write_list(tmp_path / "zoo.yaml", [entry])
    resolve, _ = _fake_zoo(tmp_path, {"a8w8": b"int8 graph"})
    output = tmp_path / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)], resolve) == 0

    assert load_model_file(output)["tiny-kws-a8w8"].skips == (("helia-aot", "lacks ROUND"),)


@pytest.mark.parametrize("skip", [["helia-aot"], {"helia-aot": ""}])
def test_invalid_skip_refuses(tmp_path, skip):
    entry = {**_entry("a8w8", b"x"), "skip": skip}
    with pytest.raises(ValueError, match="skip must map engine to reason"):
        zoo_registry.load_model_list(_write_list(tmp_path / "zoo.yaml", [entry]))


def test_unknown_skip_engine_refuses(tmp_path):
    entry = {**_entry("a8w8", b"x"), "skip": {"tvm": "lacks ROUND"}}
    with pytest.raises(ValueError, match="unknown engine tvm"):
        zoo_registry.load_model_list(_write_list(tmp_path / "zoo.yaml", [entry]))


def test_skip_engines_match_engine_type():
    from helia_profiler.engines import EngineType

    assert zoo_registry.ENGINES == {engine.value for engine in EngineType}


HF_URI = f"Ambiq/private-models@{'1' * 40}/models/tiny-vad/a8w8/model.tflite"


def _hf_entry(payload: bytes, arena: int = 4096) -> dict:
    return {
        "hf": HF_URI,
        "family": "tiny-vad",
        "precision": "a8w8",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "arena_size": arena,
    }


def _fake_hf(tmp_path: Path, payload: bytes):
    calls: list[tuple[str, str]] = []

    def fetch(uri: str, sha256: str) -> Path:
        calls.append((uri, sha256))
        model = tmp_path / "hf" / "model.tflite"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(payload)
        return model

    return fetch, calls


def _private(monkeypatch, entries: list[dict] | None, token: str = "hf_test") -> None:
    text = None if entries is None else yaml.safe_dump({"models": entries})
    monkeypatch.setattr(zoo_registry, "_read_private", lambda path: text)
    monkeypatch.setattr(zoo_registry, "_hf_token", lambda: token)


def _run_private(tmp_path, resolve=None, fetch=None) -> tuple[int, Path, Path]:
    models_list = _write_list(tmp_path / "zoo.yaml", [_entry("a8w8", b"int8 graph")])
    resolve = resolve or _fake_zoo(tmp_path, {"a8w8": b"int8 graph"})[0]
    output, private = tmp_path / "models.yaml", tmp_path / "private" / "models.yaml"
    argv = ["--models", str(models_list), "--output", str(output)]
    code = zoo_registry.main([*argv, "--private-output", str(private)], resolve, fetch)
    return code, output, private


def test_private_models_get_opaque_ids(tmp_path, monkeypatch):
    entry = {**_hf_entry(b"private graph", 16384), "skip": {"helia-aot": "lacks ROUND"}}
    _private(monkeypatch, [entry])
    fetch, calls = _fake_hf(tmp_path, b"private graph")

    code, output, private = _run_private(tmp_path, fetch=fetch)

    assert code == 0
    assert calls == [(HF_URI, entry["sha256"])]
    assert list(load_model_file(output)) == ["tiny-kws-a8w8"]
    model_id = f"p-{entry['sha256'][:8]}"
    spec = load_model_file(private)[model_id]
    assert spec.arena_size == 16384
    assert spec.name == "tiny-vad (a8w8)"
    assert (spec.family, spec.precision) == ("tiny-vad", "a8w8")
    assert spec.source == f"hf://{HF_URI}"
    assert spec.skips == (("helia-aot", "lacks ROUND"),)


def test_private_skipped_without_output(tmp_path, monkeypatch):
    monkeypatch.setattr(zoo_registry, "_read_private", pytest.fail)
    models_list = _write_list(tmp_path / "zoo.yaml", [_entry("a8w8", b"int8 graph")])
    resolve, _ = _fake_zoo(tmp_path, {"a8w8": b"int8 graph"})
    output = tmp_path / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)], resolve) == 0


@pytest.mark.parametrize(
    ("entries", "token", "unset"), [(None, "hf", "ZOO_AGE_KEY"), ([], "", "HF_TOKEN")]
)
def test_private_skips_without_secret(tmp_path, monkeypatch, capsys, entries, token, unset):
    _private(monkeypatch, entries if entries is None else [_hf_entry(b"x")], token)

    code, output, private = _run_private(tmp_path)

    assert code == 0
    assert output.exists() and not private.exists()
    assert f"{unset} unset; skipping private models" in capsys.readouterr().err


def test_private_fetch_error_hides_names(tmp_path, monkeypatch, capsys):
    entry = _hf_entry(b"private graph")
    _private(monkeypatch, [entry])

    def fetch(uri: str, sha256: str) -> Path:
        raise RuntimeError(f"https://huggingface.co/{uri}: HTTP 404")

    code, output, _ = _run_private(tmp_path, fetch=fetch)

    err = capsys.readouterr().err
    assert code == 1 and not output.exists()
    assert f"p-{entry['sha256'][:8]}: fetch failed: RuntimeError" in err
    assert "tiny-vad" not in err and "Ambiq" not in err


def test_private_sha_mismatch_hides_names(tmp_path, monkeypatch, capsys):
    _private(monkeypatch, [_hf_entry(b"pinned bytes")])
    fetch, _ = _fake_hf(tmp_path, b"other bytes")

    assert _run_private(tmp_path, fetch=fetch)[0] == 1

    err = capsys.readouterr().err
    assert "differs from pin" in err
    assert "tiny-vad" not in err and "Ambiq" not in err


def test_public_list_refuses_hf(tmp_path):
    models_list = _write_list(tmp_path / "zoo.yaml", [_hf_entry(b"x")])
    with pytest.raises(ValueError, match="hf entries belong in the private list"):
        zoo_registry.load_model_list(models_list)


def test_private_ids_must_differ():
    first = _hf_entry(b"x")
    second = {**first, "family": "other-vad"}
    with pytest.raises(ValueError, match="share a sha256 prefix"):
        zoo_registry.parse_private_list(yaml.safe_dump({"models": [first, second]}))


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"hf": "Ambiq/x@main/model.tflite"}, "40-hex commit"),
        ({"zoo": "tiny-kws", "rev": REV}, "one of 'zoo' or 'hf'"),
        ({"family": None}, "lacks 'family'"),
    ],
)
def test_invalid_hf_entry_refuses(change, message):
    entry = {**_hf_entry(b"x"), **change}
    entry = {key: value for key, value in entry.items() if value is not None}
    with pytest.raises(ValueError, match=message):
        zoo_registry.parse_private_list(yaml.safe_dump({"models": [entry]}))


def test_private_labels_stay_out_of_registry(tmp_path, monkeypatch):
    labels = {"title": "Tiny VAD", "task": "voice-activity-detection", "domain": "audio"}
    _private(monkeypatch, [{**_hf_entry(b"private graph", 16384), **labels}])
    fetch, _ = _fake_hf(tmp_path, b"private graph")

    code, _, private = _run_private(tmp_path, fetch=fetch)

    assert code == 0
    (spec,) = yaml.safe_load(private.read_text(encoding="utf-8"))["models"].values()
    assert not set(labels) & set(spec)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"task": ""}, "must be non-empty text"),
        ({"domain": 3}, "must be non-empty text"),
        ({"title": "   "}, "must be non-empty text"),
    ],
)
def test_invalid_private_labels_refuse(change, message):
    entry = {**_hf_entry(b"x"), **change}
    with pytest.raises(ValueError, match=message):
        zoo_registry.parse_private_list(yaml.safe_dump({"models": [entry]}))


def test_zoo_entry_labels_refuse(tmp_path):
    entry = {**_entry("a8w8", b"x"), "task": "keyword-spotting"}
    with pytest.raises(ValueError, match="labels from the Zoo record"):
        zoo_registry.load_model_list(_write_list(tmp_path / "zoo.yaml", [entry]))


@pytest.mark.parametrize(
    ("env", "saved"),
    [
        ({"HF_TOKEN_PATH": "custom/tok"}, "custom/tok"),
        ({"HF_HOME": "hfhome"}, "hfhome/token"),
        ({"XDG_CACHE_HOME": "xdg"}, "xdg/huggingface/token"),
    ],
)
def test_hf_token_path_follows_env(tmp_path, monkeypatch, env, saved):
    for key in ("HF_TOKEN", "HF_TOKEN_PATH", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(tmp_path / value))
    path = tmp_path / saved
    path.parent.mkdir(parents=True)
    path.write_text("hf_custom\n", encoding="utf-8")

    assert zoo_registry._hf_token() == "hf_custom"


def test_hf_token_falls_back_to_saved_file(tmp_path, monkeypatch):
    for key in ("HF_TOKEN", "HF_TOKEN_PATH", "HF_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    saved = tmp_path / ".cache" / "huggingface" / "token"
    saved.parent.mkdir(parents=True)
    saved.write_text("hf_saved\n", encoding="utf-8")

    assert zoo_registry._hf_token() == "hf_saved"
    monkeypatch.setenv("HF_TOKEN", "hf_env")
    assert zoo_registry._hf_token() == "hf_env"


def test_age_key_from_env_then_file(tmp_path, monkeypatch):
    key = tmp_path / "zoo.key"
    key.write_text("# public key: age1x\nAGE-SECRET-KEY-1FILE\n", encoding="utf-8")
    monkeypatch.delenv("ZOO_AGE_KEY", raising=False)
    monkeypatch.setenv("ZOO_AGE_KEY_FILE", str(key))
    assert zoo_registry.age_identity().endswith("AGE-SECRET-KEY-1FILE")
    monkeypatch.setenv("ZOO_AGE_KEY", "AGE-SECRET-KEY-1ENV")
    assert zoo_registry.age_identity() == "AGE-SECRET-KEY-1ENV"


def test_private_list_round_trips(tmp_path):
    x25519 = pytest.importorskip("pyrage").x25519
    identity = x25519.Identity.generate()
    recipients = tmp_path / "recipients"
    recipients.write_text(f"# test\n{identity.to_public()}\n", encoding="utf-8")
    text = yaml.safe_dump({"models": [_hf_entry(b"x")]})

    data = zoo_registry.encrypt_list(text, recipients)

    assert data.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----")
    assert zoo_registry.decrypt_list(data, f"# key\n{identity}\n") == text
    with pytest.raises(ValueError, match="age: "):
        zoo_registry.decrypt_list(data, str(x25519.Identity.generate()))


def test_checked_in_private_list_is_encrypted():
    data = zoo_registry.PRIVATE_LIST.read_bytes()
    assert data.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----")
    lines = zoo_registry.RECIPIENTS.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("age1") for line in lines)


def test_checked_in_list_is_valid():
    entries = zoo_registry.load_model_list(zoo_registry.DEFAULT_LIST)
    assert {(e["zoo"], e["precision"]) for e in entries} == {
        *(
            (f"mlperf-tiny-{model}", precision)
            for model in ("kws", "vww", "resnet8", "ad01")
            for precision in ("a8w8", "fp32", "fp16")
        ),
        *(("heartkit-arr", precision) for precision in ("a8w8", "a16w8", "fp32", "fp16")),
        *(("heartkit-seg", precision) for precision in ("a8w8", "a16w8", "fp32", "fp16")),
        ("mlperf-tiny-streaming-wake-word", "a8w8"),
        ("compressionkit-ppg-encoder", "a8w8"),
        ("sleepkit-stage", "a8w8"),
        ("microwakeword-okay-nabu", "a8w8"),
        ("rnnoise", "a8w8"),
        ("dtln-tflm", "a8w8"),
        ("fomo-cuedc-animals", "a8w8"),
    }


def test_malformed_list_reports(tmp_path, capsys):
    models_list = tmp_path / "zoo.yaml"
    models_list.write_text("models: [", encoding="utf-8")
    output = tmp_path / "models.yaml"

    assert zoo_registry.main(["--models", str(models_list), "--output", str(output)]) == 1

    assert capsys.readouterr().err.startswith("zoo_registry: ")


def test_seal_round_trips_without_work(tmp_path, monkeypatch):
    x25519 = pytest.importorskip("pyrage").x25519
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("zoo_private", ROOT / "tools" / "zoo_private.py")
    assert spec is not None and spec.loader is not None
    zoo_private = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zoo_private)
    identity = x25519.Identity.generate()
    recipients = tmp_path / "recipients"
    recipients.write_text(f"{identity.to_public()}\n", encoding="utf-8")
    private = tmp_path / "private"
    (private / "validation" / "case" / "work").mkdir(parents=True)
    (private / "validation" / "case" / "summary.json").write_text("{}", encoding="utf-8")
    (private / "validation" / "case" / "work" / "fw.bin").write_bytes(b"big")
    sealed = tmp_path / "sealed" / "zoo-private.tar.gz.age"

    zoo_private.seal(private, sealed, recipients)
    zoo_private.unseal(sealed, tmp_path / "out", str(identity))

    case = tmp_path / "out" / "private" / "validation" / "case"
    assert (case / "summary.json").read_text(encoding="utf-8") == "{}"
    assert not (case / "work").exists()
