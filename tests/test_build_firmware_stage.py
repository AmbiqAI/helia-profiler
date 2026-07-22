"""Tests for the firmware build pipeline stage."""

from __future__ import annotations

from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.build_firmware import BuildFirmwareStage


def test_missing_binary_sections_does_not_fail_successful_build(
    tmp_path: Path, monkeypatch
) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path / "work")
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True)
    build_dir = tmp_path / "build"
    binary_path = build_dir / "hpx_profiler"
    progress = []
    ctx.progress_sink = progress.append

    monkeypatch.setattr("helia_profiler.firmware.build_app", lambda _ctx: (build_dir, binary_path))
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.binary_sections",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.compiler_version",
        lambda *_args, **_kwargs: "clang version",
    )
    monkeypatch.setattr(
        "helia_profiler.stages.build_firmware.cmake_version",
        lambda **_kwargs: "cmake version",
    )

    BuildFirmwareStage().run(ctx)

    assert ctx.binary_sections is None
    assert ctx.profile_firmware is not None
    assert progress[-1].message == "Profile firmware ready"
