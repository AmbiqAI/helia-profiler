"""ns-cmsis-nn CMake option policy (#246).

heliaRT 1.19.0's ``helia`` backend refuses to configure unless the fp32 kernels
are enabled in ns-cmsis-nn; the fp16 kernels exist only for MVE-F cores and,
below ns-cmsis-nn v7.30.0, ICE on GCC 14 -- so they are compiled only for
models that compute in FLOAT16. Both are ``option()`` defaults in ns-cmsis-nn,
so hpx must override them as cache variables *before* the module is added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import helia_profiler
from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.engines.cmsis_nn import cmsis_nn_cmake_vars, cmsis_nn_module_ref
from helia_profiler.engines.helia_aot.adapter import _engine_cmake_vars
from helia_profiler.engines.helia_rt import HeliaRTAdapter

_TEMPLATE = Path(helia_profiler.__file__).parent / "firmware" / "templates" / "CMakeLists.txt.j2"
_FIXTURES = Path(__file__).parent / "fixtures"
INT8 = _FIXTURES / "kws_ref_model.tflite"
FP32 = _FIXTURES / "kws_float_fp32.tflite"
FP16 = _FIXTURES / "kws_float_fp16.tflite"  # true all-FLOAT16 graph
FP16_WEIGHTS = _FIXTURES / "kws_float_fp16_weights.tflite"  # FLOAT16 weights, FLOAT32 compute
HELIART_SOURCE = _FIXTURES / "heliart_nsx"

# heliaRT checks ns-cmsis-nn's option; heliaAOT's generated module checks the
# exported define in the cache. Both must be set for either engine to build.
_F32 = ("NSX_CMSIS_NN_ENABLE_F32", "ARM_NN_ENABLE_F32")
_F16 = ("NSX_CMSIS_NN_ENABLE_F16", "ARM_NN_ENABLE_F16")

M55 = "apollo510_evb"
M4 = "apollo4p_evb"


def _config(
    board: str,
    model: Path,
    engine_config: dict[str, object] | None = None,
    engine_type: str = "helia-rt",
) -> ProfileConfig:
    return load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": engine_type, "config": engine_config or {}},
            "target": {"board": board},
        },
    )


def _baseline_cmsis_nn_ref(config: ProfileConfig) -> str:
    assert config.compatibility is not None
    return config.compatibility.baseline.module("nsx-cmsis-nn").ref


def _assert_policy(cmake_vars: dict[str, str], *, fp16: bool) -> None:
    assert all(cmake_vars[name] == "ON" for name in _F32)
    if fp16:
        assert all(cmake_vars[name] == "ON" for name in _F16)
    else:
        assert not any(name in cmake_vars for name in _F16)


@pytest.mark.parametrize(
    ("board", "model", "fp16"),
    [
        (M55, FP16, True),
        (M55, FP16_WEIGHTS, True),  # widening f16 weights is f16 work on an MVE-F core
        (M55, FP32, False),  # fp32 compute needs no fp16 sources
        (M55, INT8, False),
        (M4, FP16, False),  # no MVE-F core, whatever the model asks
        (M4, FP16_WEIGHTS, False),
        (M4, INT8, False),
    ],
)
def test_fp32_always_and_fp16_only_for_float16_models_on_mve_cores(
    board: str, model: Path, fp16: bool
) -> None:
    _assert_policy(cmsis_nn_cmake_vars(_config(board, model)), fp16=fp16)


@pytest.mark.parametrize(
    ("engine_config", "expected"),
    [
        ({}, "ON"),
        ({"cmsis_nn_requantize_inline_asm": True}, "ON"),
        ({"cmsis_nn_requantize_inline_asm": False}, None),
    ],
)
def test_requantize_asm_stays_configurable(
    engine_config: dict[str, object], expected: str | None
) -> None:
    cmake_vars = cmsis_nn_cmake_vars(_config(M55, FP16, engine_config))
    assert cmake_vars.get("NSX_CMSIS_NN_USE_REQUANTIZE_INLINE_ASM") == expected
    # Turning the asm off never turns the float kernels off.
    _assert_policy(cmake_vars, fp16=True)


@pytest.mark.parametrize("engine_type", ["helia-rt", "helia-aot"])
def test_nsx_cmsis_nn_is_declared_at_the_baseline_ref_by_default(
    tmp_path: Path, engine_type: str
) -> None:
    """neuralSPOT-X 0.7.17's registry resolves a core heliaAOT 0.19.0 refuses,
    so hpx declares the module at the baseline's qualified ref; a user ref wins."""
    config = _config(M55, INT8, engine_type=engine_type)
    declared = cmsis_nn_module_ref(config, tmp_path)
    assert declared.local is False
    assert declared.ref == _baseline_cmsis_nn_ref(config)

    override = _config(M55, INT8, {"cmsis_nn_ref": "deadbeef"}, engine_type=engine_type)
    assert cmsis_nn_module_ref(override, tmp_path).ref == "deadbeef"


@pytest.mark.parametrize(("board", "fp16"), [(M55, True), (M4, False)])
def test_registry_default_heliart_forwards_the_policy(
    tmp_path: Path, board: str, fp16: bool
) -> None:
    """The default (registry-resolved) heliaRT build is a source build, so it
    must carry the options and declare the core -- the path the bench hit on 1.19.0."""
    config = _config(board, FP16)
    artifacts = HeliaRTAdapter().prepare(config, tmp_path)
    _assert_policy(artifacts.cmake_vars, fp16=fp16)
    baseline_ref = _baseline_cmsis_nn_ref(config)
    assert any(
        module.name == "nsx-cmsis-nn" and module.ref == baseline_ref
        for module in artifacts.extra_modules
    )


def _fake_heliart_source(root: Path) -> Path:
    """The five files the source route insists on, borrowing the fixture's NSX module."""
    source = root / "helia-rt"
    (source / "nsx").mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("")
    for name in ("CMakeLists.txt", "nsx-module.yaml"):
        (source / "nsx" / name).write_bytes((HELIART_SOURCE / name).read_bytes())
    (source / "cmake").mkdir()
    (source / "cmake" / "helia_rt_sources.cmake").write_text("")
    version_h = source / "tensorflow" / "lite" / "micro" / "helia_rt_version.h"
    version_h.parent.mkdir(parents=True)
    version_h.write_text('#define HELIA_RT_VERSION "v1.19.0"\n')
    return source


def test_source_path_heliart_forwards_the_policy(tmp_path: Path) -> None:
    source = _fake_heliart_source(tmp_path)
    config = _config(M55, FP16, {"source_path": str(source)})
    artifacts = HeliaRTAdapter().prepare(config, tmp_path / "work")
    _assert_policy(artifacts.cmake_vars, fp16=True)


@pytest.mark.parametrize(("board", "fp16"), [(M55, True), (M4, False)])
def test_heliaaot_forwards_the_policy_plus_the_linker_profile(board: str, fp16: bool) -> None:
    """heliaAOT's generated module is the consumer of the ARM_NN_ENABLE_* names."""
    config = _config(board, FP16, {"linker_profile": "itcm"}, engine_type="helia-aot")
    cmake_vars = _engine_cmake_vars(config)
    _assert_policy(cmake_vars, fp16=fp16)
    assert cmake_vars["NSX_LINKER_PROFILE"] == "itcm"


def test_template_sets_engine_options_before_any_module_is_included() -> None:
    """NSX modules read these options while they are included (the bootstrap
    include adds them), so the cache overrides must precede every nsx include."""
    text = _TEMPLATE.read_text()
    assert text.index("cmake_vars.items()") < text.index(
        "include(${CMAKE_CURRENT_LIST_DIR}/cmake/nsx/"
    )
