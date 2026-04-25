"""Tests for the preflight pipeline stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.s00_preflight import PreflightStage


# A minimal valid TFLite flatbuffer header is just the 'TFL3' magic in the
# first 16 bytes.  The stage only sniffs for the magic — it does not parse
# the full flatbuffer — so this is enough.
_MIN_TFLITE = b"\x00\x00\x00\x00TFL3" + b"\x00" * 512


def _make_ctx(tmp_path: Path, overrides: dict | None = None) -> PipelineContext:
    model = tmp_path / "model.tflite"
    model.write_bytes(_MIN_TFLITE)
    base: dict = {
        "model": {"path": str(model)},
        "engine": {"type": "tflm"},
        "output": {"dir": str(tmp_path / "out")},
        "work_dir": str(tmp_path / "work"),
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
    config = load_config(None, base)
    return PipelineContext(config=config, work_dir=tmp_path / "work")


def _all_tools_present(_name: str) -> str:
    return f"/usr/bin/{_name}"


class TestPreflightHappyPath:
    def test_passes_with_valid_inputs(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        with patch("shutil.which", side_effect=_all_tools_present):
            PreflightStage().run(ctx)
        # Output dir should have been created.
        assert (tmp_path / "out").is_dir()


class TestPreflightModel:
    def test_missing_model_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        (tmp_path / "model.tflite").unlink()
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="not found"):
                PreflightStage().run(ctx)

    def test_empty_model_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        (tmp_path / "model.tflite").write_bytes(b"")
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="empty"):
                PreflightStage().run(ctx)

    def test_non_tflite_extension_raises(self, tmp_path: Path):
        model = tmp_path / "model.bin"
        model.write_bytes(_MIN_TFLITE)
        ctx = _make_ctx(tmp_path, {"model": {"path": str(model)}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match=".tflite"):
                PreflightStage().run(ctx)

    def test_missing_tflite_magic_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        (tmp_path / "model.tflite").write_bytes(b"not a flatbuffer" * 10)
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="TFLite flatbuffer"):
                PreflightStage().run(ctx)

    def test_directory_as_model_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)
        model_path = tmp_path / "model.tflite"
        model_path.unlink()
        model_path.mkdir()
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="not a regular file"):
                PreflightStage().run(ctx)


class TestPreflightConfig:
    def test_zero_arena_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"model": {"arena_size": 0}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="arena_size"):
                PreflightStage().run(ctx)

    def test_negative_arena_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"model": {"arena_size": -1}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="arena_size"):
                PreflightStage().run(ctx)

    def test_invalid_model_location_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"model": {"model_location": "flash"}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="model_location"):
                PreflightStage().run(ctx)


class TestPreflightHostTools:
    def test_missing_nsx_raises_with_hint(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)

        def which_no_nsx(name: str) -> str | None:
            return None if name == "nsx" else f"/usr/bin/{name}"

        with patch("shutil.which", side_effect=which_no_nsx):
            with pytest.raises(ConfigError) as exc_info:
                PreflightStage().run(ctx)

        assert "nsx" in str(exc_info.value)
        assert exc_info.value.hint is not None
        assert "doctor" in exc_info.value.hint.lower()

    def test_jlink_only_required_for_supported_transports(self, tmp_path: Path):
        """Unsupported transport should not demand JLinkExe — but the config
        will also be rejected, we just want to make sure our logic tracks
        transport correctly.  Here we pretend transport is 'rtt' but JLinkExe
        is missing: the check must fail."""
        ctx = _make_ctx(tmp_path, {"target": {"transport": "rtt"}})

        def which_no_jlink(name: str) -> str | None:
            return None if name == "JLinkExe" else f"/usr/bin/{name}"

        with patch("shutil.which", side_effect=which_no_jlink):
            with pytest.raises(ConfigError, match="JLinkExe"):
                PreflightStage().run(ctx)


class TestPreflightOutputDir:
    def test_creates_missing_output_dir(self, tmp_path: Path):
        out = tmp_path / "nested" / "does" / "not" / "exist"
        ctx = _make_ctx(tmp_path, {"output": {"dir": str(out)}})
        with patch("shutil.which", side_effect=_all_tools_present):
            PreflightStage().run(ctx)
        assert out.is_dir()

    def test_unwritable_output_dir_raises(self, tmp_path: Path):
        out = tmp_path / "readonly"
        out.mkdir()
        out.chmod(0o500)
        try:
            ctx = _make_ctx(tmp_path, {"output": {"dir": str(out)}})
            with patch("shutil.which", side_effect=_all_tools_present):
                with pytest.raises(ConfigError, match="not writable"):
                    PreflightStage().run(ctx)
        finally:
            out.chmod(0o700)  # allow cleanup
