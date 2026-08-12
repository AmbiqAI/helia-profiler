"""Unit tests for the heliaAOT adapter's Ethos-U (NPU) wiring.

``engine.backend: ethos_u`` must map the profiler board to an NPU-capable
AOT platform, propagate the backend onto ``EngineArtifacts`` (which drives
``nsx::npu`` linking in the firmware CMake template), and insert the nsx-npu
registry module *before* the generated AOT module so the driver target exists
when the AOT module's CMakeLists resolves it.
"""

from __future__ import annotations

from pathlib import Path

from helia_profiler.config import load_config
from helia_profiler.engines.helia_aot.adapter import _build_extra_modules
from helia_profiler.engines.helia_aot.compile import (
    _BOARD_TO_AOT_PLATFORM,
    _resolve_aot_platform,
)
from helia_profiler.engines.helia_rt.adapter import NSX_NPU_MODULE, NSX_NPU_PROJECT
from helia_profiler.results import NsxModuleRef


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
    def test_atomiq110_maps_to_at110(self):
        assert _BOARD_TO_AOT_PLATFORM["atomiq110_fpga_turbo"] == "at110"

    def test_resolve_platform_for_atomiq110(self):
        assert _resolve_aot_platform(_cfg()) == "at110"


class TestExtraModules:
    _CMSIS = NsxModuleRef(name="ns-cmsis-nn", path=Path("/tmp/cmsis"))

    def test_default_backend_has_no_npu_module(self):
        mods = _build_extra_modules(_cfg(), self._CMSIS, "kws_model", Path("/tmp/aot"))
        assert [m.name for m in mods] == ["ns-cmsis-nn", "kws_model"]

    def test_ethos_u_backend_inserts_npu_before_aot_module(self):
        mods = _build_extra_modules(
            _cfg("ethos_u"), self._CMSIS, "kws_model", Path("/tmp/aot")
        )
        names = [m.name for m in mods]
        assert names == ["ns-cmsis-nn", NSX_NPU_MODULE, "kws_model"]
        assert names.index(NSX_NPU_MODULE) < names.index("kws_model")

    def test_npu_module_ref_is_registry_backed(self):
        mods = _build_extra_modules(
            _cfg("ethos_u"), self._CMSIS, "kws_model", Path("/tmp/aot")
        )
        npu = next(m for m in mods if m.name == NSX_NPU_MODULE)
        assert npu.local is False
        assert npu.project == NSX_NPU_PROJECT


class TestEngineBackendPropagation:
    def test_config_backend_reaches_artifacts_field(self):
        # EngineArtifacts.engine_backend drives has_ethos_u in
        # firmware/project.py; ensure the dataclass accepts the field.
        from helia_profiler.engines.base import EngineArtifacts
        from helia_profiler.engines import EngineType

        artifacts = EngineArtifacts(
            engine_type=EngineType.HELIA_AOT,
            engine_backend="ethos_u",
        )
        assert artifacts.engine_backend == "ethos_u"


class TestAttributesHeader:
    def test_atomiq110_covered_by_soc_guard(self, tmp_path):
        from helia_profiler.engines.helia_aot.compile import _write_attributes_header

        header = _write_attributes_header(tmp_path, "hpx")
        text = header.read_text()
        # Atomiq110 shares the Apollo510 (M55 + shared SRAM) section layout;
        # without this guard the placement macros silently become no-ops.
        assert "AM_PART_ATOMIQ110" in text
