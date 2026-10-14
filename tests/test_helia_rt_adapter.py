"""Tests for the heliaRT engine adapter and NSX module installation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
from helia_profiler.results import NsxModuleRef


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


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "unread_file",
        "cmsis_nn_path",
        "cmsis_nn_ref",
        "CMSIS_NN_PATH",
        "variant_debug",
        "variant_release",
        "variant_bogus",
        "variant_release-with-logs",
        "core_override",
        "requantize_false",
        "requantize_true",
        "wrong_stack",
        "wrong_backend",
    ],
)
def test_prepared_settings_before_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
):
    monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    options = {}
    if case in ("cmsis_nn_path", "cmsis_nn_ref"):
        options[case] = str(tmp_path / "kernels") if case == "cmsis_nn_path" else "frozen-ref"
    elif case == "CMSIS_NN_PATH":
        monkeypatch.setenv(case, str(tmp_path / "kernels"))
    elif case.startswith("variant_"):
        options["variant"] = case.removeprefix("variant_")
    elif case == "core_override":
        options[case] = "cm4"
    elif case.startswith("requantize_"):
        options["cmsis_nn_requantize_inline_asm"] = case == "requantize_true"
    engine = {"backend": "cmsis_nn" if case == "wrong_backend" else "helia", "config": options}
    if case in ("unread_file", "wrong_stack", "wrong_backend"):
        engine["config_path"] = str(tmp_path / "unread.yml")
    config = _make_config(tmp_path, engine)
    runtime = SimpleNamespace(
        record=SimpleNamespace(
            stack="upstream" if case == "wrong_stack" else "helia-rt",
            providers=[SimpleNamespace(name="helia-rt", revision="frozen-revision")],
            abi=SimpleNamespace(toolchain="gcc"),
        )
    )
    module = NsxModuleRef.registry("prepared-helia", "prepared-helia")
    stage = Mock(return_value=module)
    monkeypatch.setattr("helia_profiler.fixture_runtime.prepare_runtime_module", stage)
    work_dir = tmp_path / "work"
    if case == "valid":
        artifacts = HeliaRTAdapter().prepare_runtime(config, work_dir, runtime)
        stage.assert_called_once_with(runtime, work_dir)
        assert artifacts.extra_modules == [module]
        assert artifacts.engine_header == "tensorflow/lite/micro/micro_interpreter.h"
        assert artifacts.engine_backend == "helia"
        assert artifacts.heliart_variant == "prepared"
        assert artifacts.heliart_version == "prepared:frozen-revision"
        assert artifacts.heliart_toolchain_tag == "gcc"
        assert artifacts.cmake_vars == {}
    else:
        message = (
            "Prepared heliaRT requires"
            if case in ("wrong_stack", "wrong_backend")
            else "config_path"
            if case == "unread_file"
            else next(iter(options))
            if options
            else case
        )
        with pytest.raises(EngineError, match=message):
            HeliaRTAdapter().prepare_runtime(config, work_dir, runtime)
        stage.assert_not_called()
        assert not work_dir.exists()


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
        assert "../CMakeLists.txt" in shim.read_text()

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
        resolves nsx-helia-rt from the NSX registry (no local vendoring) and
        declares nsx-cmsis-nn at the baseline's qualified ref."""
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert [module.name for module in artifacts.extra_modules] == [
            "nsx-helia-rt",
            "nsx-cmsis-nn",
        ]
        mod = artifacts.extra_modules[0]
        assert mod.name == "nsx-helia-rt"
        assert mod.local is False
        assert mod.project == "helia-rt"
        assert mod.ref  # pinned registry tag
        # Nothing is vendored on disk — NSX clones it from the registry.
        assert not (tmp_path / "modules" / "helia-rt").exists()
        assert not (tmp_path / "modules" / "nsx-helia-rt").exists()

    @pytest.mark.parametrize("variant", ["debug", "release-with-logs"])
    def test_registry_passes_variant_to_cmake(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
    ):
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        config = _make_config(tmp_path, {"config": {"variant": variant}})

        artifacts = HeliaRTAdapter().prepare(config, tmp_path)

        assert artifacts.cmake_vars["HELIA_RT_VARIANT"] == variant
        assert artifacts.heliart_variant == variant

    def test_release_variant_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        config = _make_config(tmp_path, {"config": {"variant": "release"}})

        with pytest.raises(EngineError, match="strips per-op profiling"):
            HeliaRTAdapter().prepare(config, tmp_path)

    def test_registry_rejects_core_override(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.delenv("HELIART_DIST_PATH", raising=False)
        monkeypatch.delenv("HELIART_SOURCE_PATH", raising=False)
        config = _make_config(tmp_path, {"config": {"core_override": "cm4"}})

        work_dir = tmp_path / "work"
        with pytest.raises(EngineError, match="core_override.*prebuilt"):
            HeliaRTAdapter().prepare(config, work_dir)

        assert not work_dir.exists()

    @pytest.mark.parametrize("route", ["registry", "source", "prebuilt"])
    def test_rejects_unread_config_file_before_resolution(
        self,
        tmp_path: Path,
        fake_source_tree: Path,
        fake_dist: Path,
        monkeypatch: pytest.MonkeyPatch,
        route: str,
    ):
        config_file = tmp_path / "engine.yml"
        config_file.write_text("variant: debug\n", encoding="utf-8")
        options = {"source_path": str(fake_source_tree)} if route == "source" else {}
        if route == "prebuilt":
            options["dist_path"] = str(fake_dist)
        config = _make_config(tmp_path, {"config_path": str(config_file), "config": options})

        def unexpected_resolution(_config):
            pytest.fail("engine resolution must not run for an unread config file")

        monkeypatch.setattr(
            "helia_profiler.engines.helia_rt.adapter._resolve_source_path",
            unexpected_resolution,
        )
        work_dir = tmp_path / "work"
        with pytest.raises(EngineError, match="config_path"):
            HeliaRTAdapter().prepare(config, work_dir)
        assert not work_dir.exists()

    @pytest.mark.parametrize("source_option", ["source_path", "HELIART_SOURCE_PATH"])
    def test_source_rejects_core_override_even_with_prebuilt_distribution(
        self,
        tmp_path: Path,
        fake_source_tree: Path,
        fake_dist: Path,
        monkeypatch: pytest.MonkeyPatch,
        source_option: str,
    ):
        options = {"dist_path": str(fake_dist), "core_override": "cm4"}
        if source_option == "source_path":
            options["source_path"] = str(fake_source_tree)
        else:
            monkeypatch.setenv(source_option, str(fake_source_tree))
        config = _make_config(tmp_path, {"config": options})
        work_dir = tmp_path / "work"
        with pytest.raises(EngineError, match="core_override.*prebuilt"):
            HeliaRTAdapter().prepare(config, work_dir)
        assert not work_dir.exists()

    @pytest.mark.parametrize("core", ["cm4", "cm55"])
    @pytest.mark.parametrize("variant", ["debug", "release-with-logs"])
    @pytest.mark.parametrize("dist_option", ["dist_path", "HELIART_DIST_PATH", "source"])
    def test_prebuilt_core_and_variant_reach_wrapper(
        self,
        tmp_path: Path,
        fake_dist: Path,
        core: str,
        variant: str,
        monkeypatch: pytest.MonkeyPatch,
        dist_option: str,
    ):
        options: dict[str, object] = {"core_override": core, "variant": variant}
        if dist_option == "HELIART_DIST_PATH":
            monkeypatch.setenv(dist_option, str(fake_dist))
        elif dist_option == "source":
            options[dist_option] = {"repo": "vendor/helia-rt", "ref": "custom-release"}
            monkeypatch.setattr(
                "helia_profiler.engines.helia_rt.adapter._resolve_distribution",
                lambda _config: (fake_dist, HELIART_VERSION),
            )
        else:
            options[dist_option] = str(fake_dist)
        config = _make_config(tmp_path, {"config": options})
        work_dir = tmp_path / "work"
        artifacts = HeliaRTAdapter().prepare(config, work_dir)
        cmake = (work_dir / "modules/helia-rt/CMakeLists.txt").read_text(encoding="utf-8")
        assert f'set(_HELIA_RT_CORE "{core}")' in cmake
        assert f'HELIA_RT_VARIANT "{variant}"' in cmake
        assert artifacts.heliart_variant == variant

    def test_custom_backend_and_engine_settings_remain_engine_owned(self, tmp_path: Path):
        config = _make_config(
            tmp_path,
            {
                "backend": "vendor-custom",
                "config": {"vendor_option": True},
            },
        )
        artifacts = HeliaRTAdapter().prepare(config, tmp_path / "work")
        assert artifacts.engine_backend == "vendor-custom"
        assert config.engine.config["vendor_option"] is True

    @pytest.mark.parametrize("dist_option", ["dist_path", "HELIART_DIST_PATH", "source"])
    @pytest.mark.parametrize("selector", ["cmsis_nn_path", "cmsis_nn_ref", "CMSIS_NN_PATH"])
    def test_prebuilt_rejects_ineffective_cmsis_nn_override_before_resolution(
        self,
        tmp_path: Path,
        fake_dist: Path,
        monkeypatch: pytest.MonkeyPatch,
        dist_option: str,
        selector: str,
    ):
        options: dict[str, object] = {}
        if dist_option == "HELIART_DIST_PATH":
            monkeypatch.setenv(dist_option, str(fake_dist))
        elif dist_option == "source":
            options[dist_option] = {"repo": "vendor/helia-rt", "ref": "custom-release"}
        else:
            options[dist_option] = str(fake_dist)
        requested = "custom-ref" if selector == "cmsis_nn_ref" else str(tmp_path / "nn")
        if selector == "CMSIS_NN_PATH":
            monkeypatch.setenv(selector, requested)
            diagnostic = "env.CMSIS_NN_PATH"
        else:
            options[selector] = requested
            diagnostic = f"engine.config.{selector}"
        config = _make_config(tmp_path, {"config": options})

        def unexpected_resolution(_config):
            pytest.fail("prebuilt resolution must not run for an ineffective CMSIS-NN override")

        monkeypatch.setattr(
            "helia_profiler.engines.helia_rt.adapter._resolve_distribution",
            unexpected_resolution,
        )
        work_dir = tmp_path / "work"
        with pytest.raises(EngineError, match="prebuilt.*cannot apply") as excinfo:
            HeliaRTAdapter().prepare(config, work_dir)
        assert diagnostic in str(excinfo.value)
        assert "registry/source build" in (excinfo.value.hint or "")
        assert not work_dir.exists()

    @pytest.mark.parametrize("route", ["registry", "source"])
    @pytest.mark.parametrize("selector", ["cmsis_nn_path", "cmsis_nn_ref", "CMSIS_NN_PATH"])
    def test_source_routes_apply_cmsis_nn_selectors(
        self,
        tmp_path: Path,
        fake_source_tree: Path,
        fake_dist: Path,
        fake_cmsis_nn: Path,
        monkeypatch: pytest.MonkeyPatch,
        route: str,
        selector: str,
    ):
        options: dict[str, object] = {}
        if route == "source":
            options.update(source_path=str(fake_source_tree), dist_path=str(fake_dist))
        requested = "custom-ref" if selector == "cmsis_nn_ref" else str(fake_cmsis_nn)
        if selector == "CMSIS_NN_PATH":
            monkeypatch.setenv(selector, requested)
        else:
            options[selector] = requested
        config = _make_config(tmp_path, {"config": options})
        artifacts = HeliaRTAdapter().prepare(config, tmp_path / "work")
        module = next(m for m in artifacts.extra_modules if m.name == "nsx-cmsis-nn")
        if selector == "cmsis_nn_ref":
            assert module.ref == requested
            assert not module.local
        else:
            assert module.local
            assert module.path is not None
            assert (module.path / "Source" / "stub.c").is_file()

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
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.prepare_engine import PrepareEngineStage
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage

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
        from helia_profiler.stages.prepare_engine import PrepareEngineStage
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage

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

        yaml_text = (module_dir / "nsx-module.yaml").read_text()
        assert "nsx-helia-rt" in yaml_text

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
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.prepare_engine import PrepareEngineStage
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage

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
        # cmsis-nn is declared unconditionally for the registry-default path
        # regardless of backend — the NPU module is appended after it.
        assert names == ["nsx-helia-rt", "nsx-cmsis-nn", "nsx-npu"]
        npu = artifacts.extra_modules[2]
        assert npu.local is False
        assert npu.project == "nsx-ambiq-sdk"
        assert artifacts.cmake_vars["NSX_HELIA_RT_ENABLE_ETHOSU"] == "ON"
        assert artifacts.engine_backend == "ethos_u"

    def test_source_build_adds_npu_module_and_flag(self, tmp_path: Path, fake_source_tree: Path):
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

    # An older heliaRT tree has Ethos-U in its root build but no NSX wrapper
    # option (added by heliaRT #191), so the flag would be silently ignored.
    _NSX_117 = (
        "# source-build heliaRT nsx CMakeLists (test stub)\n"
        "add_library(nsx_helia_rt INTERFACE)\n"
        "add_library(nsx::helia_rt ALIAS nsx_helia_rt)\n"
    )

    @staticmethod
    def _source_with(tree: Path, nsx_cmake: str, version: str | None) -> Path:
        (tree / "nsx" / "CMakeLists.txt").write_text(nsx_cmake)
        header = tree / "tensorflow" / "lite" / "micro" / "helia_rt_version.h"
        # A development tree may carry the header without a parsable version.
        header.write_text(
            "// development tree\n"
            if version is None
            else f'#define HELIA_RT_VERSION "v{version}"\n'
        )
        return tree

    @pytest.mark.parametrize(
        ("nsx_cmake", "version"),
        [
            (_NSX_117, "1.17.0"),
            (_NSX_117 + '# option(NSX_HELIA_RT_ENABLE_ETHOSU "commented out" OFF)\n', "1.17.0"),
            (_NSX_117 + 'option(HELIA_RT_ENABLE_ETHOSU "root option only" OFF)\n', "1.17.0"),
            (_NSX_117 + 'option(NSX_HELIA_RT_ENABLE_ETHOSU_EXTRA "longer name" OFF)\n', "1.17.0"),
            # The probe, not the tag, decides: a tree that claims a new
            # version or none must still declare the option.
            (_NSX_117, "1.20.0"),
            (_NSX_117, None),
        ],
        ids=[
            "no-option",
            "commented-out",
            "root-option-only",
            "longer-name",
            "new-tag-no-option",
            "unknown-version-no-option",
        ],
    )
    def test_source_without_the_nsx_option_is_refused(
        self, tmp_path: Path, fake_source_tree: Path, nsx_cmake: str, version: str | None
    ):
        source = self._source_with(fake_source_tree, nsx_cmake, version)
        config = _make_config(
            tmp_path, {"backend": "ethos_u", "config": {"source_path": str(source)}}
        )

        with pytest.raises(
            EngineError, match="does not declare NSX_HELIA_RT_ENABLE_ETHOSU"
        ) as excinfo:
            HeliaRTAdapter().prepare(config, tmp_path)

        assert f"version {version or 'unknown'}" in str(excinfo.value)
        assert "helia-rt-v1.18.0" in (excinfo.value.hint or "")
        assert "HELIART_SOURCE_PATH" in (excinfo.value.hint or "")
        assert "engine.config.source_path" in (excinfo.value.hint or "")
        assert not (tmp_path / "modules" / "helia-rt" / "CMakeLists.txt").exists()

    @pytest.mark.parametrize(
        "version",
        ["1.18.0", None, "1.17.0"],
        ids=["tagged", "unknown-dev-tree", "old-tag-with-backported-option"],
    )
    def test_source_with_the_nsx_option_builds_the_npu_path(
        self, tmp_path: Path, fake_source_tree: Path, version: str | None
    ):
        source = self._source_with(
            fake_source_tree,
            self._NSX_117 + '  option( NSX_HELIA_RT_ENABLE_ETHOSU "Ethos-U" OFF)\n',
            version,
        )
        config = _make_config(
            tmp_path, {"backend": "ethos_u", "config": {"source_path": str(source)}}
        )

        artifacts = HeliaRTAdapter().prepare(config, tmp_path)

        assert artifacts.cmake_vars["NSX_HELIA_RT_ENABLE_ETHOSU"] == "ON"
        assert "nsx-npu" in [m.name for m in artifacts.extra_modules]

    def test_cpu_backend_still_builds_a_117_source(self, tmp_path: Path, fake_source_tree: Path):
        source = self._source_with(fake_source_tree, self._NSX_117, "1.17.0")
        config = _make_config(tmp_path, {"config": {"source_path": str(source)}})

        artifacts = HeliaRTAdapter().prepare(config, tmp_path)

        assert "NSX_HELIA_RT_ENABLE_ETHOSU" not in artifacts.cmake_vars

    def test_registry_pin_carries_the_nsx_ethos_u_option(self):
        from helia_profiler.engines.helia_rt.artifacts import HELIART_VERSION
        from helia_profiler.engines.semver import parse_semver

        assert parse_semver(HELIART_VERSION) >= (1, 18, 0)

    def test_unreadable_nsx_wrapper_is_refused_with_its_reason(
        self, tmp_path: Path, fake_source_tree: Path, monkeypatch: pytest.MonkeyPatch
    ):
        nsx = fake_source_tree / "nsx" / "CMakeLists.txt"
        real_read_text = Path.read_text

        def read_text(self: Path, *args, **kwargs) -> str:
            if self == nsx:
                raise PermissionError(13, "Permission denied")
            return real_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read_text)
        config = _make_config(
            tmp_path, {"backend": "ethos_u", "config": {"source_path": str(fake_source_tree)}}
        )

        with pytest.raises(EngineError, match=r"could not be read \(Permission denied\)"):
            HeliaRTAdapter().prepare(config, tmp_path)

    def test_default_backend_has_no_npu_artifacts(self, tmp_path: Path):
        config = _make_config(tmp_path)
        adapter = HeliaRTAdapter()
        artifacts = adapter.prepare(config, tmp_path)
        assert all(m.name != "nsx-npu" for m in artifacts.extra_modules)
        assert "NSX_HELIA_RT_ENABLE_ETHOSU" not in artifacts.cmake_vars
