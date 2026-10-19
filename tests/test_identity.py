"""Shared identity vocabulary: every name HPX uses today maps to its canonical name."""

from __future__ import annotations

import pytest

from helia_profiler._fixture_build import FixtureBackend
from helia_profiler.deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from helia_profiler.engines import EngineType
from helia_profiler.engines.executorch import EXECUTORCH_MODULE
from helia_profiler.engines.helia_rt.adapter import HeliaRTAdapter
from helia_profiler.engines.helia_rt.artifacts import HELIART_MODULE
from helia_profiler.engines.tflm import _SUPPORTED_BACKENDS, TFLITE_MICRO_MODULE, TFLMAdapter
from helia_profiler.fixture_runtime import STACK_KERNEL_LIBRARY, STACK_RUNTIME_RECORD
from helia_profiler.identity import (
    CanonicalEngine,
    CanonicalKernel,
    Unknown,
    canonical_engine,
    canonical_kernel,
)

LITERT, HELIA_RT = CanonicalEngine.LITERT, CanonicalEngine.HELIA_RT
REFERENCE, CMSIS_NN, HELIA_CORE = (
    CanonicalKernel.REFERENCE,
    CanonicalKernel.CMSIS_NN,
    CanonicalKernel.HELIA_CORE,
)


def test_engine_type_and_its_wire_name_map_to_the_same_engine():
    expected = {
        EngineType.TFLM: LITERT,
        EngineType.HELIA_RT: HELIA_RT,
        EngineType.HELIA_AOT: CanonicalEngine.HELIA_AOT,
        EngineType.EXECUTORCH: CanonicalEngine.EXECUTORCH,
    }
    assert set(expected) == set(EngineType)
    for engine, canonical in expected.items():
        assert canonical_engine(engine) is canonical
        assert canonical_engine(engine.wire_name) is canonical


def test_runtime_stacks_and_modules_map_to_their_engine():
    assert {stack: canonical_engine(stack) for stack in STACK_RUNTIME_RECORD} == {
        "upstream": LITERT,
        "helia-rt": HELIA_RT,
    }
    assert {canonical_engine(record) for record in STACK_RUNTIME_RECORD.values()} == {
        LITERT,
        HELIA_RT,
    }
    assert canonical_engine(TFLITE_MICRO_MODULE) is LITERT
    assert canonical_engine(HELIART_MODULE) is HELIA_RT
    assert canonical_engine(EXECUTORCH_MODULE) is CanonicalEngine.EXECUTORCH


def test_kernel_names_map_to_their_library():
    assert {stack: canonical_kernel(lib) for stack, lib in STACK_KERNEL_LIBRARY.items()} == {
        "upstream": CMSIS_NN,
        "helia-rt": HELIA_CORE,
    }
    assert {m: canonical_kernel(m) for m in CMSIS_NN_PROVIDER_MODULES} == {
        "arm-cmsis-nn": CMSIS_NN,
        "nsx-cmsis-nn": HELIA_CORE,
    }
    assert {b: canonical_kernel(b) for b in _SUPPORTED_BACKENDS} == {
        "reference": REFERENCE,
        "cmsis_nn": CMSIS_NN,
    }
    assert {b: canonical_kernel(b) for b in FixtureBackend} == {
        FixtureBackend.CMSIS_NN: CMSIS_NN,
        FixtureBackend.HELIA: HELIA_CORE,
    }
    for adapter, engine, kernel in (
        (TFLMAdapter, LITERT, CMSIS_NN),
        (HeliaRTAdapter, HELIA_RT, HELIA_CORE),
    ):
        stack, backend = adapter.prepared_runtime_contract
        assert (canonical_engine(stack), canonical_kernel(backend)) == (engine, kernel)
    assert (canonical_kernel("arm"), canonical_kernel("ns")) == (CMSIS_NN, HELIA_CORE)


@pytest.mark.parametrize(
    "spelling", ["heliaRT", "helia_rt", "HELIA-AOT", "heliaAOT", "tflite_micro", " tflm "]
)
def test_case_underscores_and_spaces_do_not_matter(spelling):
    canonical_engine(spelling)


@pytest.mark.parametrize("spelling", ["heliaCORE", "helia_core", "ns_cmsis_nn", "ARM_CMSIS_NN"])
def test_kernel_spellings(spelling):
    canonical_kernel(spelling)


def test_canonical_names_map_to_themselves():
    for engine in CanonicalEngine:
        assert canonical_engine(engine) is engine
    for kernel in CanonicalKernel:
        assert canonical_kernel(kernel) is kernel


def test_an_unrecognised_or_non_string_name_is_refused():
    with pytest.raises(ValueError, match="Unrecognised kernel library name 'ethos_u'"):
        canonical_kernel("ethos_u")
    with pytest.raises(ValueError, match="must be a string"):
        canonical_engine(None)  # ty: ignore[invalid-argument-type]


def test_an_unknown_fact_needs_a_reason():
    assert Unknown("no CMSIS-NN module in the dependency state").reason
    for reason in (" ", None, 1):
        with pytest.raises(ValueError, match="needs a reason"):
            Unknown(reason)  # ty: ignore[invalid-argument-type]
