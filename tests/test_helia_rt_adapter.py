"""Tests for the heliaRT engine adapter and NSX module installation."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType
from helia_profiler.engines.helia_rt import (
    HELIART_VERSION,
    HeliaRTAdapter,
    _install_nsx_module,
)
from helia_profiler.errors import EngineError


def _make_config(tmp_path: Path, engine_overrides: dict | None = None):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    base = {
        "model": {"path": str(model)},
        "engine": {"type": "helia-rt"},
    }
    if engine_overrides:
        base["engine"].update(engine_overrides)
    return load_config(None, base)


class TestInstallNsxModule:
    def test_copies_nsx_module_yaml(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        yaml_path = module_dir / "nsx-module.yaml"
        assert yaml_path.exists()
        content = yaml_path.read_text()
        assert "nsx-heliart" in content
        assert "schema_version: 1" in content

    def test_copies_cmakelists(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        cmake_path = module_dir / "CMakeLists.txt"
        assert cmake_path.exists()
        content = cmake_path.read_text()
        assert "nsx_heliart" in content
        assert "nsx::heliart" in content
        assert "NSX_BOARD_FLAGS_TARGET" in content
        assert "TF_LITE_STATIC_MEMORY" in content

    def test_variant_patched_in_cmakelists(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="debug")
        content = (module_dir / "CMakeLists.txt").read_text()
        assert 'HELIART_VARIANT "debug"' in content

    def test_default_variant_unchanged(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        content = (module_dir / "CMakeLists.txt").read_text()
        assert 'HELIART_VARIANT "release-with-logs"' in content

    def test_copies_dist_dirs(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        assert (module_dir / "lib").is_dir()
        assert (module_dir / "tensorflow").is_dir()
        assert (module_dir / "third_party").is_dir()
        assert (module_dir / "signal").is_dir()

    def test_missing_nsx_raises(self, tmp_path: Path, fake_dist: Path):
        """A dist without nsx/ should fail (heliaRT < 1.12.2)."""
        import shutil
        shutil.rmtree(fake_dist / "nsx")
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        with pytest.raises(EngineError, match="missing nsx/ module files"):
            _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")


class TestHeliaRTAdapter:
    def test_name(self):
        adapter = HeliaRTAdapter()
        assert adapter.name == "heliaRT"

    def test_prepare_creates_module_dir(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        adapter.prepare(config, tmp_path)
        module_dir = tmp_path / "modules" / "nsx-heliart"
        assert module_dir.is_dir()
        assert (module_dir / "nsx-module.yaml").exists()
        assert (module_dir / "CMakeLists.txt").exists()

    def test_prepare_links_distribution(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        adapter.prepare(config, tmp_path)
        module_dir = tmp_path / "modules" / "nsx-heliart"
        assert (module_dir / "lib").is_dir()
        assert (module_dir / "tensorflow").is_dir()
        assert (module_dir / "third_party").is_dir()

    def test_prepare_returns_extra_module(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert len(artifacts.extra_modules) == 1
        mod = artifacts.extra_modules[0]
        assert mod.name == "nsx-heliart"
        assert mod.version == HELIART_VERSION
        assert mod.path.is_dir()

    def test_prepare_typed_fields(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert artifacts.engine_type is EngineType.HELIA_RT
        assert artifacts.engine_header == "tensorflow/lite/micro/micro_interpreter.h"
        assert artifacts.heliart_version == HELIART_VERSION
        assert artifacts.heliart_variant == "release-with-logs"

    def test_prepare_default_backend(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert artifacts.engine_backend == "helia"

    def test_prepare_custom_backend(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(
            tmp_path, {"backend": "cmsis-nn", "config": {"dist_path": str(fake_dist)}}
        )
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert artifacts.engine_backend == "cmsis-nn"

    def test_prepare_custom_variant(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(
            tmp_path, {"config": {"variant": "debug", "dist_path": str(fake_dist)}}
        )
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert artifacts.heliart_variant == "debug"

    def test_prepare_invalid_variant_raises(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(
            tmp_path, {"config": {"variant": "bogus", "dist_path": str(fake_dist)}}
        )
        adapter = HeliaRTAdapter()
        with pytest.raises(EngineError, match="Invalid heliaRT variant"):
            adapter.prepare(config, tmp_path)

    def test_prepare_idempotent(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        artifacts1 = adapter.prepare(config, tmp_path)
        artifacts2 = adapter.prepare(config, tmp_path)
        assert artifacts1.extra_modules[0].name == artifacts2.extra_modules[0].name

    def test_prepare_no_dist_path_falls_through_to_download(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When no dist_path or env var is set, prepare() falls through to
        the GitHub release download path."""
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()

        # Mock the download to raise so we can confirm the fallthrough.
        monkeypatch.setattr(
            "helia_profiler.engines.helia_rt._fetch_github_release",
            lambda *a, **kw: (_ for _ in ()).throw(
                EngineError("download failed (mocked)")
            ),
        )
        with pytest.raises(EngineError, match="download failed"):
            adapter.prepare(config, tmp_path)

    def test_prepare_via_env_var(
        self, tmp_path: Path, fake_dist: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("HELIART_DIST_PATH", str(fake_dist))
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert len(artifacts.extra_modules) == 1

    def test_prepare_via_stage(self, tmp_path: Path, fake_dist: Path):
        """Integration: verify the stage dispatches to HeliaRTAdapter."""
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.s01_resolve_platform import ResolvePlatformStage
        from helia_profiler.stages.s02_prepare_engine import PrepareEngineStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt", "config": {"dist_path": str(fake_dist)}},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)
        assert ctx.engine_artifacts is not None
        assert len(ctx.engine_artifacts.extra_modules) == 1
        assert (work_dir / "modules" / "nsx-heliart" / "nsx-module.yaml").exists()
        assert (work_dir / "modules" / "nsx-heliart" / "lib").is_dir()
