"""Tests for the heliaML v2 engine adapter and firmware generation.

heliaML is not (yet) a declared dependency of helia-profiler — these
tests mock the ``heliaml.emit.manifest`` import boundary
(``_import_heliaml_manifest``) rather than requiring a real heliaML
install, matching this repo's "tests should be fast, local, and mock
external tools" convention (AGENTS.md). The module *directory* fixtures
are real files with real hashes, because the hash-verification path is
the point.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines import EngineType, get_adapter
from helia_profiler.engines.helia_ml.adapter import HeliaMLAdapter, check_helia_ml_artifact
from helia_profiler.errors import ConfigError, EngineError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import ArenaRole, Placement
from helia_profiler.stages.prepare_engine import PrepareEngineStage
from helia_profiler.stages.resolve_platform import ResolvePlatformStage

_RUN_SIGNATURES = {
    "scores": "(const float *input, float *scores, size_t *out_class)",
    "class": "(const float *input, size_t *out_class)",
    "value": "(const float *input, float *out_value)",
}


class _FakeManifestModule:
    """Stand-in for ``heliaml.emit.manifest``.

    ``load()`` reads the manifest.json the fixture wrote — the real
    loader verifies array hashes too, but the adapter's own job (the
    module-file hashes, the schema gate, the module-block checks) is
    what these tests exercise.
    """

    def __init__(self, *, raise_on_load: Exception | None = None):
        self._raise_on_load = raise_on_load
        self.load_calls: list[Path] = []

    def load(self, path):
        self.load_calls.append(Path(path))
        if self._raise_on_load is not None:
            raise self._raise_on_load
        manifest = json.loads((Path(path) / "manifest.json").read_text())
        return manifest, {}


def _write_module_dir(
    tmp_path: Path,
    *,
    name: str = "gesture",
    run_shape: str = "scores",
    schema_version: int = 2,
    integration: str = "nsx",
    with_module: bool = True,
) -> Path:
    """A v2 generated-module directory with self-consistent hashes."""
    model_dir = tmp_path / "model"
    model_dir.mkdir(exist_ok=True)
    contents = {
        f"{name}_model.c": b"/* glue */\n",
        f"{name}_model.h": b"/* entry points */\n",
        f"{name}_params.h": b"/* weights */\n",
        "nsx-module.yaml": b"module:\n  name: helia-ml-model\n",
        "CMakeLists.txt": b"# module build\n",
    }
    files = {}
    for filename, content in contents.items():
        (model_dir / filename).write_bytes(content)
        files[filename] = hashlib.sha256(content).hexdigest()
    manifest: dict = {
        "schema_version": schema_version,
        "family": "AffineClassifier",
        "memory": {
            "arrays": [
                {"name": "weights", "bytes": 64, "role": "constant", "preferred_alignment": 16},
                {"name": "bias", "bytes": 8, "role": "constant", "preferred_alignment": 16},
            ],
            "parameter_bytes": 72,
        },
    }
    if with_module:
        manifest["module"] = {
            "integration": integration,
            "header": f"{name}_model.h",
            "source": f"{name}_model.c",
            "kind": "classifier" if run_shape != "value" else "regressor",
            "run_signature": _RUN_SIGNATURES[run_shape],
            "entry_points": {"init": f"{name}_model_init", "run": f"{name}_model_run"},
            "input_count": 8,
            "output_count": 2,
            "files": files,
        }
    (model_dir / "manifest.json").write_text(json.dumps(manifest))
    (model_dir / "arrays.npz").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    return model_dir


def _heliaml_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "heliaml_checkout"
    (root / "nsx").mkdir(parents=True)
    (root / "nsx" / "nsx-module.yaml").write_text("module:\n  name: helia-ml\n")
    return root


def _config(tmp_path: Path, model_path: Path, *, heliaml_root: Path | None = None):
    engine: dict = {"type": "helia-ml"}
    if heliaml_root is not None:
        engine["config"] = {"heliaml_root": str(heliaml_root)}
    return load_config(None, {"model": {"path": str(model_path)}, "engine": engine})


_IMPORT = "helia_profiler.engines.helia_ml.adapter._import_heliaml_manifest"


def test_registered_as_first_class_engine_type():
    assert isinstance(get_adapter(EngineType.HELIA_ML), HeliaMLAdapter)
    assert EngineType("helia-ml") is EngineType.HELIA_ML
    assert EngineType.HELIA_ML.short_slug == "ml"


class TestValidateModel:
    def test_delegates_to_heliaml_loader(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        fake = _FakeManifestModule()
        with patch(_IMPORT, return_value=fake):
            check_helia_ml_artifact(model_dir)
        assert fake.load_calls == [model_dir]

    def test_loader_rejection_becomes_config_error_with_hint(self, tmp_path: Path):
        model_dir = tmp_path / "not_a_model"
        model_dir.mkdir()
        fake = _FakeManifestModule(raise_on_load=ValueError("no manifest.json here"))
        with patch(_IMPORT, return_value=fake):
            with pytest.raises(ConfigError, match="Not a valid heliaML artifact") as exc_info:
                check_helia_ml_artifact(model_dir)
        assert exc_info.value.hint is not None
        assert "export_module" in exc_info.value.hint

    def test_weights_only_artifact_is_refused_with_export_module_hint(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path, with_module=False)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(ConfigError, match="no generated module") as exc_info:
                check_helia_ml_artifact(model_dir)
        assert "export_module" in (exc_info.value.hint or "")

    def test_non_nsx_module_is_refused(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path, integration="cmake")
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(ConfigError, match="integration='cmake'"):
                check_helia_ml_artifact(model_dir)

    def test_schema_version_gate_names_the_supported_version(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path, schema_version=3)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(ConfigError, match="schema_version=3.*supports 2"):
                check_helia_ml_artifact(model_dir)

    def test_missing_heliaml_import_raises_actionable_engine_error(self, tmp_path: Path):
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        with patch(
            _IMPORT,
            side_effect=EngineError("heliaml is not importable.", hint="install it"),
        ):
            with pytest.raises(EngineError, match="not importable"):
                check_helia_ml_artifact(model_dir)


class TestPrepare:
    def _prepare(self, tmp_path: Path, model_dir: Path):
        root = _heliaml_checkout(tmp_path)
        config = _config(tmp_path, model_dir, heliaml_root=root)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            artifacts = HeliaMLAdapter().prepare(config, tmp_path / "work")
        return artifacts, root

    def test_prepare_consumes_the_module_directory_as_is(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        artifacts, root = self._prepare(tmp_path, model_dir)

        assert artifacts.engine_type is EngineType.HELIA_ML
        names = {m.name: m for m in artifacts.extra_modules}
        # The library rides through a generated wrapper whose CMakeLists
        # reaches the checkout by absolute path (a vendored copy cannot
        # use a relative ..).
        wrapper = names["helia-ml"].path
        assert (wrapper / "nsx-module.yaml").is_file()
        cmake = (wrapper / "CMakeLists.txt").read_text()
        assert root.as_posix() in cmake
        assert "nsx::helia_ml" in cmake
        # No wrapper generation for the model: its directory IS the module.
        assert names["helia-ml-model"].path == model_dir
        assert artifacts.engine_header == "gesture_model.h"
        assert artifacts.aot_prefix == "gesture"
        assert artifacts.helia_ml_run_shape == "scores"
        assert artifacts.aot_cmake_target == "nsx::helia_ml_model"
        assert artifacts.aot_allocate_arenas is False
        # heliaML has no per-operator manifest: whole-model timing only.
        # Under the #162 artifact split that is an absent field, not a None.
        with pytest.raises(AttributeError):
            artifacts.aot_op_manifest

    @pytest.mark.parametrize("run_shape", ["scores", "class", "value"])
    def test_prepare_maps_every_run_signature(self, tmp_path: Path, run_shape: str):
        model_dir = _write_module_dir(tmp_path, run_shape=run_shape)
        artifacts, _ = self._prepare(tmp_path, model_dir)
        assert artifacts.helia_ml_run_shape == run_shape

    def test_prepare_reports_constant_memory_from_the_arrays_block(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        artifacts, _ = self._prepare(tmp_path, model_dir)
        regions = artifacts.aot_arena_regions
        assert len(regions) == 1
        assert regions[0].role is ArenaRole.CONSTANT
        assert regions[0].size == 72
        assert regions[0].placement is Placement.MRAM

    def test_prepare_supplies_an_honest_memory_plan(self, tmp_path: Path):
        # No invented tensor arena: v2 parameters are static const in the
        # image, so the plan carries one weights consumer and nothing else.
        model_dir = _write_module_dir(tmp_path)
        artifacts, _ = self._prepare(tmp_path, model_dir)
        plan = artifacts.memory_plan
        assert plan is not None
        assert plan.model_weight_bytes == 72
        assert len(plan.regions) == 1
        consumers = plan.regions[0].consumers
        assert [c.name for c in consumers] == ["model_parameters"]
        assert consumers[0].size == 72
        assert not any(c.name == "tensor_arena" for c in consumers)

    def test_prepare_rejects_a_tampered_generated_file(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        (model_dir / "gesture_model.c").write_bytes(b"/* edited by hand */\n")
        root = _heliaml_checkout(tmp_path)
        config = _config(tmp_path, model_dir, heliaml_root=root)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(EngineError, match="does not match the manifest"):
                HeliaMLAdapter().prepare(config, tmp_path / "work")

    def test_prepare_rejects_a_missing_generated_file(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        (model_dir / "gesture_model.c").unlink()
        root = _heliaml_checkout(tmp_path)
        config = _config(tmp_path, model_dir, heliaml_root=root)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(EngineError, match="missing"):
                HeliaMLAdapter().prepare(config, tmp_path / "work")

    def test_prepare_requires_heliaml_root(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        config = _config(tmp_path, model_dir)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(EngineError, match="checkout not found"):
                HeliaMLAdapter().prepare(config, tmp_path / "work")

    def test_prepare_resolves_heliaml_root_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        model_dir = _write_module_dir(tmp_path)
        root = _heliaml_checkout(tmp_path)
        monkeypatch.setenv("HELIAML_ROOT", str(root))
        config = _config(tmp_path, model_dir)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            artifacts = HeliaMLAdapter().prepare(config, tmp_path / "work")
        cmake = (artifacts.extra_modules[0].path / "CMakeLists.txt").read_text()
        assert root.as_posix() in cmake

    def test_unknown_run_signature_is_refused(self, tmp_path: Path):
        model_dir = _write_module_dir(tmp_path)
        manifest = json.loads((model_dir / "manifest.json").read_text())
        manifest["module"]["run_signature"] = "(const double *input)"
        (model_dir / "manifest.json").write_text(json.dumps(manifest))
        root = _heliaml_checkout(tmp_path)
        config = _config(tmp_path, model_dir, heliaml_root=root)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            with pytest.raises(EngineError, match="run signature"):
                HeliaMLAdapter().prepare(config, tmp_path / "work")


class TestPlacementHooks:
    def test_arena_placement_override_is_a_no_op(self):
        # v2 scratch is compiled-in static state (HELIAML_SCRATCH_SECTION):
        # a run-time override cannot move it, so the regions must come back
        # untouched rather than pretending the placement changed.
        from helia_profiler.engines.base import ArenaRegion

        regions = [
            ArenaRegion(
                region_id=0,
                name="helia_ml_constant_bytes",
                enum_name="HELIA_ML_ARENA_CONSTANT",
                size=72,
                alignment=16,
                role=ArenaRole.CONSTANT,
                memory="mram",
                placement=Placement.MRAM,
            )
        ]
        out = HeliaMLAdapter().apply_arena_placement_override(regions, Placement.TCM)
        assert out == regions

    def test_default_auto_placement_falls_through(self):
        assert (
            HeliaMLAdapter().default_auto_placement(tcm_cap=1 << 20, sram_cap=1 << 20)
            is None
        )


class TestFirmwareGeneration:
    """The full generate_app path for a heliaML model directory."""

    def _generate(self, tmp_path: Path, *, run_shape: str = "scores"):
        from helia_profiler.firmware import generate_app

        model_dir = _write_module_dir(tmp_path, run_shape=run_shape)
        root = _heliaml_checkout(tmp_path)
        config = load_config(
            None,
            {
                "model": {"path": str(model_dir)},
                "engine": {
                    "type": "helia-ml",
                    "config": {"heliaml_root": str(root)},
                },
                "target": {"board": "apollo510_evb"},
                "work_dir": str(tmp_path / "work"),
            },
        )
        work_dir = tmp_path / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        ctx = PipelineContext(config=config, work_dir=work_dir)
        with patch(_IMPORT, return_value=_FakeManifestModule()):
            ResolvePlatformStage().run(ctx)
            PrepareEngineStage().run(ctx)
            app_dir = generate_app(ctx)
        return app_dir, ctx

    def test_generates_a_whole_model_main(self, tmp_path: Path):
        app_dir, ctx = self._generate(tmp_path)
        main = (app_dir / "src" / "main.cc").read_text()
        assert "HPX_ENGINE=helia_ml" in main
        assert "gesture_model_init()" in main
        assert "gesture_model_run(hpx_input, hpx_scores, &hpx_out_class)" in main
        assert 'model_run:0' in main
        assert "GESTURE_INPUT_COUNT" in main
        # No per-op machinery, no model byte embedding, no TFLM profiler.
        assert "bind_arena" not in main
        assert not (app_dir / "src" / "model_data.h").exists()
        assert not (app_dir / "src" / "hpx_pmu_profiler.cc").exists()
        # The model directory rides along as a vendored NSX module.
        assert (app_dir / "modules" / "helia-ml-model" / "nsx-module.yaml").exists()
        assert (app_dir / "modules" / "helia-ml" / "nsx-module.yaml").exists()

    def test_scoreless_and_regressor_shapes_render(self, tmp_path: Path):
        for run_shape, expected in (
            ("class", "gesture_model_run(hpx_input, &hpx_out_class)"),
            ("value", "gesture_model_run(hpx_input, &hpx_out_value)"),
        ):
            case_dir = tmp_path / run_shape
            case_dir.mkdir()
            app_dir, _ = self._generate(case_dir, run_shape=run_shape)
            main = (app_dir / "src" / "main.cc").read_text()
            assert expected in main, run_shape

    def test_cmake_links_the_model_module(self, tmp_path: Path):
        app_dir, _ = self._generate(tmp_path)
        cmake = (app_dir / "CMakeLists.txt").read_text()
        assert "nsx::helia_ml_model" in cmake
        assert "hpx_pmu_profiler.cc" not in cmake

    def test_model_info_digests_the_directory(self, tmp_path: Path):
        # ResolvePlatformStage must not read_bytes() a directory: the model
        # digest comes from manifest.json, which itself hashes everything.
        _, ctx = self._generate(tmp_path)
        assert ctx.run_metadata.model is not None
        assert ctx.run_metadata.model.name == "model"
        assert ctx.run_metadata.model.size_bytes > 0
        assert len(ctx.run_metadata.model.sha256) == 64
