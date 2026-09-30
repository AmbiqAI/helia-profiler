from collections.abc import Mapping
from pathlib import Path

import pytest

from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.deps.compatibility import select_cmsis_nn_override
from helia_profiler.engines.cmsis_nn import (
    arm_cmsis_nn_module_ref,
    cmsis_nn_module_ref,
    resolve_cmsis_nn_selector,
)
from helia_profiler.errors import EngineError


def _config(engine_config: Mapping[str, object]) -> ProfileConfig:
    return load_config(
        None,
        {
            "model": {"path": str(Path(__file__).parent / "fixtures" / "kws_ref_model.tflite")},
            "engine": {"type": "helia-rt", "config": engine_config},
            "target": {"board": "apollo510_evb"},
        },
    )


@pytest.mark.parametrize("revision", ["V.18.0", "V.19.1.0", "V.20.0", None])
@pytest.mark.parametrize("route", ["config", "environment"])
def test_local_module_accepts_header_revisions(
    tmp_path: Path,
    fake_cmsis_nn: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    route: str,
) -> None:
    header = fake_cmsis_nn / "Include" / "arm_nnfunctions.h"
    header.write_text(f"/* $Revision: {revision} */\n" if revision else "/* no revision */\n")
    monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    engine_config = {}
    if route == "config":
        engine_config["cmsis_nn_path"] = str(fake_cmsis_nn)
    else:
        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

    module = cmsis_nn_module_ref(_config(engine_config), tmp_path / "work")

    assert module.local
    assert module.name == "nsx-cmsis-nn"
    assert (module.path / "Include" / header.name).read_bytes() == header.read_bytes()
    assert (module.path / "Source" / "stub.c").is_file()
    assert (module.path / "nsx-module.yaml").read_bytes() == (
        fake_cmsis_nn / "nsx" / "nsx-module.yaml"
    ).read_bytes()
    assert (module.path / "nsx" / "CMakeLists.txt").read_bytes() == (
        fake_cmsis_nn / "nsx" / "CMakeLists.txt"
    ).read_bytes()


@pytest.mark.parametrize("missing", ["CMakeLists.txt", "nsx-module.yaml", "both"])
def test_local_module_requires_native_build_files(
    tmp_path: Path, fake_cmsis_nn: Path, missing: str
) -> None:
    (fake_cmsis_nn / "Include" / "arm_nnfunctions.h").write_text("/* $Revision: V.19.1.0 */\n")
    for name in ("CMakeLists.txt", "nsx-module.yaml"):
        if missing in (name, "both"):
            (fake_cmsis_nn / "nsx" / name).unlink()

    with pytest.raises(EngineError, match="missing native nsx/ module") as exc:
        cmsis_nn_module_ref(_config({"cmsis_nn_path": str(fake_cmsis_nn)}), tmp_path / "work")

    assert exc.value.hint is not None
    assert "nsx/CMakeLists.txt and nsx/nsx-module.yaml" in exc.value.hint


_ENV_PATH = "/env/ns-cmsis-nn"
_CFG_PATH = ("path", "engine.config.cmsis_nn_path", "/cfg")
_CFG_REF = ("ref", "engine.config.cmsis_nn_ref", "v1")
_ENV = ("path", "env.CMSIS_NN_PATH", _ENV_PATH)


@pytest.mark.parametrize(
    ("engine_config", "env", "provider", "expected"),
    [
        ({}, False, "nsx-cmsis-nn", None),
        ({}, True, "nsx-cmsis-nn", _ENV),
        ({}, True, "arm-cmsis-nn", None),
        ({"cmsis_nn_path": "/cfg"}, True, "nsx-cmsis-nn", _CFG_PATH),
        ({"cmsis_nn_path": "/cfg"}, False, "arm-cmsis-nn", _CFG_PATH),
        ({"cmsis_nn_ref": "v1"}, True, "nsx-cmsis-nn", _CFG_REF),
        ({"cmsis_nn_ref": "v1"}, False, "arm-cmsis-nn", _CFG_REF),
        # Empty values are not selectors, so the next rule down applies.
        ({"cmsis_nn_ref": ""}, True, "nsx-cmsis-nn", _ENV),
        ({"cmsis_nn_path": ""}, True, "nsx-cmsis-nn", _ENV),
        ({"cmsis_nn_path": "", "cmsis_nn_ref": "v1"}, False, "arm-cmsis-nn", _CFG_REF),
        ({"cmsis_nn_path": None}, False, "arm-cmsis-nn", None),
    ],
)
def test_selector_precedence_matches_the_qualification_stamp(
    monkeypatch: pytest.MonkeyPatch,
    engine_config: dict[str, object],
    env: bool,
    provider: str,
    expected: tuple[str, str, str] | None,
) -> None:
    if env:
        monkeypatch.setenv("CMSIS_NN_PATH", _ENV_PATH)
    else:
        monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    config = _config(engine_config)

    selected = resolve_cmsis_nn_selector(config, provider)

    assert selected == select_cmsis_nn_override(config.engine.config, provider_module=provider)
    if expected is None:
        assert selected is None
    else:
        assert selected is not None
        assert selected.module == provider
        assert (selected.mode, selected.selector, selected.requested) == expected


@pytest.mark.parametrize("provider", ["nsx-cmsis-nn", "arm-cmsis-nn"])
@pytest.mark.parametrize(
    ("engine_config", "match"),
    [
        ({"cmsis_nn_path": "/cfg", "cmsis_nn_ref": "v1"}, "mutually exclusive"),
        ({"cmsis_nn_ref": ""}, "cmsis_nn_ref must be a non-empty git ref"),
        ({"cmsis_nn_ref": "   "}, "cmsis_nn_ref must be a non-empty git ref"),
        ({"cmsis_nn_ref": 7}, "cmsis_nn_ref must be a non-empty git ref"),
        ({"cmsis_nn_path": "   "}, "cmsis_nn_path must be a non-empty filesystem path"),
        ({"cmsis_nn_path": 7}, "cmsis_nn_path must be a non-empty filesystem path"),
    ],
)
def test_selector_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch, provider: str, engine_config: dict[str, object], match: str
) -> None:
    monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    with pytest.raises(EngineError, match=match):
        resolve_cmsis_nn_selector(_config(engine_config), provider)


def test_unset_ref_defaults_differ_by_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    config = _config({})

    # arm-cmsis-nn keeps None so NSX uses the registry pin; ns-cmsis-nn is
    # declared at the baseline's qualified ref.
    assert arm_cmsis_nn_module_ref(config).ref is None
    assert (
        cmsis_nn_module_ref(config, tmp_path).ref
        == config.compatibility_baseline.module("nsx-cmsis-nn").ref
    )
    assert arm_cmsis_nn_module_ref(_config({"cmsis_nn_ref": "v1"})).ref == "v1"
