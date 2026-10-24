"""Fixture IO declarations and admission shared by producers and retained readers."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from .engines import EngineType
from .errors import ConfigError

if TYPE_CHECKING:
    from .fixture_analysis import FixtureTensor


class FixtureDType(StrEnum):
    """IO tensor element types a fixture can declare."""

    INT8 = "int8"
    INT16 = "int16"
    INT32 = "int32"
    FLOAT16 = "float16"
    FLOAT32 = "float32"


class FixtureCapability(StrEnum):
    """Producer-declared status of one engine and IO dtype for fixed fixtures."""

    QUALIFIED = "qualified"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


# Public IO types handled by the fixture renderer and each engine integration.
# These declarations do not describe internal tensor or kernel support.
_FIXTURE_IO_DTYPES = {
    EngineType.LITERT: frozenset(
        {FixtureDType.INT8, FixtureDType.INT16, FixtureDType.INT32, FixtureDType.FLOAT32}
    ),
    EngineType.HELIA_AOT: frozenset(FixtureDType),
    EngineType.HELIA_RT: frozenset(FixtureDType),
}

#: Generic fixture IO support, independent of runtime qualification evidence.
#: INT32 support is input-only. Selected-engine failures still propagate.
FIXTURE_CAPABILITIES: dict[EngineType, dict[str, FixtureCapability]] = {
    engine: {
        dtype: (
            FixtureCapability.SUPPORTED if dtype in supported else FixtureCapability.UNSUPPORTED
        )
        for dtype in FixtureDType
    }
    for engine, supported in _FIXTURE_IO_DTYPES.items()
}

#: Largest total output a fixture may expose for full readback.
FIXTURE_READBACK_BUDGET = 128 * 1024


def io_capabilities(
    inputs: tuple[FixtureTensor, ...], outputs: tuple[FixtureTensor, ...], engine: EngineType
) -> tuple[tuple[str, FixtureCapability], ...]:
    if any(t.dtype == "int32" for t in outputs):
        raise ConfigError("INT32 fixture support is input-only")
    output_bytes = sum(t.size_bytes for t in outputs)
    if output_bytes > FIXTURE_READBACK_BUDGET:
        raise ConfigError(
            f"Fixture outputs total {output_bytes} B, above the "
            f"{FIXTURE_READBACK_BUDGET} B readback budget"
        )
    table = FIXTURE_CAPABILITIES[engine]
    tensors = (*inputs, *outputs)
    capabilities = tuple(sorted({(t.dtype, table[t.dtype]) for t in tensors}))
    unsupported = [
        dtype for dtype, status in capabilities if status is FixtureCapability.UNSUPPORTED
    ]
    if unsupported:
        raise ConfigError(f"{engine.value} fixtures do not support {', '.join(unsupported)} IO")
    return capabilities
