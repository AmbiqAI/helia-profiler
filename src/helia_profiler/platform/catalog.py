"""Catalog facts projected into the profiler's established platform views."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from functools import cache
from types import MappingProxyType
from typing import TYPE_CHECKING

from helia_platforms import Catalog, Target, builtin
from helia_platforms.model import DomainPoint

from .board import BoardDef
from .memory_map import LinkFamily, MemoryRegion, linked_memory_map
from .policy import BOARD_POLICIES, CORE_CLOCKED_PROBES, PROBE_CLOCK_COMPAT, SOC_POLICIES, SocPolicy
from .provenance import CatalogProvenance, CatalogSource, TargetSelection
from .sdk_descriptor import apollo5b_descriptor
from .sdk_facts import rss_power_off_available
from .soc import ClockDomain, ClockSpeed, CoreArch, MemoryLayout, PmuTier, SocDef, SocOrigin

if TYPE_CHECKING:
    from .linker.model import ApplicationRegion


@cache
def _builtin_catalog() -> Catalog:
    return builtin(include_plugins=False)


@cache
def catalog_views() -> tuple[
    Mapping[str, SocDef],
    Mapping[str, BoardDef],
    Mapping[str, SocDef],
    Mapping[str, TargetSelection],
    CatalogProvenance,
]:
    catalog = _builtin_catalog()
    provenance = catalog_provenance(catalog)
    socs = {
        name: _soc(catalog.resolve(policy.board), name, policy)
        for name, policy in SOC_POLICIES.items()
    }
    descriptor = apollo5b_descriptor()
    socs[descriptor.name] = replace(
        descriptor, origin=SocOrigin.BUILTIN, registered_name=descriptor.name
    )
    boards: dict[str, BoardDef] = {}
    board_socs: dict[str, SocDef] = {}
    selections: dict[str, TargetSelection] = {}
    for policy in BOARD_POLICIES:
        if policy.soc == descriptor.name:
            boards[policy.name] = policy
            selections[policy.name] = TargetSelection("sdk-descriptor", None)
            continue
        target = catalog.resolve(policy.name)
        fitted_psram = _external_kib(target)
        soc_default_psram = socs[target.soc.id].memory.psram_kb
        # A board records differences, so defaults cannot replace a caller's SoC capacity.
        board_psram = fitted_psram if fitted_psram != soc_default_psram else None
        boards[policy.name] = replace(
            policy,
            soc=target.soc.id,
            psram_kb=board_psram,
            is_fpga=target.category == "fpga",
        )
        board_socs[policy.name] = replace(
            socs[target.soc.id],
            jlink_device=target.jlink_device or socs[target.soc.id].jlink_device,
            catalog_target_id=target.id,
        )
        selections[policy.name] = target_selection(target, provenance, policy)
    return (
        MappingProxyType(socs),
        MappingProxyType(boards),
        MappingProxyType(board_socs),
        MappingProxyType(selections),
        provenance,
    )


def target_selection(
    target: Target, provenance: CatalogProvenance, policy: BoardDef, *, custom: bool = False
) -> TargetSelection:
    return TargetSelection(
        "hpx-custom" if custom else "catalog",
        provenance,
        target.id,
        target.cores()[0].id,
        target.boot.name if target.boot else None,
        target.implementation.id if target.implementation else None,
        tuple(addon.id for addon in target.addons),
        tuple(
            name
            for name, applies in (
                ("probe-device-sdk-alias", target.jlink_device is None),
                (
                    "probe-clock-compatibility",
                    policy.soc in PROBE_CLOCK_COMPAT or policy.soc in CORE_CLOCKED_PROBES,
                ),
                ("radio-reset-bsp-wiring", policy.ble_reset_gpio_pin is not None),
                ("hpx-linker-capacity-view", not custom),
                ("catalog-overlay-policy-inheritance", custom),
            )
            if applies
        ),
    )


def catalog_provenance(catalog: Catalog) -> CatalogProvenance:
    identity = catalog.identity()
    return CatalogProvenance(
        identity.package_version,
        identity.schema_version,
        identity.catalog_digest,
        tuple(
            CatalogSource(s.label, s.schema_version, s.sha256, s.distribution_version)
            for s in identity.sources
        ),
    )


def _external_kib(target: Target) -> int:
    return sum(m.size for m in target.external_memories() if m.technology == "psram") // 1024


def _soc(
    target: Target,
    name: str,
    policy: SocPolicy,
    *,
    custom: bool = False,
    policy_name: str | None = None,
) -> SocDef:
    policy_name = policy_name or name
    sdk = target.sdk
    if sdk is None or sdk.c_define is None or sdk.cmsis_header is None:
        raise ValueError(f"Catalog target {target.id!r} lacks required SDK build facts")
    memories = {r.name: r for r in target.memories()}

    def size(*names: str) -> int:
        return sum(memories[n].size for n in names if n in memories) // 1024

    cpu = target.cores()[0].clock_domain
    speeds = []
    for exposed, point, tier in policy.cpu_points:
        fact = target.operating_point(point, domain=cpu)
        if not isinstance(fact, DomainPoint):
            raise ValueError(f"{target.id}: catalog returned no CPU domain point")
        if int(fact.mhz) != fact.mhz:
            raise ValueError(f"{target.id}: profiler clocks require whole MHz")
        speeds.append(ClockSpeed(exposed, int(fact.mhz), tier))
    isa = target.isa()
    scan = (
        memories.get("dtcm")
        or memories.get("tcm")
        or memories.get("ssram0")
        or memories.get("sram")
    )
    if scan is None or scan.base is None:
        raise ValueError(f"{target.id}: profiler has no supported RTT scan aperture")
    scan_size = scan.size
    sram = memories.get("sram") or memories.get("ssram") or memories.get("ssram0")
    # M4 RTT buffers can be in either contiguous working-memory bank.
    if (
        target.core.id in {"cortex-m4", "cortex-m4f"}
        and sram is not None
        and sram.base == scan.base + scan.size
    ):
        scan_size += sram.size
    trace = (
        None
        if policy_name in CORE_CLOCKED_PROBES
        else PROBE_CLOCK_COMPAT.get(policy_name, target.trace_clock_mhz)
    )
    if trace is not None and int(trace) != trace:
        raise ValueError(f"{target.id}: profiler probe clocks require whole MHz")
    # Logical placement chooses one linked SRAM bank, not every physical region.
    memory = MemoryLayout(
        mram_kb=size("mram", "flash", "rram"),
        sram_kb=size("sram", "ssram", "ssram0"),
        dtcm_kb=size("dtcm", "tcm"),
        itcm_kb=size("itcm"),
        psram_kb=_external_kib(target),
    )
    soc = SocDef(
        name=name,
        family=policy.family,
        core={
            "cortex-m4f": CoreArch.CORTEX_M4,
            "cortex-m4": CoreArch.CORTEX_M4,
            "cortex-m55": CoreArch.CORTEX_M55,
        }[target.core.id],
        pmu_tier=PmuTier.ARMV8M_PMU if isa.has_pmu else PmuTier.DWT_ONLY,
        has_mve=isa.has_mve,
        memory=memory,
        clocks=(ClockDomain("cpu", tuple(speeds), "lp"),),
        c_define=sdk.c_define,
        cmsis_header=sdk.cmsis_header,
        rtt_scan_ranges=((scan.base, min(scan_size, 1024 * 1024)),),
        jlink_device=target.jlink_device or policy.probe_alias or "",
        pmu_max_ops=policy.max_ops,
        swo_trace_clock_mhz=int(trace) if trace is not None else None,
        has_usb=policy.usb,
        ssram_full_power_enum=sdk.hal_enums.get("ssram_full_power", "AM_HAL_PWRCTRL_SRAM_3M"),
        has_radio_subsystem=(
            any(c.role == "radio" for c in target.cores())
            and rss_power_off_available(sdk.c_define, sdk.cmsis_header)
        ),
        npu=target.npu.npu.accelerator_config if target.npu else None,
        catalog_target_id=target.id,
        catalog_app_flash_load_addr=target.flash_load_address(),
        catalog_has_dcache=bool(target.core_options.dcache_kb),
        origin=SocOrigin.CUSTOM if custom else SocOrigin.BUILTIN,
        registered_name=None if custom else name,
        memory_bases_like=policy_name if custom else None,
        app_flash_load_addr=target.flash_load_address() if custom else None,
    )
    if policy.scan_app_only and not custom:
        windows = {w.region: w for w in linked_memory_map(soc)}
        extent = windows[MemoryRegion.DTCM].app_window[LinkFamily.GNU]
        soc = replace(soc, rtt_scan_ranges=((extent.start, extent.length),))
    return soc


def application_regions(selection: TargetSelection) -> tuple[ApplicationRegion, ...] | None:
    """Sourced application intervals for a catalog selection, in its core's address space."""
    from .linker.model import ApplicationRegion
    from .soc import MemoryRange

    if selection.origin != "catalog" or selection.target_id is None:
        return None
    target = _builtin_catalog().resolve(
        selection.target_id, addons=selection.addons, boot=selection.boot
    )
    core = selection.core or target.cores()[0].id
    ranges = target.application_ranges(core, include_discouraged=True)
    names = {
        "mram": MemoryRegion.MRAM,
        "flash": MemoryRegion.MRAM,
        "rram": MemoryRegion.MRAM,
        "dtcm": MemoryRegion.DTCM,
        "tcm": MemoryRegion.DTCM,
        "itcm": MemoryRegion.ITCM,
        "sram": MemoryRegion.SRAM,
        "ssram": MemoryRegion.SRAM,
        "ssram0": MemoryRegion.SRAM,
    }
    result = []
    for region in target.memories(core, include_discouraged=True):
        logical = MemoryRegion.PSRAM if region.technology == "psram" else names.get(region.name)
        if logical is None:
            continue
        access = target.reachable(core, region.name)
        offset = (
            access.address - region.base
            if access is not None and access.address is not None and region.base is not None
            else 0
        )
        intervals = (
            tuple(MemoryRange(r.start + offset, r.size) for r in ranges[region.name])
            if region.name in ranges and selection.boot is not None
            else None
        )
        address = (
            access.address if access is not None and access.address is not None else region.base
        )
        aperture = (
            MemoryRange(address, region.size)
            if logical is MemoryRegion.PSRAM and address is not None
            else None
        )
        result.append(ApplicationRegion(region.name, logical, intervals, aperture))
    return tuple(result)
