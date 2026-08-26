"""Tests for the FlashFirmwareStage pipeline stage (stages/flash.py).

The stage deploys the built profile binary directly through
``target.probe.flash.flash_binary`` (the NSX-generated J-Link recipe path
shared with the power firmware) instead of ``nsx flash``, so these tests
pin the direct-call contract: artifact/SoC threading, deployment
publication, and the power-cycle retry taxonomy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.results import FirmwareArtifact
from helia_profiler.config import load_config
from helia_profiler.errors import BuildError, CaptureError, DeterministicCaptureError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.platform import get_soc_for_board
from helia_profiler.stages.flash import FlashFirmwareStage


def _make_ctx(tmp_path: Path, *, board: str = "apollo510_evb") -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "target": {"board": board},
            "work_dir": str(tmp_path / "work"),
        },
    )
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(config=config, work_dir=work_dir)
    ctx.soc = get_soc_for_board(board)
    ctx.resolved_jlink_serial = "1160001481"
    ctx.firmware_dir = tmp_path / "app"
    ctx.firmware_dir.mkdir(parents=True, exist_ok=True)
    ctx.binary_path = tmp_path / "app" / "hpx_profiler"
    ctx.binary_path.write_bytes(b"elf")
    ctx.publish_profile_firmware(FirmwareArtifact(
        role="profile",
        target_name="hpx_profiler",
        app_dir=ctx.firmware_dir,
        build_dir=ctx.firmware_dir,
        binary_path=ctx.binary_path,
    ))
    return ctx


class TestFlashFirmwareStageDirect:
    def test_flashes_built_binary_via_jlink_recipe(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path)
        calls: list[dict] = []

        def fake_flash_binary(binary_path, **kwargs):
            calls.append({"binary_path": binary_path, **kwargs})

        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary", fake_flash_binary
        )

        FlashFirmwareStage().run(ctx)

        assert len(calls) == 1
        assert calls[0]["binary_path"] == ctx.binary_path
        assert calls[0]["jlink_serial"] == "1160001481"
        assert ctx.soc is not None
        assert calls[0]["device"] == ctx.soc.jlink_device
        # AP5 app flash base — resolved from SoC capabilities, not hardcoded.
        assert calls[0]["load_addr"] == 0x00410000
        assert ctx.profile_run is not None
        assert ctx.profile_run.deployment is not None
        assert ctx.profile_run.deployment.firmware is ctx.profile_firmware

    def test_load_addr_resolved_per_soc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-AP5 board must get its own flash base, not Apollo5's."""
        ctx = _make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        calls: list[dict] = []
        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary",
            lambda binary_path, **kwargs: calls.append({"binary_path": binary_path, **kwargs}),
        )

        FlashFirmwareStage().run(ctx)

        assert ctx.soc is not None
        expected = ctx.soc.capabilities.memory.app_flash_load_addr
        assert expected is not None
        assert calls[0]["load_addr"] == expected
        assert calls[0]["load_addr"] != 0x00410000

    def test_missing_artifact_is_rejected(self, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        ctx.profile_firmware = None
        ctx.profile_run = None

        with pytest.raises(BuildError, match="No profile artifact"):
            FlashFirmwareStage().run(ctx)

    def test_capture_error_power_cycles_and_retries_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path)
        attempts: list[int] = []

        def flaky_flash_binary(binary_path, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise CaptureError("debug domain locked")

        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary", flaky_flash_binary
        )
        cycles: list[int] = []
        monkeypatch.setattr(
            "helia_profiler.stages.flash.try_power_cycle_for_context",
            lambda ctx: cycles.append(1) or True,
        )

        FlashFirmwareStage().run(ctx)

        assert len(attempts) == 2
        assert len(cycles) == 1
        assert ctx.profile_run is not None
        assert ctx.profile_run.deployment is not None

    def test_deterministic_error_skips_power_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ctx = _make_ctx(tmp_path)

        def missing_image(binary_path, **kwargs):
            raise DeterministicCaptureError("no flashable image", hint="re-run the build")

        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary", missing_image
        )
        cycles: list[int] = []
        monkeypatch.setattr(
            "helia_profiler.stages.flash.try_power_cycle_for_context",
            lambda ctx: cycles.append(1) or True,
        )

        with pytest.raises(BuildError, match="no flashable image"):
            FlashFirmwareStage().run(ctx)

        assert not cycles
