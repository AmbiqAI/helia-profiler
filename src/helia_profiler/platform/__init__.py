"""Platform model package — SoC families, boards, and the platform registry.

Split across focused modules but re-exported here so
``from helia_profiler.platform import X`` keeps working for every importer:

* :mod:`.soc` — SoC families, core/PMU/clock/memory model and ``SocDef`` plus the
  built-in SoC registrations.
* :mod:`.board` — ``BoardDef`` and the built-in board registrations.
* :mod:`.registry` — ``PlatformRegistry`` construction and the public lookup
  helpers.
"""

from __future__ import annotations

from .board import (
    DEFAULT_GO_GPIO_PIN,
    DEFAULT_STATE_GPIO_PIN,
    DEFAULT_SYNC_GPIO_PIN,
    BoardDef,
)
from .capabilities import (
    ClockCapabilities,
    MemoryCapabilities,
    PowerCaptureCapabilities,
    ResetCapabilities,
    SocCapabilities,
    TransportCapabilities,
)
from .registry import (
    PlatformRegistry,
    build_platform_registry,
    get_board,
    get_default_go_gpio_pin,
    get_default_state_gpio_pin,
    get_default_sync_gpio_pin,
    get_soc,
    get_soc_for_board,
    list_boards,
    list_socs,
)
from .soc import (
    ClockDomain,
    ClockSpeed,
    CoreArch,
    MemoryLayout,
    MemoryRange,
    PerfTier,
    PmuTier,
    SocDef,
    SocFamily,
    soc_placement_ranges,
)

__all__ = [
    "DEFAULT_GO_GPIO_PIN",
    "DEFAULT_STATE_GPIO_PIN",
    "DEFAULT_SYNC_GPIO_PIN",
    "BoardDef",
    "ClockCapabilities",
    "ClockDomain",
    "ClockSpeed",
    "CoreArch",
    "MemoryCapabilities",
    "MemoryLayout",
    "MemoryRange",
    "PerfTier",
    "PlatformRegistry",
    "PmuTier",
    "PowerCaptureCapabilities",
    "ResetCapabilities",
    "SocCapabilities",
    "SocDef",
    "SocFamily",
    "TransportCapabilities",
    "build_platform_registry",
    "get_board",
    "get_default_go_gpio_pin",
    "get_default_state_gpio_pin",
    "get_default_sync_gpio_pin",
    "get_soc",
    "get_soc_for_board",
    "list_boards",
    "list_socs",
    "soc_placement_ranges",
]
