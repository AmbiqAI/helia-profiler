"""Custom SoC and board overlay parsing for profile configuration."""

from __future__ import annotations

from typing import Any

from ..errors import ConfigError
from .board import DEFAULT_GO_GPIO_PIN, DEFAULT_STATE_GPIO_PIN, DEFAULT_SYNC_GPIO_PIN, BoardDef
from .registry import PlatformRegistry, build_platform_registry, get_board, get_soc
from .soc import (
    ClockDomain,
    ClockSpeed,
    CoreArch,
    MemoryLayout,
    PerfTier,
    PmuTier,
    SocDef,
    SocFamily,
)


def build_custom_platform_registry(target: dict[str, Any]) -> PlatformRegistry:
    """Build the platform registry after applying target-local overlays."""
    base = build_platform_registry()
    custom_socs = _build_custom_socs(target.get("custom_socs"), base)
    registry_with_socs = build_platform_registry(base=base, socs=custom_socs)
    custom_boards = _build_custom_boards(target.get("custom_boards"), registry_with_socs)
    return build_platform_registry(base=registry_with_socs, boards=custom_boards)


def _build_custom_socs(raw: Any, base: PlatformRegistry) -> dict[str, SocDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("target.custom_socs must be a mapping of name -> definition")

    custom: dict[str, SocDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"target.custom_socs.{name} must be a mapping")
        overlay = build_platform_registry(base=base, socs=custom)
        based_on = spec.get("based_on")
        base_soc = get_soc(based_on, registry=overlay) if based_on else None
        family = _enum_value(
            SocFamily,
            spec.get("family", base_soc.family if base_soc else None),
            field_name=f"target.custom_socs.{name}.family",
        )
        core = _enum_value(
            CoreArch,
            spec.get("core", base_soc.core if base_soc else None),
            field_name=f"target.custom_socs.{name}.core",
        )
        pmu_tier = _enum_value(
            PmuTier,
            spec.get("pmu_tier", base_soc.pmu_tier if base_soc else None),
            field_name=f"target.custom_socs.{name}.pmu_tier",
        )
        has_mve = spec.get("has_mve", base_soc.has_mve if base_soc else None)
        if has_mve is None:
            raise ConfigError(f"target.custom_socs.{name}.has_mve is required")
        c_define = spec.get("c_define", base_soc.c_define if base_soc else None)
        cmsis_header = spec.get("cmsis_header", base_soc.cmsis_header if base_soc else None)
        if c_define is None:
            raise ConfigError(f"target.custom_socs.{name}.c_define is required")
        if cmsis_header is None:
            raise ConfigError(f"target.custom_socs.{name}.cmsis_header is required")
        custom[name] = SocDef(
            name=name,
            family=family,
            core=core,
            pmu_tier=pmu_tier,
            has_mve=bool(has_mve),
            memory=_build_memory_layout(
                spec.get("memory"),
                field_name=f"target.custom_socs.{name}.memory",
                base=base_soc.memory if base_soc else None,
            ),
            clocks=_build_clock_domains(
                spec.get("clocks"),
                field_name=f"target.custom_socs.{name}.clocks",
                base=base_soc.clocks if base_soc else None,
            ),
            c_define=str(c_define),
            cmsis_header=str(cmsis_header),
            rtt_scan_ranges=_build_rtt_scan_ranges(
                spec.get("rtt_scan_ranges", base_soc.rtt_scan_ranges if base_soc else None),
                field_name=f"target.custom_socs.{name}.rtt_scan_ranges",
            ),
            jlink_device=str(spec.get("jlink_device", base_soc.jlink_device if base_soc else "")),
            pmu_max_ops=int(spec.get("pmu_max_ops", base_soc.pmu_max_ops if base_soc else 2048)),
        )
    return custom


def _build_custom_boards(raw: Any, registry: PlatformRegistry) -> dict[str, BoardDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("target.custom_boards must be a mapping of name -> definition")

    custom: dict[str, BoardDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"target.custom_boards.{name} must be a mapping")
        overlay = build_platform_registry(base=registry, boards=custom)
        based_on = spec.get("based_on")
        base_board = get_board(based_on, registry=overlay) if based_on else None
        soc = spec.get("soc", base_board.soc if base_board else None)
        channel = spec.get("channel", base_board.channel if base_board else None)
        if soc is None:
            raise ConfigError(f"target.custom_boards.{name}.soc is required")
        if channel is None:
            raise ConfigError(f"target.custom_boards.{name}.channel is required")
        starter_profile_board = spec.get(
            "starter_profile_board",
            base_board.profile_source_board if base_board else None,
        )
        custom[name] = BoardDef(
            name=name,
            soc=str(soc),
            channel=str(channel),
            psram_kb=_optional_int(spec.get("psram_kb", base_board.psram_kb if base_board else None)),
            default_sync_gpio_pin=int(
                spec.get(
                    "default_sync_gpio_pin",
                    base_board.default_sync_gpio_pin if base_board else DEFAULT_SYNC_GPIO_PIN,
                )
            ),
            default_state_gpio_pin=int(
                spec.get(
                    "default_state_gpio_pin",
                    base_board.default_state_gpio_pin if base_board else DEFAULT_STATE_GPIO_PIN,
                )
            ),
            default_go_gpio_pin=int(
                spec.get(
                    "default_go_gpio_pin",
                    base_board.default_go_gpio_pin if base_board else DEFAULT_GO_GPIO_PIN,
                )
            ),
            starter_profile_board=(
                str(starter_profile_board) if starter_profile_board is not None else None
            ),
            description=str(spec.get("description", base_board.description if base_board else "")),
        )
    return custom


def _enum_value(enum_cls: type, raw: Any, *, field_name: str):
    if isinstance(raw, enum_cls):
        return raw
    if raw is None:
        raise ConfigError(f"{field_name} is required")
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_cls)
        raise ConfigError(f"Invalid {field_name}: {raw!r}. Supported: {allowed}") from exc


def _build_memory_layout(raw: Any, *, field_name: str, base: MemoryLayout | None) -> MemoryLayout:
    if raw is None:
        if base is None:
            raise ConfigError(f"{field_name} is required")
        return base
    if not isinstance(raw, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    values = {
        "mram_kb": base.mram_kb if base else 0,
        "sram_kb": base.sram_kb if base else 0,
        "dtcm_kb": base.dtcm_kb if base else 0,
        "itcm_kb": base.itcm_kb if base else 0,
        "psram_kb": base.psram_kb if base else 0,
        "nvm_kb": base.nvm_kb if base else 0,
    }
    for key in values:
        if key in raw:
            values[key] = int(raw[key])
    return MemoryLayout(**values)


def _build_clock_domains(
    raw: Any,
    *,
    field_name: str,
    base: tuple[ClockDomain, ...] | None,
) -> tuple[ClockDomain, ...]:
    if raw is None:
        if base is None:
            raise ConfigError(f"{field_name} is required")
        return base
    if not isinstance(raw, list):
        raise ConfigError(f"{field_name} must be a list")
    domains: list[ClockDomain] = []
    for index, domain in enumerate(raw):
        if not isinstance(domain, dict):
            raise ConfigError(f"{field_name}[{index}] must be a mapping")
        speeds_raw = domain.get("speeds")
        if not isinstance(speeds_raw, list) or not speeds_raw:
            raise ConfigError(f"{field_name}[{index}].speeds must be a non-empty list")
        speeds: list[ClockSpeed] = []
        for speed_index, speed in enumerate(speeds_raw):
            if not isinstance(speed, dict):
                raise ConfigError(f"{field_name}[{index}].speeds[{speed_index}] must be a mapping")
            perf_tier = speed.get("perf_tier")
            speeds.append(
                ClockSpeed(
                    name=str(speed["name"]),
                    mhz=int(speed["mhz"]),
                    perf_tier=(
                        _enum_value(
                            PerfTier,
                            perf_tier,
                            field_name=f"{field_name}[{index}].speeds[{speed_index}].perf_tier",
                        )
                        if perf_tier is not None
                        else None
                    ),
                )
            )
        domains.append(
            ClockDomain(
                name=str(domain["name"]),
                speeds=tuple(speeds),
                default=str(domain["default"]),
            )
        )
    return tuple(domains)


def _build_rtt_scan_ranges(raw: Any, *, field_name: str) -> tuple[tuple[int, int], ...]:
    if raw is None:
        raise ConfigError(f"{field_name} is required")
    if not isinstance(raw, (list, tuple)):
        raise ConfigError(f"{field_name} must be a list of [base, length] pairs")
    ranges: list[tuple[int, int]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ConfigError(f"{field_name}[{index}] must be a [base, length] pair")
        ranges.append((int(item[0]), int(item[1])))
    if not ranges:
        raise ConfigError(f"{field_name} must not be empty")
    return tuple(ranges)


def _optional_int(raw: Any) -> int | None:
    if raw is None:
        return None
    return int(raw)
