"""Explicit module cache options retain their build and provenance meaning."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.config import NsxModuleOverride, load_config
from helia_profiler.deps.dependencies import create_workspace
from helia_profiler.engines.base import LiteRtArtifacts
from helia_profiler.engines.helia_rt.adapter import HeliaRTAdapter
from helia_profiler.errors import ConfigError
from helia_profiler.firmware import generate_app
from helia_profiler.pipeline import PipelineContext, serialize_config
from helia_profiler.stages.plan_memory import PlanMemoryStage
from helia_profiler.stages.resolve_platform import ResolvePlatformStage

_REF = "6795b3d977a5b59482fc1ab2d1c65ccca8d05de3"


def _context(tmp_path: Path, options: dict, *, engine: dict | None = None) -> PipelineContext:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / "model.tflite"
    model.write_bytes(b"TFL3" + bytes(64))
    config = load_config(
        None,
        {
            "model": {"path": str(model), "weights_location": "psram"},
            "engine": engine or {"type": "litert"},
            "target": {"board": "apollo510_evb"},
            "build": {"nsx_modules": {"nsx-psram": {"ref": _REF, "options": options}}},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ResolvePlatformStage().run(ctx)
    ctx.engine_artifacts = LiteRtArtifacts(
        engine_header="tensorflow/lite/micro/micro_interpreter.h"
    )
    PlanMemoryStage().run(ctx)
    ctx.dependency_workspace = create_workspace(ctx)
    return ctx


@pytest.mark.parametrize("source", [{"ref": _REF}, {"path": Path("/module")}])
def test_source_override_accepts_typed_options(source):
    options = {"NSX_PSRAM_ASYNC_DEPTH": 16, "ENABLED": False, "MODE": "stream", "RATIO": 1.5}
    override = NsxModuleOverride(**source, options=options)
    assert override.options == options
    assert {name: type(value) for name, value in override.options.items()} == {
        name: type(value) for name, value in options.items()
    }


@pytest.mark.parametrize(
    "options",
    [
        None,
        [],
        {"bad-name": 1},
        {"GOOD": []},
        {"GOOD": None},
        {"GOOD": float("inf")},
        {"GOOD": "a\0b"},
        {"GOOD": "a\nb"},
        {"GOOD": "a\rb"},
    ],
)
def test_invalid_cache_options_fail_before_build(options):
    with pytest.raises(ConfigError):
        NsxModuleOverride(ref=_REF, options=options)


def test_empty_options_preserve_existing_config_snapshots(tmp_path):
    ctx = _context(tmp_path, {})
    snapshot = serialize_config(ctx.config)["build"]["nsx_modules"]["nsx-psram"]
    assert snapshot == {"path": None, "ref": _REF, "version": None}
    assert "options" not in ctx.resolved_workspace.inputs["build"]["module_overrides"]["nsx-psram"]


def test_options_change_workspace_and_are_recorded_beside_source(tmp_path):
    ctx = _context(tmp_path, {"NSX_PSRAM_ASYNC_DEPTH": 16})
    first = ctx.resolved_workspace
    entry = first.inputs["build"]["module_overrides"]["nsx-psram"]
    assert entry["requested"] == _REF
    assert entry["options"] == {"NSX_PSRAM_ASYNC_DEPTH": 16}
    settings = serialize_config(ctx.config)
    assert settings["build"]["nsx_modules"]["nsx-psram"]["options"] == entry["options"]
    from helia_profiler.report.metadata import _metadata_to_dict

    ctx.run_metadata.config_snapshot = settings
    recorded = _metadata_to_dict(ctx.run_metadata)
    assert recorded["config"]["build"]["nsx_modules"]["nsx-psram"]["options"] == entry["options"]
    module = replace(
        ctx.config.build.nsx_modules["nsx-psram"], options={"NSX_PSRAM_ASYNC_DEPTH": 32}
    )
    ctx.config = replace(
        ctx.config, build=replace(ctx.config.build, nsx_modules={"nsx-psram": module})
    )
    second = create_workspace(ctx)
    assert first.fingerprint != second.fingerprint and first.root != second.root


def test_real_cmake_observes_rendered_values_before_module_defaults(tmp_path):
    cmake = shutil.which("cmake")
    if cmake is None:
        pytest.skip("cmake is required for cache-option execution")
    values = {
        "NSX_PSRAM_ASYNC_DEPTH": 16,
        "MODULE_ENABLED": False,
        "MODULE_TEXT": 'a"b\\c;${literal}',
    }
    ctx = _context(tmp_path, values)
    app = generate_app(ctx)
    rendered = (app / "CMakeLists.txt").read_text()
    cache_lines = [
        line
        for line in rendered.splitlines()
        if any(line.startswith(f"set({name} ") for name in values)
    ]
    assert len(cache_lines) == len(values)
    assert rendered.index(cache_lines[0]) < rendered.index("include(")
    script = tmp_path / "options.cmake"
    output = tmp_path / "cache.txt"
    script.write_text(
        "\n".join(cache_lines)
        + '\nset(NSX_PSRAM_ASYNC_DEPTH 8 CACHE STRING "module default")\n'
        + f'file(WRITE "{output.as_posix()}" "${{NSX_PSRAM_ASYNC_DEPTH}}\\n${{MODULE_ENABLED}}\\n${{MODULE_TEXT}}")\n'
    )
    subprocess.run(
        [cmake, "-P", str(script)], check=True, capture_output=True, text=True, timeout=30
    )
    assert output.read_text().splitlines() == ["16", "OFF", values["MODULE_TEXT"]]
    assert ctx.prepared_artifacts.cmake_vars == {}


@pytest.mark.parametrize("kind", ["unused", "engine", "profiler", "conflicting"])
def test_unapplied_or_conflicting_options_are_refused(tmp_path, kind):
    ctx = _context(tmp_path, {"MODULE_VALUE": 16})
    if kind == "engine":
        ctx.engine_artifacts = replace(ctx.prepared_artifacts, cmake_vars={"MODULE_VALUE": "16"})
    elif kind == "unused":
        override = ctx.config.build.nsx_modules["nsx-psram"]
        ctx.config = replace(
            ctx.config, build=replace(ctx.config.build, nsx_modules={"not-used": override})
        )
    elif kind == "profiler":
        override = NsxModuleOverride(ref=_REF, options={"NSX_SEGGER_RTT_BUFFER_SIZE_UP": 16})
        ctx.config = replace(
            ctx.config, build=replace(ctx.config.build, nsx_modules={"nsx-psram": override})
        )
    else:
        override = NsxModuleOverride(ref=_REF, options={"MODULE_VALUE": 32})
        ctx.config = replace(
            ctx.config,
            build=replace(
                ctx.config.build, nsx_modules={**ctx.config.build.nsx_modules, "nsx-core": override}
            ),
        )
    with pytest.raises(ConfigError, match="unused|owned|Conflicting"):
        generate_app(ctx)


@pytest.mark.parametrize(
    "name",
    [
        "NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM",
        "ARM_NN_ENABLE_F16",
        "NSX_HELIA_RT_ENABLE_ETHOSU",
    ],
)
def test_disabled_engine_options_cannot_be_enabled_through_another_module(tmp_path, name):
    ctx = _context(
        tmp_path,
        {name: True},
        engine={
            "type": "helia-rt",
            "config": {"cmsis_nn_requantize_inline_asm": False},
        },
    )
    ctx.engine_artifacts = HeliaRTAdapter().prepare(ctx.config, tmp_path / "engine")
    assert name not in ctx.prepared_artifacts.cmake_vars
    with pytest.raises(ConfigError, match=f"{name} is owned by the selected engine"):
        generate_app(ctx)
