"""Tests for the preflight pipeline stage."""

from __future__ import annotations

import os
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
        "engine": {"type": "helia-rt"},
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

    def test_invalid_runtime_arena_location_raises(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {"engine": {"config": {"runtime_arena_location": "mram"}}},
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="runtime_arena_location"):
                PreflightStage().run(ctx)

    def test_invalid_runtime_weights_location_raises(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {"engine": {"config": {"runtime_weights_location": "flash"}}},
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="runtime_weights_location"):
                PreflightStage().run(ctx)

    def test_runtime_split_overrides_rejected_for_helia_aot(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "engine": {
                    "type": "helia-aot",
                    "config": {"runtime_weights_location": "sram"},
                },
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="runtime_weights_location is not supported"):
                PreflightStage().run(ctx)

    def test_psram_model_location_requires_rtt_transport(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"model_location": "psram"},
                "target": {"transport": "usb_cdc"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="PSRAM model weights require"):
                PreflightStage().run(ctx)

    def test_psram_runtime_weights_require_rtt_transport(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "engine": {"type": "helia-rt", "config": {"runtime_weights_location": "psram"}},
                "target": {"transport": "usb_cdc"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="PSRAM model weights require"):
                PreflightStage().run(ctx)

    def test_psram_model_location_allows_rtt_transport(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"model_location": "psram"},
                "target": {"transport": "rtt"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            PreflightStage().run(ctx)

    def test_ap4_rejects_mve_counter_group(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "target": {"board": "apollo4p_evb"},
                "profiling": {"pmu_counters": {"mve": "default"}},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="not supported"):
                PreflightStage().run(ctx)

    def test_ap3_rejects_legacy_mve_preset(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "target": {"board": "apollo3p_evb"},
                "profiling": {"pmu_presets": ["mve"]},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="not supported"):
                PreflightStage().run(ctx)


class TestPreflightHostTools:
    def test_missing_neuralspotx_package_raises_with_hint(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)

        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch(
                "helia_profiler.stages.s00_preflight.find_spec",
                return_value=None,
            ),
        ):
            with pytest.raises(ConfigError) as exc_info:
                PreflightStage().run(ctx)

        assert "neuralspotx" in str(exc_info.value)
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

    def test_rtt_requires_pylink_package(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"transport": "rtt"}})

        def fake_find_spec(name: str):
            if name == "neuralspotx":
                return object()
            if name == "pylink":
                return None
            return object()

        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch("helia_profiler.stages.s00_preflight.find_spec", side_effect=fake_find_spec),
        ):
            with pytest.raises(ConfigError, match="pylink"):
                PreflightStage().run(ctx)

    def test_usb_cdc_does_not_require_pylink_package(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"transport": "usb_cdc"}})

        def fake_find_spec(name: str):
            if name == "neuralspotx":
                return object()
            if name == "pylink":
                return None
            return object()

        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch("helia_profiler.stages.s00_preflight.find_spec", side_effect=fake_find_spec),
        ):
            PreflightStage().run(ctx)

    def test_atfe_uses_atfe_root_tools(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"toolchain": "atfe"}})
        atfe_root = tmp_path / "atfe"
        bin_dir = atfe_root / "bin"
        bin_dir.mkdir(parents=True)
        for tool in ("clang", "clang++", "llvm-ar", "llvm-objcopy", "llvm-size"):
            (bin_dir / tool).write_text("")

        def which_no_atfe_binary(name: str) -> str | None:
            return None if name == "atfe" else f"/usr/bin/{name}"

        with patch.dict("os.environ", {"ATFE_ROOT": str(atfe_root)}):
            with patch("shutil.which", side_effect=which_no_atfe_binary):
                PreflightStage().run(ctx)

    def test_atfe_missing_root_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"toolchain": "atfe"}})

        with patch.dict("os.environ", {}, clear=True):
            with patch("shutil.which", side_effect=_all_tools_present):
                with pytest.raises(ConfigError, match="ATFE_ROOT"):
                    PreflightStage().run(ctx)


class TestPreflightOutputDir:
    def test_creates_missing_output_dir(self, tmp_path: Path):
        out = tmp_path / "nested" / "does" / "not" / "exist"
        ctx = _make_ctx(tmp_path, {"output": {"dir": str(out)}})
        with patch("shutil.which", side_effect=_all_tools_present):
            PreflightStage().run(ctx)
        assert out.is_dir()

    @pytest.mark.skipif(
        os.name == "nt",
        reason="POSIX chmod bits do not make the directory unwritable on Windows",
    )
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
