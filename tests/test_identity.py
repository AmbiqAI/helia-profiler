"""Shared identity vocabulary: every name HPX uses today maps to one canonical name."""

from __future__ import annotations

import pytest

from helia_profiler.deps.compatibility import CMSIS_NN_PROVIDER_MODULES
from helia_profiler.engines import EngineType
from helia_profiler.fixture_runtime import STACK_KERNEL_LIBRARY, STACK_RUNTIME_RECORD
from helia_profiler.identity import (
    CanonicalEngine,
    CanonicalKernel,
    Unknown,
    canonical_engine,
    canonical_kernel,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("tflm", CanonicalEngine.LITERT),
        ("tflite-micro", CanonicalEngine.LITERT),
        ("upstream", CanonicalEngine.LITERT),
        ("helia_aot", CanonicalEngine.HELIA_AOT),
        ("HeliaRT", CanonicalEngine.HELIA_RT),
    ],
)
def test_engine_aliases(name, expected):
    assert canonical_engine(name) is expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("cmsis_nn", CanonicalKernel.CMSIS_NN),
        ("arm-cmsis-nn", CanonicalKernel.CMSIS_NN),
        ("arm", CanonicalKernel.CMSIS_NN),
        ("ns-cmsis-nn", CanonicalKernel.HELIA_CORE),
        ("nsx-cmsis-nn", CanonicalKernel.HELIA_CORE),
        ("ns", CanonicalKernel.HELIA_CORE),
        ("helia", CanonicalKernel.HELIA_CORE),
    ],
)
def test_kernel_aliases(name, expected):
    assert canonical_kernel(name) is expected


def test_every_name_hpx_uses_today_has_a_canonical_name():
    from helia_profiler.engines.tflm import _SUPPORTED_BACKENDS

    for engine in (*EngineType, *STACK_RUNTIME_RECORD, *STACK_RUNTIME_RECORD.values()):
        canonical_engine(engine)
    for kernel in (
        *_SUPPORTED_BACKENDS,
        *CMSIS_NN_PROVIDER_MODULES,
        *STACK_KERNEL_LIBRARY.values(),
    ):
        canonical_kernel(kernel)
    for name in (*CanonicalEngine, *CanonicalKernel):
        assert (canonical_engine if name in set(CanonicalEngine) else canonical_kernel)(
            name
        ) == name


def test_an_unknown_name_is_refused():
    with pytest.raises(ValueError, match="Unknown kernel library name 'ethos_u'"):
        canonical_kernel("ethos_u")


def test_an_unknown_fact_needs_a_reason():
    assert Unknown("no CMSIS-NN module in the dependency state").reason
    with pytest.raises(ValueError, match="needs a reason"):
        Unknown(" ")
