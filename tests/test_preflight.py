"""Tests for the preflight pipeline stage."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, ConfigError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.preflight import PreflightStage


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

    def test_passes_with_executorch_pte(self, tmp_path: Path):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model)},
                "engine": {"type": "executorch"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            PreflightStage().run(ctx)

    def test_executorch_rejects_power_capture(self, tmp_path: Path):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model)},
                "engine": {"type": "executorch"},
                "power": {"enabled": True},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="ExecuTorch profiling"):
                PreflightStage().run(ctx)

    @pytest.mark.parametrize("field", ["arena_location", "weights_location"])
    def test_executorch_rejects_psram_placement(self, tmp_path: Path, field: str):
        model = tmp_path / "model.pte"
        model.write_bytes(b"\x00\x00\x00\x00ET" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model), field: "psram"},
                "engine": {"type": "executorch"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="ExecuTorch profiling does not support PSRAM"):
                PreflightStage().run(ctx)

    @pytest.mark.parametrize("field", ["arena_location", "weights_location"])
    def test_helia_aot_rejects_psram_placement_without_external_arena_mode(
        self, tmp_path: Path, field: str
    ):
        """Default allocate_arenas=True renders ZERO PSRAM code (#219).

        main_aot.cc.j2 gates its whole PSRAM region on external-arena mode,
        so under the default the memory plan and the generated firmware
        disagree about where the tensors live and the run hangs at the
        host's PSRAM handshake.  Preflight must refuse in stage 0 with the
        config key that enables the sidecar-blob path.
        """
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model), field: "psram"},
                "engine": {"type": "helia-aot"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="external-arena mode") as excinfo:
                PreflightStage().run(ctx)
        assert "allocate_arenas" in str(excinfo.value)

    @pytest.mark.parametrize(
        "rule_attrs",
        [
            {"memory": "psram"},
            {"memory": "mram", "constant_destination_memory": "psram"},
        ],
        ids=["memory", "constant_destination_memory"],
    )
    def test_helia_aot_rejects_tensor_rule_psram_without_external_arena_mode(
        self, tmp_path: Path, rule_attrs: dict
    ):
        """PSRAM via aot_args.memory.tensors hits the same wall (#219).

        The coarse split fields are not the only route into PSRAM — a
        per-tensor rule can steer constants there with both fields unset,
        and under the default allocate_arenas=True that renders the same
        firmware-with-no-PSRAM-code silent no-op.
        """
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-aot",
                    "config": {
                        "aot_args": {
                            "memory": {
                                "tensors": [
                                    {"type": "constant", "attributes": rule_attrs},
                                ]
                            }
                        }
                    },
                },
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="external-arena mode"):
                PreflightStage().run(ctx)

    def test_helia_aot_accepts_psram_placement_in_external_arena_mode(self, tmp_path: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model), "weights_location": "psram"},
                "engine": {
                    "type": "helia-aot",
                    "config": {"aot_args": {"memory": {"allocate_arenas": False}}},
                },
            },
        )
        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch("helia_profiler.hostenv.doctor.find_spec", return_value=object()),
        ):
            PreflightStage().run(ctx)

    def test_helia_aot_psram_placement_does_not_require_rtt_transport(self, tmp_path: Path):
        """The RTT-transport requirement exists for the HOST upload only.

        A self-contained engine populates PSRAM from its own sidecar blobs,
        so no host upload happens and any transport is fine.
        """
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model), "weights_location": "psram"},
                "engine": {
                    "type": "helia-aot",
                    "config": {"aot_args": {"memory": {"allocate_arenas": False}}},
                },
                "target": {"transport": "usb_cdc"},
            },
        )
        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch("helia_profiler.hostenv.doctor.find_spec", return_value=object()),
        ):
            PreflightStage().run(ctx)

    def test_host_upload_engine_psram_weights_still_require_rtt(self, tmp_path: Path):
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00\x00\x00\x00TFL3" + b"\x00" * 512)
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"path": str(model), "weights_location": "psram"},
                "engine": {"type": "helia-rt"},
                "target": {"transport": "usb_cdc"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="require target.transport='rtt'"):
                PreflightStage().run(ctx)


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

    def test_zero_rtt_buffer_size_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"rtt_buffer_size_up": 0}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="rtt_buffer_size_up"):
                PreflightStage().run(ctx)

    def test_negative_rtt_buffer_size_raises(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"rtt_buffer_size_up": -1}})
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="rtt_buffer_size_up"):
                PreflightStage().run(ctx)

    def test_invalid_runtime_arena_location_raises(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {"model": {"arena_location": "mram"}},
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="arena_location"):
                PreflightStage().run(ctx)

    def test_invalid_runtime_weights_location_raises(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {"model": {"weights_location": "flash"}},
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="weights_location"):
                PreflightStage().run(ctx)

    def test_split_placement_is_accepted_for_helia_aot(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"weights_location": "sram"},
                "engine": {
                    "type": "helia-aot",
                },
            },
        )
        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch("helia_profiler.hostenv.doctor.find_spec", return_value=object()),
        ):
            PreflightStage().run(ctx)

    def test_psram_weights_require_rtt_transport(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"weights_location": "psram"},
                "target": {"transport": "usb_cdc"},
            },
        )
        with patch("shutil.which", side_effect=_all_tools_present):
            with pytest.raises(ConfigError, match="PSRAM model weights require"):
                PreflightStage().run(ctx)

    def test_psram_weights_allow_rtt_transport(self, tmp_path: Path):
        ctx = _make_ctx(
            tmp_path,
            {
                "model": {"weights_location": "psram"},
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


class TestPreflightHostTools:
    def test_missing_neuralspotx_package_raises_with_hint(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path)

        with (
            patch("shutil.which", side_effect=_all_tools_present),
            patch(
                "helia_profiler.hostenv.doctor.find_spec",
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
            return None if name in ("JLinkExe", "JLink.exe") else f"/usr/bin/{name}"

        with (
            patch("shutil.which", side_effect=which_no_jlink),
            patch(
                "helia_profiler.hostenv.doctor.find_jlink_exe",
                side_effect=CaptureError("JLinkExe not found"),
            ),
        ):
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
            patch("helia_profiler.hostenv.doctor.find_spec", side_effect=fake_find_spec),
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
            patch("helia_profiler.hostenv.doctor.find_spec", side_effect=fake_find_spec),
        ):
            PreflightStage().run(ctx)

    def test_atfe_uses_atfe_root_tools(self, tmp_path: Path):
        ctx = _make_ctx(tmp_path, {"target": {"toolchain": "atfe"}})
        atfe_root = tmp_path / "atfe"
        bin_dir = atfe_root / "bin"
        bin_dir.mkdir(parents=True)
        for tool in (
            "clang",
            "clang++",
            "llvm-ar",
            "llvm-objcopy",
            "llvm-size",
            "llvm-nm",
        ):
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
