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
    _install_nsx_module_source,
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
        assert "nsx-helia-rt" in content
        assert "schema_version: 1" in content

    def test_copies_cmakelists(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        cmake_path = module_dir / "CMakeLists.txt"
        assert cmake_path.exists()
        content = cmake_path.read_text()
        assert "nsx_helia_rt" in content
        assert "nsx::helia_rt" in content
        assert "NSX_BOARD_FLAGS_TARGET" in content
        assert "TF_LITE_STATIC_MEMORY" in content

    def test_writes_nested_nsx_shim(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        shim = module_dir / "nsx" / "CMakeLists.txt"
        assert shim.exists()
        assert '../CMakeLists.txt' in shim.read_text()

    def test_variant_patched_in_cmakelists(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="debug")
        content = (module_dir / "CMakeLists.txt").read_text()
        assert 'HELIA_RT_VARIANT "debug"' in content

    def test_default_variant_unchanged(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        content = (module_dir / "CMakeLists.txt").read_text()
        assert 'HELIA_RT_VARIANT "release-with-logs"' in content

    def test_copies_dist_dirs(self, tmp_path: Path, fake_dist: Path):
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")
        assert (module_dir / "lib").is_dir()
        assert (module_dir / "tensorflow").is_dir()
        assert (module_dir / "third_party").is_dir()
        assert (module_dir / "signal").is_dir()

    def test_missing_nsx_raises(self, tmp_path: Path, fake_dist: Path):
        """A dist without nsx/nsx-module.yaml should fail."""
        import shutil

        shutil.rmtree(fake_dist / "nsx")
        module_dir = tmp_path / "module"
        module_dir.mkdir()
        with pytest.raises(EngineError, match="missing nsx/nsx-module.yaml"):
            _install_nsx_module(module_dir, fake_dist, variant="release-with-logs")


class TestHeliaRTAdapter:
    def test_name(self):
        adapter = HeliaRTAdapter()
        assert adapter.name == "heliaRT"

    def test_prepare_creates_module_dir(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        adapter.prepare(config, tmp_path)
        module_dir = tmp_path / "modules" / "helia-rt"
        assert module_dir.is_dir()
        assert (module_dir / "nsx-module.yaml").exists()
        assert (module_dir / "CMakeLists.txt").exists()
        assert (module_dir / "nsx" / "CMakeLists.txt").exists()

    def test_prepare_links_distribution(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        adapter.prepare(config, tmp_path)
        module_dir = tmp_path / "modules" / "helia-rt"
        assert (module_dir / "lib").is_dir()
        assert (module_dir / "tensorflow").is_dir()
        assert (module_dir / "third_party").is_dir()

    def test_prepare_returns_extra_module(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(tmp_path, {"config": {"dist_path": str(fake_dist)}})
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert len(artifacts.extra_modules) == 1
        mod = artifacts.extra_modules[0]
        assert mod.name == "nsx-helia-rt"
        assert mod.version == HELIART_VERSION
        assert mod.local is True
        assert mod.project == "helia-rt"
        assert mod.path.is_dir()

    def test_prepare_pins_cmsis_nn_registry_module_to_commit(
        self, tmp_path: Path, fake_cmsis_nn: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # An ambient legacy path must not override the explicit reproducible pin.
        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))
        commit = "edc4edbd81af2f3baa9354ea1e30cca50dfcfd99"
        config = _make_config(tmp_path, {"config": {"cmsis_nn_ref": commit}})

        artifacts = HeliaRTAdapter().prepare(config, tmp_path)

        cmsis_nn = next(
            module for module in artifacts.extra_modules if module.name == "nsx-cmsis-nn"
        )
        assert cmsis_nn.project == "ns-cmsis-nn"
        assert cmsis_nn.local is False
        assert cmsis_nn.ref == commit

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

    def test_prepare_no_dist_path_uses_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """With no dist_path/source_path/source configured, prepare()
        resolves nsx-helia-rt from the NSX registry (no local vendoring)."""
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert len(artifacts.extra_modules) == 1
        mod = artifacts.extra_modules[0]
        assert mod.name == "nsx-helia-rt"
        assert mod.local is False
        assert mod.project == "helia-rt"
        assert mod.ref  # pinned registry tag
        # Nothing is vendored on disk — NSX clones it from the registry.
        assert not (tmp_path / "modules" / "helia-rt").exists()
        assert not (tmp_path / "modules" / "nsx-helia-rt").exists()

    def test_registry_helia_rt_uses_explicit_cmsis_nn_checkout(
        self,
        tmp_path: Path,
        fake_cmsis_nn: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

        artifacts = HeliaRTAdapter().prepare(_make_config(tmp_path), tmp_path)

        assert [module.name for module in artifacts.extra_modules] == [
            "nsx-helia-rt",
            "nsx-cmsis-nn",
        ]
        cmsis_nn = artifacts.extra_modules[1]
        assert cmsis_nn.local is True
        assert cmsis_nn.project == "ns-cmsis-nn"
        assert cmsis_nn.path == tmp_path / "modules" / "ns-cmsis-nn"
        assert (cmsis_nn.path / "nsx-module.yaml").is_file()

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
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage
        from helia_profiler.stages.prepare_engine import PrepareEngineStage

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
        assert (work_dir / "modules" / "helia-rt" / "nsx-module.yaml").exists()
        assert (work_dir / "modules" / "helia-rt" / "lib").is_dir()


class TestSourceBuildMode:
    """heliaRT source-build mode (engine.config.source_path)."""

    def test_prepare_uses_source_path(
        self,
        tmp_path: Path,
        fake_source_tree: Path,
        fake_cmsis_nn: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage
        from helia_profiler.stages.prepare_engine import PrepareEngineStage

        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-rt",
                    "config": {"source_path": str(fake_source_tree)},
                },
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        module_dir = work_dir / "modules" / "helia-rt"
        cmake = (module_dir / "CMakeLists.txt").read_text()
        # Source-build wrapper includes the source tree's nsx/CMakeLists.txt
        # directly and points HELIA_RT_TFLM_ROOT at the source checkout.
        assert f"{fake_source_tree.as_posix()}/nsx/CMakeLists.txt" in cmake
        assert 'HELIA_RT_VARIANT "release-with-logs"' in cmake
        assert f'HELIA_RT_TFLM_ROOT "{fake_source_tree.as_posix()}"' in cmake

        # nsx-module.yaml is copied from the source tree.
        yaml_text = (module_dir / "nsx-module.yaml").read_text()
        assert "nsx-helia-rt" in yaml_text

        # No prebuilt lib/ tree was installed.
        assert not (module_dir / "lib").exists()

    def test_invalid_source_path_raises(self, tmp_path: Path):
        from helia_profiler.engines.helia_rt import _resolve_source_path

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-rt",
                    "config": {"source_path": str(tmp_path / "does_not_exist")},
                },
            },
        )
        with pytest.raises(EngineError, match="is not a directory"):
            _resolve_source_path(config)

    def test_source_path_missing_required_files(self, tmp_path: Path):
        from helia_profiler.engines.helia_rt import _resolve_source_path

        bare = tmp_path / "bare"
        bare.mkdir()
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-rt",
                    "config": {"source_path": str(bare)},
                },
            },
        )
        with pytest.raises(EngineError, match="missing required files"):
            _resolve_source_path(config)

    def test_env_var_resolves_source_path(
        self, tmp_path: Path, fake_source_tree: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.engines.helia_rt import _resolve_source_path

        monkeypatch.setenv("HELIART_SOURCE_PATH", str(fake_source_tree))
        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
            },
        )
        resolved = _resolve_source_path(config)
        assert resolved == fake_source_tree.resolve()

    def test_source_path_takes_precedence_over_dist(
        self,
        tmp_path: Path,
        fake_dist: Path,
        fake_source_tree: Path,
        fake_cmsis_nn: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When source_path is set, prebuilt dist_path is ignored entirely."""
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage
        from helia_profiler.stages.prepare_engine import PrepareEngineStage

        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {
                    "type": "helia-rt",
                    "config": {
                        "dist_path": str(fake_dist),
                        "source_path": str(fake_source_tree),
                    },
                },
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        ResolvePlatformStage().run(ctx)
        PrepareEngineStage().run(ctx)

        module_dir = work_dir / "modules" / "helia-rt"
        # Source-build path was taken — the local heliaRT module exists and no
        # prebuilt lib/ tree was installed from dist_path.
        assert (module_dir / "CMakeLists.txt").exists()
        assert not (module_dir / "lib").exists()

def test_install_nsx_module_source_points_to_source_root(tmp_path: Path, fake_source_tree: Path):
    module_dir = tmp_path / "module"
    module_dir.mkdir()

    _install_nsx_module_source(module_dir, fake_source_tree, variant="release-with-logs")

    cmake = (module_dir / "CMakeLists.txt").read_text()
    assert f'HELIA_RT_TFLM_ROOT "{fake_source_tree.as_posix()}"' in cmake
    assert not (module_dir / "_source_shim").exists()


class TestEthosUBackend:
    """engine.backend=ethos_u — NPU module + kernel flag wiring."""

    def test_registry_default_adds_npu_module_and_flag(self, tmp_path: Path):
        config = _make_config(tmp_path, {"backend": "ethos_u"})
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        names = [m.name for m in artifacts.extra_modules]
        assert names == ["nsx-helia-rt", "nsx-npu"]
        npu = artifacts.extra_modules[1]
        assert npu.local is False
        assert npu.project == "nsx-ambiq-sdk"
        assert artifacts.cmake_vars["NSX_HELIA_RT_ENABLE_ETHOSU"] == "ON"
        assert artifacts.engine_backend == "ethos_u"

    def test_source_build_adds_npu_module_and_flag(
        self, tmp_path: Path, fake_source_tree: Path
    ):
        config = _make_config(
            tmp_path,
            {"backend": "ethos_u", "config": {"source_path": str(fake_source_tree)}},
        )
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        names = [m.name for m in artifacts.extra_modules]
        assert "nsx-npu" in names
        assert artifacts.cmake_vars["NSX_HELIA_RT_ENABLE_ETHOSU"] == "ON"

    def test_prebuilt_dist_rejected(self, tmp_path: Path, fake_dist: Path):
        config = _make_config(
            tmp_path, {"backend": "ethos_u", "config": {"dist_path": str(fake_dist)}}
        )
        adapter = HeliaRTAdapter()
        with pytest.raises(EngineError, match="source build"):
            adapter.prepare(config, tmp_path)

    def test_default_backend_has_no_npu_artifacts(self, tmp_path: Path):
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert all(m.name != "nsx-npu" for m in artifacts.extra_modules)
        assert "NSX_HELIA_RT_ENABLE_ETHOSU" not in artifacts.cmake_vars
