"""Project a resolved config into its retained platform input."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..platform.memory_map import link_family_for_toolchain
from ..platform.provenance import PlatformSnapshot, platform_snapshot

if TYPE_CHECKING:
    from . import ProfileConfig


def config_platform(config: ProfileConfig) -> PlatformSnapshot:
    return platform_snapshot(
        config.target.board,
        registry=config.platform_registry,
        link_family=link_family_for_toolchain(config.target.toolchain.value),
    )
