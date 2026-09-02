"""ns-cmsis-nn CMake option policy (#246).

heliaRT 1.19.0's ``helia`` backend refuses to configure unless the fp32 kernels
are enabled in ns-cmsis-nn, and the fp16 kernels only exist for MVE-F cores.
Both are ``option()`` defaults in ns-cmsis-nn, so hpx must override them as
cache variables *before* the module is added. These tests pin that policy and
the ordering contract in the firmware template.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import helia_profiler
from helia_profiler.config import load_config
from helia_profiler.engines.cmsis_nn import cmsis_nn_cmake_vars
from helia_profiler.engines.helia_rt import HeliaRTAdapter

_TEMPLATE = Path(helia_profiler.__file__).parent / "firmware" / "templates" / "CMakeLists.txt.j2"


def _config(tmp_path: Path, board: str, engine_config: dict | None = None):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    return load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt", "config": engine_config or {}},
            "target": {"board": board},
        },
    )


# heliaRT checks ns-cmsis-nn's option; heliaAOT's generated module checks the
# exported define in the cache. Both must be set for either engine to build.
_F32 = ("NSX_CMSIS_NN_ENABLE_F32", "ARM_NN_ENABLE_F32")
_F16 = ("NSX_CMSIS_NN_ENABLE_F16", "ARM_NN_ENABLE_F16")


def test_fp32_kernels_are_always_enabled_under_both_names(tmp_path: Path) -> None:
    for board in ("apollo510_evb", "apollo4p_evb"):
        cmake_vars = cmsis_nn_cmake_vars(_config(tmp_path, board))
        assert all(cmake_vars[name] == "ON" for name in _F32)


def test_fp16_kernels_follow_the_mve_core_under_both_names(tmp_path: Path) -> None:
    m55 = cmsis_nn_cmake_vars(_config(tmp_path, "apollo510_evb"))
    assert all(m55[name] == "ON" for name in _F16)
    m4 = cmsis_nn_cmake_vars(_config(tmp_path, "apollo4p_evb"))
    assert not any(name in m4 for name in _F16)


@pytest.mark.parametrize(
    ("engine_config", "expected"),
    [
        ({}, "ON"),
        ({"cmsis_nn_requantize_inline_asm": True}, "ON"),
        ({"cmsis_nn_requantize_inline_asm": False}, None),
    ],
)
def test_requantize_asm_stays_configurable(
    tmp_path: Path, engine_config: dict, expected: str | None
) -> None:
    cmake_vars = cmsis_nn_cmake_vars(_config(tmp_path, "apollo510_evb", engine_config))
    assert cmake_vars.get("NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM") == expected
    # Turning the asm off never turns the float kernels off.
    assert cmake_vars["NSX_CMSIS_NN_ENABLE_F32"] == "ON"


def test_registry_default_heliart_forwards_the_policy(tmp_path: Path) -> None:
    """The default (registry-resolved) heliaRT build is a source build, so it
    must carry the options -- this is the path the bench hit on 1.19.0."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    artifacts = HeliaRTAdapter().prepare(_config(tmp_path, "apollo510_evb"), work_dir)
    assert all(artifacts.cmake_vars[name] == "ON" for name in (*_F32, *_F16))

    artifacts = HeliaRTAdapter().prepare(_config(tmp_path, "apollo4p_evb"), work_dir)
    assert all(artifacts.cmake_vars[name] == "ON" for name in _F32)
    assert not any(name in artifacts.cmake_vars for name in _F16)


def test_template_sets_engine_options_before_any_module_is_added() -> None:
    """ns-cmsis-nn's option() defaults win over a later set(); the cache
    overrides must precede the modules include or they silently do nothing."""
    text = _TEMPLATE.read_text()
    assert text.index("cmake_vars.items()") < text.index("cmake/nsx/modules.cmake")
