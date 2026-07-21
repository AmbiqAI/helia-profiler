"""Tests for the FlashFirmwareStage pipeline stage (stages/flash.py).

Focused on config.frozen forwarding to the flash backend — the stage's
retry/power-cycle logic is exercised indirectly through the broader
pipeline tests; this file only covers the frozen-threading contract
added alongside neuralspotx>=0.7.5 support.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.results import FirmwareArtifact
from helia_profiler.config import load_config
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.flash import FlashFirmwareStage


def _make_ctx(tmp_path: Path, *, frozen: bool = False) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"board": "apollo510_evb"},
            "work_dir": str(tmp_path / "work"),
            "frozen": frozen,
        },
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(config=config, work_dir=work_dir)
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True, exist_ok=True)
    ctx.binary_path = tmp_path / "app" / "hpx_profiler.bin"
    ctx.binary_path.write_bytes(b"bin")
    ctx.publish_profile_firmware(FirmwareArtifact(
        role="profile",
        target_name="hpx_profiler",
        app_dir=ctx.firmware_dir,
        build_dir=ctx.firmware_dir,
        binary_path=ctx.binary_path,
    ))
    return ctx


class _FakeFlashBackend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def flash(self, firmware_path: Path, **kwargs: object) -> None:
        self.calls.append({"firmware_path": firmware_path, **kwargs})


class TestFlashFirmwareStageFrozen:
    def test_frozen_true_forwarded_to_backend(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, frozen=True)
        backend = _FakeFlashBackend()
        ctx.flash_backend = backend

        FlashFirmwareStage().run(ctx)

        assert len(backend.calls) == 1
        assert backend.calls[0]["frozen"] is True
        assert ctx.profile_run is not None
        assert ctx.profile_run.deployment is not None
        assert ctx.profile_run.deployment.firmware is ctx.profile_firmware

    def test_frozen_false_by_default(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path, frozen=False)
        backend = _FakeFlashBackend()
        ctx.flash_backend = backend

        FlashFirmwareStage().run(ctx)

        assert len(backend.calls) == 1
        assert backend.calls[0]["frozen"] is False

    def test_legacy_binary_path_without_artifact_is_rejected(self, tmp_path: Path) -> None:
        from helia_profiler.errors import BuildError

        ctx = _make_ctx(tmp_path)
        ctx.profile_firmware = None
        ctx.profile_run = None

        with pytest.raises(BuildError, match="No profile artifact"):
            FlashFirmwareStage().run(ctx)
