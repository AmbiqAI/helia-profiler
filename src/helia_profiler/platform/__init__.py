"""Platform model package — SoC families, boards, and the platform registry.

Split across focused modules but re-exported here so
``from helia_profiler.platform import X`` keeps working for every importer:

* :mod:`.soc` — SoC families, core/PMU/clock/memory model and ``SocDef`` plus the
  built-in SoC registrations.
* :mod:`.board` — ``BoardDef`` and the built-in board registrations.
* :mod:`.registry` — ``PlatformRegistry`` construction and the public lookup
  helpers.
* :mod:`.memory_map` — the verified linked-memory map (#133): per-SoC region
  windows characterized from the NSX linker scripts.
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
    ResetCapabilities,
    SocCapabilities,
    TransportCapabilities,
)
from .custom import build_custom_platform_registry
from .memory_map import (
    LinkedRegionWindow,
    LinkFamily,
    classify_address,
    link_family_for_toolchain,
    linked_memory_map,
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
    SocOrigin,
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
    "LinkFamily",
    "LinkedRegionWindow",
    "MemoryCapabilities",
    "MemoryLayout",
    "MemoryRange",
    "PerfTier",
    "PlatformRegistry",
    "PmuTier",
    "ResetCapabilities",
    "SocCapabilities",
    "SocDef",
    "SocFamily",
    "SocOrigin",
    "TransportCapabilities",
    "build_platform_registry",
    "build_custom_platform_registry",
    "classify_address",
    "get_board",
    "get_default_go_gpio_pin",
    "get_default_state_gpio_pin",
    "get_default_sync_gpio_pin",
    "get_soc",
    "get_soc_for_board",
    "link_family_for_toolchain",
    "linked_memory_map",
    "list_boards",
    "list_socs",
    "soc_placement_ranges",
]
