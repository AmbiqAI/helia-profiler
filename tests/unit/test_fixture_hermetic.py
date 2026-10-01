"""Fixed-fixture builds refuse every override that swaps a pinned input."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import jinja2
import pytest

from helia_profiler._fixture_build import FIXTURE_REFUSED_ENVIRONMENT, _refuse_overrides
from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.errors import ConfigError
from helia_profiler.fixture import (
    FixtureFile,
    FixtureIO,
    FixtureMethod,
    FixtureTensor,
    FixtureTimingScope,
    PerTensorQuantization,
    TypedFixture,
    build_fixed_fixture,
)


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


@pytest.mark.parametrize("value", ["none", "off", "auto"])
def test_disabled_or_automatic_launcher_from_the_environment_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HPX_COMPILER_LAUNCHER", value)
    _refuse_overrides(_config(tmp_path))


def test_explicit_launcher_from_the_environment_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HPX_COMPILER_LAUNCHER", "ccache")
    with pytest.raises(ConfigError, match="env.HPX_COMPILER_LAUNCHER"):
        _refuse_overrides(_config(tmp_path))


def test_build_refuses_before_any_stage_or_work_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    data = tmp_path / "x.bin"
    data.write_bytes(bytes(4))
    tensor = FixtureTensor("x", 0, "int8", (1, 4), PerTensorQuantization(0.5, 0))
    pinned = FixtureFile(data, hashlib.sha256(data.read_bytes()).hexdigest())
    model = config.model.path
    fixture = TypedFixture(
        FixtureFile(model, hashlib.sha256(model.read_bytes()).hexdigest()),
        (FixtureIO(tensor, pinned),),
        (FixtureIO(tensor, pinned),),
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a pipeline stage ran")

    monkeypatch.setattr("helia_profiler._fixture_build.PipelineRunner", forbidden)
    monkeypatch.setenv("CFLAGS", "-O0")
    with pytest.raises(ConfigError, match="env.CFLAGS"):
        build_fixed_fixture(
            config, fixture, method=FixtureMethod(FixtureTimingScope.INVOKE_ONLY), compile=False
        )
    assert config.work_dir is not None and not config.work_dir.exists()


def test_fixture_flag_only_inserts_the_prefix_map_before_the_modules() -> None:
    from helia_profiler.firmware.render import _jinja_env

    assert _jinja_env.loader is not None
    source, _, _ = _jinja_env.loader.get_source(_jinja_env, "CMakeLists.txt.j2")
    template = _jinja_env.overlay(undefined=jinja2.ChainableUndefined).from_string(source)
    values = {
        "board": "apollo510_evb",
        "engine_type": "helia-aot",
        "cmake_vars": {},
        "transport": "rtt",
    }
    plain = template.render(**values, strip_build_paths=False)
    stripped = template.render(**values, strip_build_paths=True)
    inserted = (
        "\n\n# Record no build-machine paths, so the ELF depends only on its inputs.\n"
        'add_compile_options("-ffile-prefix-map=${CMAKE_CURRENT_LIST_DIR}=.")'
    )
    anchor = "set(CMAKE_EXPORT_COMPILE_COMMANDS ON)"
    assert "file-prefix-map" not in plain
    assert stripped == plain.replace(anchor, anchor + inserted, 1)
    assert stripped.index("file-prefix-map") < stripped.index("modules.cmake")
