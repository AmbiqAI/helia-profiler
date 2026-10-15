"""Unit tests for the heliaAOT adapter's Ethos-U (NPU) wiring.

``engine.backend: ethos_u`` must map the profiler board to an NPU-capable
AOT platform, propagate the backend onto ``EngineArtifacts`` (which drives
``nsx::npu`` linking in the firmware CMake template), and insert the nsx-npu
registry module *before* the generated AOT module so the driver target exists
when the AOT module's CMakeLists resolves it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.base import HeliaAotArtifacts
from helia_profiler.engines.ethos_u import NSX_NPU_MODULE, NSX_NPU_PROJECT
from helia_profiler.engines.helia_aot.adapter import HeliaAOTAdapter, _build_extra_modules
from helia_profiler.engines.helia_aot.compile import (
    _BOARD_TO_AOT_PLATFORM,
    _merged_aot_args,
    _resolve_aot_platform,
)
from helia_profiler.errors import EngineError
from helia_profiler.results import NsxModuleRef

_CMSIS_REF = NsxModuleRef(name="ns-cmsis-nn", path=Path("/tmp/cmsis"))


def _cfg(backend: str | None = None):
    engine: dict = {"type": "helia-aot"}
    if backend:
        engine["backend"] = backend
    return load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "engine": engine,
            "target": {"board": "atomiq110_fpga_turbo"},
        },
    )


class TestBoardMap:
    def test_atomiq110_maps_to_atomiq110(self):
        assert _BOARD_TO_AOT_PLATFORM["atomiq110_fpga_turbo"] == "atomiq110"

    def test_resolve_platform_for_atomiq110(self):
        assert _resolve_aot_platform(_cfg()) == "atomiq110"


class TestExtraModules:
    def test_default_backend_has_no_npu_module(self):
        mods = _build_extra_modules(_cfg(), _CMSIS_REF, "kws_model", Path("/tmp/aot"))
        assert [m.name for m in mods] == ["ns-cmsis-nn", "kws_model"]

    def test_ethos_u_backend_inserts_npu_before_aot_module(self):
        mods = _build_extra_modules(_cfg("ethos_u"), _CMSIS_REF, "kws_model", Path("/tmp/aot"))
        names = [m.name for m in mods]
        assert names == ["ns-cmsis-nn", NSX_NPU_MODULE, "kws_model"]
        assert names.index(NSX_NPU_MODULE) < names.index("kws_model")

    def test_npu_module_ref_is_registry_backed(self):
        mods = _build_extra_modules(_cfg("ethos_u"), _CMSIS_REF, "kws_model", Path("/tmp/aot"))
        npu = next(m for m in mods if m.name == NSX_NPU_MODULE)
        assert npu.local is False
        assert npu.project == NSX_NPU_PROJECT


class TestEngineBackendPropagation:
    def test_config_backend_reaches_artifacts_field(self):
        # HeliaAotArtifacts.engine_backend drives has_ethos_u in
        # firmware/project.py; ensure the dataclass accepts the field.
        from helia_profiler.engines import EngineType
        from helia_profiler.engines.base import HeliaAotArtifacts

        artifacts = HeliaAotArtifacts(
            engine_type=EngineType.HELIA_AOT,
            engine_header="fake_model.h",
            aot_prefix="fake",
            aot_module_name="fake_module",
            aot_cmake_target="nsx::fake_module",
            helia_aot_version="0.0.0",
            engine_backend="ethos_u",
        )
        assert artifacts.engine_backend == "ethos_u"
        assert artifacts.resolved_backend == "ethos_u"


class TestAttributesHeader:
    def test_atomiq110_covered_by_soc_guard(self, tmp_path):
        from helia_profiler.engines.helia_aot.compile import _write_attributes_header

        header = _write_attributes_header(tmp_path, "hpx")
        text = header.read_text()
        # Atomiq110 shares the Apollo510 (M55 + shared SRAM) section layout;
        # without this guard the placement macros silently become no-ops.
        assert "AM_PART_ATOMIQ110" in text


def _write_aot_yaml(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "aot.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _aot_cfg(engine: dict):
    return load_config(
        None,
        {"model": {"path": "m.tflite"}, "engine": {"type": "helia-aot", **engine}},
    )


class TestMergedAotArgs:
    def test_inline_aot_args_merge_over_config_path(self, tmp_path):
        aot_yaml = _write_aot_yaml(
            tmp_path, "memory:\n  allocate_arenas: true\n  planner: greedy\n"
        )
        config = _aot_cfg(
            {
                "config_path": str(aot_yaml),
                "config": {"aot_args": {"memory": {"allocate_arenas": False}}},
            }
        )
        assert _merged_aot_args(config) == {
            "memory": {"allocate_arenas": False, "planner": "greedy"}
        }

    def test_always_has_a_memory_mapping(self):
        assert _merged_aot_args(_aot_cfg({})) == {"memory": {}}

    def test_returns_a_copy_the_compiler_may_mutate(self):
        config = _aot_cfg({"config": {"aot_args": {"memory": {"allocate_arenas": False}}}})
        _merged_aot_args(config)["memory"]["tensors"] = ["injected"]
        assert config.engine.config["aot_args"] == {"memory": {"allocate_arenas": False}}

    @pytest.mark.parametrize(
        ("text", "message"),
        [
            ("memory: [\n", "not valid YAML"),
            ("- a\n", "must contain a YAML mapping"),
            ("false\n", "must contain a YAML mapping"),
            ("0\n", "must contain a YAML mapping"),
            ("[]\n", "must contain a YAML mapping"),
            ("''\n", "must contain a YAML mapping"),
            ("memory: psram\n", "memory must be a mapping"),
            ("memory:\n  tensors: psram\n", "memory.tensors must be a list"),
        ],
        ids=[
            "syntax",
            "not-mapping",
            "false",
            "zero",
            "empty-list",
            "empty-string",
            "memory",
            "tensors",
        ],
    )
    def test_malformed_config_path_raises_engine_error(self, tmp_path, text, message):
        config = _aot_cfg({"config_path": str(_write_aot_yaml(tmp_path, text))})
        with pytest.raises(EngineError, match=message):
            _merged_aot_args(config)

    @pytest.mark.parametrize("text", ["", "# comment only\n", "null\n", "~\n"])
    def test_empty_config_path_reads_as_no_config(self, tmp_path, text):
        config = _aot_cfg({"config_path": str(_write_aot_yaml(tmp_path, text))})
        assert _merged_aot_args(config) == {"memory": {}}

    def test_non_utf8_config_path_raises_engine_error(self, tmp_path):
        path = tmp_path / "aot.yaml"
        path.write_bytes(b"memory:\n  planner: \xff\xfe\n")
        config = _aot_cfg({"config_path": str(path)})
        with pytest.raises(EngineError, match="not valid UTF-8"):
            _merged_aot_args(config)


class TestPrepareArtifactFlags:
    """``prepare()`` derives its artifact flags from the merged AOT args."""

    def _prepare(self, monkeypatch, tmp_path, config, model=None) -> HeliaAotArtifacts:
        from helia_profiler.engines.helia_aot import adapter as adapter_mod

        def fake_run_aot_compiler(config, output_dir, module_name, *_args):
            (output_dir / module_name).mkdir(parents=True)
            from types import SimpleNamespace

            return SimpleNamespace(model=model)

        monkeypatch.setattr(adapter_mod, "_check_helia_aot_version", lambda **_: "0.0.0")
        monkeypatch.setattr(adapter_mod, "_resolve_aot_platform", lambda _c: "apollo510_evb")
        monkeypatch.setattr(adapter_mod, "_run_aot_compiler", fake_run_aot_compiler)
        monkeypatch.setattr(adapter_mod, "_extract_operator_manifest", lambda _ctx: [])
        monkeypatch.setattr(adapter_mod, "_validate_pragmas", lambda *_a: None)
        monkeypatch.setattr(adapter_mod, "cmsis_nn_module_ref", lambda *_a: _CMSIS_REF)
        monkeypatch.setattr(adapter_mod, "_write_attributes_header", lambda d, p: d / f"{p}.h")
        monkeypatch.setattr(adapter_mod, "_extract_memory_plan", lambda *_a, **_k: None)
        monkeypatch.setattr(adapter_mod, "_extract_arena_regions", lambda *_a: [])
        return HeliaAOTAdapter().prepare(config, tmp_path / "work")

    def test_config_path_allocate_arenas_false(self, monkeypatch, tmp_path):
        aot_yaml = _write_aot_yaml(tmp_path, "memory:\n  allocate_arenas: false\n")
        artifacts = self._prepare(monkeypatch, tmp_path, _aot_cfg({"config_path": str(aot_yaml)}))
        assert artifacts.aot_allocate_arenas is False
        assert artifacts.aot_user_memory_config is True

    def test_inline_tensor_rules(self, monkeypatch, tmp_path):
        rule = {"type": "scratch", "attributes": {"memory": "sram"}}
        config = _aot_cfg({"config": {"aot_args": {"memory": {"tensors": [rule]}}}})
        artifacts = self._prepare(monkeypatch, tmp_path, config)
        assert artifacts.aot_allocate_arenas is True
        assert artifacts.aot_user_memory_config is True

    def test_inline_allocate_arenas_only(self, monkeypatch, tmp_path):
        config = _aot_cfg({"config": {"aot_args": {"memory": {"allocate_arenas": False}}}})
        artifacts = self._prepare(monkeypatch, tmp_path, config)
        assert artifacts.aot_allocate_arenas is False
        assert artifacts.aot_user_memory_config is False

    def test_defaults_without_aot_config(self, monkeypatch, tmp_path):
        artifacts = self._prepare(monkeypatch, tmp_path, _aot_cfg({}))
        assert artifacts.aot_allocate_arenas is True
        assert artifacts.aot_user_memory_config is False

    def test_retains_compiled_graph_costs(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from helia_profiler.modelcost import model_analysis

        kinds = SimpleNamespace(CONV_2D="conv", DEPTHWISE_CONV_2D="dw", FULLY_CONNECTED="fc")
        monkeypatch.setattr(model_analysis, "_AirOpType", kinds)
        tensors = {
            "in": SimpleNamespace(shape=[1, 4]),
            "weights": SimpleNamespace(shape=[3, 4]),
            "out": SimpleNamespace(shape=[1, 3]),
        }
        model = SimpleNamespace(
            operators=[
                SimpleNamespace(
                    id="5",
                    op_type=kinds.FULLY_CONNECTED,
                    input_ids=["in", "weights"],
                    output_ids=["out"],
                    named_tensors={"weights": "weights"},
                )
            ],
            get_tensor=tensors.get,
        )
        artifacts = self._prepare(monkeypatch, tmp_path, _aot_cfg({}), model)
        analysis = artifacts.compiled_analysis
        assert analysis is not None
        assert analysis.index_space == "execution"
        assert analysis.total_macs == 12
        assert analysis.total_ops == 24
        assert analysis.layers[0].id == 0
        assert analysis.layers[0].original_id == 5
