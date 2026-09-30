"""Fixed-fixture builds refuse every override that swaps a pinned input."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from helia_profiler._fixture_build import FIXTURE_REFUSED_ENVIRONMENT, _refuse_overrides
from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.errors import ConfigError


def _config(tmp_path: Path, **sections: dict[str, Any]) -> ProfileConfig:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    raw: dict[str, Any] = {
        "model": {"path": str(model), "arena_size": 65536},
        "engine": {"type": "helia-aot"},
        "target": {"toolchain": "atfe", "board": "apollo510_evb", "clock": {"cpu": "lp"}},
        "work_dir": str(tmp_path / "work"),
    }
    for key, value in sections.items():
        raw[key] = {**raw.get(key, {}), **value}
    return load_config(None, raw)


@pytest.mark.parametrize("launcher", ["auto", "none", "off", ""])
def test_pinned_config_is_accepted(tmp_path: Path, launcher: str) -> None:
    _refuse_overrides(_config(tmp_path, build={"compiler_launcher": launcher}))


@pytest.mark.parametrize(
    "name",
    [*FIXTURE_REFUSED_ENVIRONMENT, "CMSIS_NN_PATH", "HELIART_DIST_PATH", "HELIART_SOURCE_PATH"],
)
def test_environment_overrides_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    config = _config(tmp_path)
    monkeypatch.setenv(name, str(tmp_path))
    with pytest.raises(ConfigError, match="pinned inputs only"):
        _refuse_overrides(config)


@pytest.mark.parametrize(
    ("sections", "named"),
    [
        ({"target": {"segger_rtt_path": "/opt/rtt"}}, "target.segger_rtt_path"),
        ({"build": {"compiler_launcher": "ccache"}}, "build.compiler_launcher"),
        ({"build": {"nsx_modules": {"nsx-core": {"ref": "feat/x"}}}}, "nsx-core"),
        ({"engine": {"config": {"cmsis_nn_ref": "main"}}}, "cmsis-nn"),
    ],
)
def test_config_overrides_are_refused(
    tmp_path: Path, sections: dict[str, dict[str, Any]], named: str
) -> None:
    with pytest.raises(ConfigError, match=named):
        _refuse_overrides(_config(tmp_path, **sections))


def test_engine_config_file_is_refused(tmp_path: Path) -> None:
    config_file = tmp_path / "aot.yaml"
    config_file.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="engine.config_path"):
        _refuse_overrides(_config(tmp_path, engine={"config_path": str(config_file)}))
