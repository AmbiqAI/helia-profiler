"""Custom SoC and board overlay parsing for profile configuration.

This module owns the *config surface* of ``target.custom_socs`` /
``target.custom_boards``: which YAML keys exist, what they mean, and how they
turn into the frozen :class:`~helia_profiler.platform.soc.SocDef` /
:class:`~helia_profiler.platform.board.BoardDef` records the rest of hpx reads.
The set of accepted key names is named once, in the ``Custom*Field`` enums
below, and anything outside it is rejected rather than discarded.  Those enums
are pinned against the dataclasses they feed by
``tests/test_platform.py::test_the_custom_memory_keys_track_the_memory_layout_fields_exactly``
and its ``SocDef`` / ``BoardDef`` siblings, so a field added to the platform
model cannot quietly become an unwritable one here.  Since unknown keys are now
rejected, an omission is no longer just an unwritable field: it is a config
that fails to load, which is how ``BoardDef.ble_reset_gpio_pin`` surfaced.
"""

from __future__ import annotations

import difflib
from enum import Enum
from typing import Any, Mapping

from ..errors import ConfigError
from .board import DEFAULT_GO_GPIO_PIN, DEFAULT_STATE_GPIO_PIN, DEFAULT_SYNC_GPIO_PIN, BoardDef
from .capabilities import resolve_app_flash_load_addr
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


#: Told to every rejected address, so the user is always shown the shape to
#: write rather than only what was wrong with what they wrote.
_ADDRESS_HINT = (
    "Write an unsigned decimal or 0x-prefixed hex literal, e.g. app_flash_load_addr: 0x22000000"
)

#: Widest address any supported part can hold.  Every SoC in this project is a
#: 32-bit Cortex-M, so an address above this is not a part hpx could ever
#: program -- see :func:`_address`.
_MAX_ADDRESS = 0xFFFFFFFF

#: Distinguishes "key absent" from "key present, written as ``null``".  A plain
#: ``spec.get(key)`` collapses the two, and they mean opposite things here (see
#: :func:`_app_flash_load_addr`).
_UNSET = object()


class CustomSocField(Enum):
    """Keys accepted inside a ``target.custom_socs.<name>`` mapping.

    ``DESCRIPTION`` has no :class:`~helia_profiler.platform.soc.SocDef` field
    behind it and is read by nothing -- it is here because
    :class:`CustomBoardField` accepts one, and rejecting the same annotation on
    the sibling block would mean a user who commented their custom *board* got
    a hard ``ConfigError`` for commenting their custom *SoC* the same way.
    """

    BASED_ON = "based_on"
    FAMILY = "family"
    CORE = "core"
    PMU_TIER = "pmu_tier"
    HAS_MVE = "has_mve"
    MEMORY = "memory"
    CLOCKS = "clocks"
    C_DEFINE = "c_define"
    CMSIS_HEADER = "cmsis_header"
    RTT_SCAN_RANGES = "rtt_scan_ranges"
    JLINK_DEVICE = "jlink_device"
    PMU_MAX_OPS = "pmu_max_ops"
    APP_FLASH_LOAD_ADDR = "app_flash_load_addr"
    DESCRIPTION = "description"


class CustomMemoryField(Enum):
    """Keys accepted inside a ``target.custom_socs.<name>.memory`` mapping.

    One per :class:`~helia_profiler.platform.soc.MemoryLayout` size field.  A
    typo here used to vanish silently and leave the ``based_on`` part's size in
    place -- and these sizes are the arena/weights capacity checks, so the
    consequence is a placement that only fails at link time.
    """

    MRAM_KB = "mram_kb"
    SRAM_KB = "sram_kb"
    DTCM_KB = "dtcm_kb"
    ITCM_KB = "itcm_kb"
    PSRAM_KB = "psram_kb"
    NVM_KB = "nvm_kb"


class CustomBoardField(Enum):
    """Keys accepted inside a ``target.custom_boards.<name>`` mapping."""

    BASED_ON = "based_on"
    SOC = "soc"
    CHANNEL = "channel"
    PSRAM_KB = "psram_kb"
    DEFAULT_SYNC_GPIO_PIN = "default_sync_gpio_pin"
    DEFAULT_STATE_GPIO_PIN = "default_state_gpio_pin"
    DEFAULT_GO_GPIO_PIN = "default_go_gpio_pin"
    BLE_RESET_GPIO_PIN = "ble_reset_gpio_pin"
    STARTER_PROFILE_BOARD = "starter_profile_board"
    DESCRIPTION = "description"


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
        _reject_unknown_keys(spec, allowed=CustomSocField, field_name=f"target.custom_socs.{name}")
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
            app_flash_load_addr=_app_flash_load_addr(
                spec,
                field_name=f"target.custom_socs.{name}.app_flash_load_addr",
                base=base_soc,
            ),
        )
    return custom


def _app_flash_load_addr(
    spec: Mapping[str, Any],
    *,
    field_name: str,
    base: SocDef | None,
) -> int | None:
    """Resolve the app-image flash address a custom SoC is declared with.

    An explicit ``app_flash_load_addr:`` wins.  Otherwise the address is
    inherited from ``based_on``, using that part's *resolved* address so the
    inheritance carries whatever the base actually flashes at -- a built-in's
    per-SoC override included -- rather than only the raw field.

    With neither, the result is ``None`` and the part has no address at all.
    That is deliberate.  The remaining case is a SoC declared from scratch
    whose only platform statement is a ``family:`` tag, and in this model that
    tag records a core tier, not a memory map (see
    :func:`~helia_profiler.platform.capabilities.resolve_app_flash_load_addr`).
    Handing such a part its family's stock address produces a *plausible*
    guess -- likely enough to be accepted by the silicon and land the image at
    the wrong offset -- where ``None`` produces a J-Link fallback that refuses
    to program and names this field.

    An explicit ``app_flash_load_addr: null`` is NOT the same as leaving the
    key out, and the two are kept apart by a sentinel rather than by
    ``spec.get(...) is None``.  Writing ``null`` is a statement -- the only one
    the config surface offers for "do not guess an address for this part" --
    and it is most plausibly reached for by someone who has a ``based_on`` they
    want for its memory/clock facts but distrusts its flash window.  Collapsing
    it into "unstated" hands that user the inherited address, i.e. the exact
    opposite of what they wrote.

    Note what this deliberately does NOT key on: whether the entry overrides
    ``memory:``.  :class:`~helia_profiler.platform.soc.MemoryLayout` carries
    sizes in KB and no addresses, so "resized a region" is not evidence about
    the bootloader reservation -- bumping ``psram_kb`` for a board with a
    larger PSRAM part would drop a perfectly good inherited address.  Deriving
    one memory fact from an unrelated one is the auto-magic this repo avoids;
    ``based_on`` is a statement the user actually made.
    """
    raw = spec.get(CustomSocField.APP_FLASH_LOAD_ADDR.value, _UNSET)
    if raw is _UNSET:
        return resolve_app_flash_load_addr(base) if base is not None else None
    if raw is None:
        return None
    return _address(raw, field_name=field_name)


def _address(raw: Any, *, field_name: str) -> int:
    """Parse a physical address, accepting ``0x``-style strings.

    YAML resolves an unquoted ``0x22000000`` to an int already; a quoted one
    stays a string, which is a natural way to write a hex literal and should
    not be a stack trace.  ``bool`` is excluded explicitly because it is an
    ``int`` subclass and ``true`` would otherwise parse as address ``0x1``.
    """
    if isinstance(raw, bool):
        raise ConfigError(
            f"{field_name} must be an address, not a boolean.",
            hint=_ADDRESS_HINT,
        )
    if isinstance(raw, str):
        try:
            value = int(raw, 0)
        except ValueError as exc:
            raise ConfigError(
                f"Invalid {field_name}: {raw!r} is not an integer address.",
                hint=_ADDRESS_HINT,
            ) from exc
    elif isinstance(raw, int):
        value = raw
    else:
        raise ConfigError(
            f"Invalid {field_name}: {raw!r} is not an integer address.",
            hint=_ADDRESS_HINT,
        )
    if value < 0:
        raise ConfigError(
            f"Invalid {field_name}: {value} is negative -- addresses are unsigned.",
            hint=_ADDRESS_HINT,
        )
    if value > _MAX_ADDRESS:
        raise ConfigError(
            f"Invalid {field_name}: {value:#x} does not fit in 32 bits "
            f"(max {_MAX_ADDRESS:#x}).",
            hint=(
                "Every part hpx supports is a 32-bit Cortex-M, so this is most likely "
                "one hex digit too many. " + _ADDRESS_HINT
            ),
        )
    return value


def _reject_unknown_keys(
    spec: Mapping[str, Any],
    *,
    allowed: type[Enum],
    field_name: str,
) -> None:
    """Fail on keys the builders would otherwise discard in silence.

    Every key not named by *allowed* is dropped on the floor by the builders
    below, so a misspelt or invented key changes nothing and says nothing --
    the user's config looks accepted while the value they wrote never reaches
    the platform model.  That is worst precisely when the key is one they
    reached for after diagnosing a real problem.

    The ``key=str`` on the sort is load-bearing, not tidiness.  YAML keys are
    not all strings: PyYAML resolves a bare ``on``/``off``/``yes``/``no`` to a
    ``bool`` and bare digits to an ``int``, so a mapping with one unknown
    string key and one unknown non-string key sorts a ``str`` against a
    ``bool`` and raises ``TypeError`` -- from ``_prepare_merged_config``, which
    sits outside ``load_config``'s ``try``, so it escapes as a traceback and
    breaks that function's "never a raw exception" contract.
    """
    known = {member.value for member in allowed}
    unknown = sorted((key for key in spec if key not in known), key=str)
    if not unknown:
        return
    suggestions = {
        key: match[0]
        for key in unknown
        if (match := difflib.get_close_matches(str(key), sorted(known), n=1))
    }
    hint = f"Supported keys: {', '.join(sorted(known))}."
    if suggestions:
        did_you_mean = ", ".join(f"{key!r} -> {value!r}" for key, value in suggestions.items())
        hint = f"Did you mean {did_you_mean}? {hint}"
    raise ConfigError(
        f"Unknown key(s) in {field_name}: {', '.join(repr(key) for key in unknown)}.",
        hint=hint,
    )


def _build_custom_boards(raw: Any, registry: PlatformRegistry) -> dict[str, BoardDef]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError("target.custom_boards must be a mapping of name -> definition")

    custom: dict[str, BoardDef] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"target.custom_boards.{name} must be a mapping")
        _reject_unknown_keys(
            spec, allowed=CustomBoardField, field_name=f"target.custom_boards.{name}"
        )
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
            # Inherited rather than dropped: a Blue board's Cooper radio holds
            # its own reset line, and without this pin the power binary never
            # emits the gating in ``_ble_reset.j2``.  A custom board derived
            # from a Blue EVB would then read a higher idle current than the
            # EVB it was copied from, for a reason nothing in the config shows.
            ble_reset_gpio_pin=_optional_int(
                spec.get(
                    "ble_reset_gpio_pin",
                    base_board.ble_reset_gpio_pin if base_board else None,
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
    _reject_unknown_keys(raw, allowed=CustomMemoryField, field_name=field_name)
    values = {
        field.value: (getattr(base, field.value) if base else 0) for field in CustomMemoryField
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
