"""Validate requested profiling features against the platform registry."""

from __future__ import annotations

from ..errors import ConfigError
from .counters import supported_groups_for_domains, validate_group_selection
from .registry import PlatformRegistry, get_soc_for_board
from .soc import SocDef


def _resolve_soc(board: str, registry: PlatformRegistry) -> SocDef:
    try:
        return get_soc_for_board(board, registry=registry)
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc


def check_pmu_selection(
    board: str, counters: dict[str, str | list[str]], *, registry: PlatformRegistry
) -> None:
    """Validate counter groups with a board-specific configuration hint."""
    soc = _resolve_soc(board, registry)
    supported_groups = supported_groups_for_domains(soc.profiling_domains)
    try:
        validate_group_selection(counters, supported_groups=supported_groups)
    except ValueError as exc:
        raise ConfigError(
            str(exc),
            hint=(
                f"Board '{board}' exposes profiling groups: "
                f"{', '.join(supported_groups) if supported_groups else 'none'}."
            ),
        ) from exc


def check_usb_support(board: str, *, registry: PlatformRegistry) -> None:
    """Reject USB transport on boards without USB device support."""
    soc = _resolve_soc(board, registry)
    if not soc.has_usb:
        raise ConfigError(
            f"Board '{board}' ({soc.name}) has no USB device support.",
            hint=(
                "Apollo3/3P has no compatible nsx-ambiq-usb module — use "
                "transport=uart, swo, or rtt instead."
            ),
        )
