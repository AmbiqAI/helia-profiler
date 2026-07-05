"""Typed firmware template render context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_ARENA_SIZE_BYTES, DEFAULT_POWER_WINDOW_TARGET_MS, Transport
from ..counters import (
    resolve_counters,
    resolve_legacy_presets,
    plan_passes,
    supported_groups_for_domains,
    validate_group_selection,
)
from ..engines import EngineType
from ..errors import FirmwareError
from ..placement import Placement
from ..usb_identity import USB_MARKER_PRODUCT, usb_marker_serial
from .op_resolver import build_resolver_plan

if TYPE_CHECKING:
    from ..config import ProfileConfig
    from ..pipeline import PipelineContext
    from ..engines.base import ArenaRegion


_PMU_PRESET_MAP: dict[str, str] = {
    "basic_cpu": "NSX_PMU_PRESET_BASIC_CPU",
    "memory": "NSX_PMU_PRESET_MEMORY",
    "mve": "NSX_PMU_PRESET_MVE",
    "ml_default": "NSX_PMU_PRESET_ML_DEFAULT",
}


@dataclass(frozen=True)
class PmuPassContext:
    name: str
    custom: bool
    event_ids: tuple[str, ...]
    counter_names: tuple[str, ...]
    num_counters: int
    c_enum: str | None
    group: str


@dataclass(frozen=True)
class AotOpContext:
    id: int
    op_type: str


@dataclass(frozen=True)
class SyncContext:
    power_sync_enabled: bool
    sync_gpio_pin: int
    lockstep: bool
    state_gpio_pin: int
    go_gpio_pin: int


@dataclass(frozen=True)
class TransportContext:
    transport: Transport
    usb_serial_marker: str | None
    usb_serial_product: str
    printf_linkage: str


@dataclass(frozen=True)
class MemoryContext:
    model_location: str
    arena_region: Placement
    weights_region: Placement
    arena_size: int
    model_size: int
    arena_regions: tuple["ArenaRegion", ...]
    allocate_arenas: bool
    has_dcache: bool
    manages_shared_ssram_power: bool
    force_shared_sram: bool


@dataclass(frozen=True)
class PmuContext:
    pmu_passes: tuple[PmuPassContext, ...]
    pmu_pass_names: tuple[str, ...]
    profiling_backends: tuple[str, ...]
    has_armv8m_pmu: bool
    cmsis_device_header: str
    perf_mode_symbol: str
    perf_mode_mhz: int
    apollo3_burst: bool


@dataclass(frozen=True)
class PowerWindowContext:
    iterations: int
    warmup: int
    clean_warmup: int
    clean_iters: int
    window_mode: str
    window_target_ms: int
    window_min: int
    window_max: int
    clean_window_probe: str
    clean_window_trace: bool
    extreme_mode: bool
    heartbeat_enabled: bool
    heartbeat_every_n_ops: int
    heartbeat_every_ms: int
    clean_window_timer: str
    gate_debug_domain_in_window: bool


@dataclass(frozen=True)
class EngineContext:
    engine_type: EngineType
    engine_header: str
    resolver_mode: str
    resolver_max_ops: int
    resolver_registrations: tuple[str, ...]
    resource_variable_count: int
    aot_prefix: str
    aot_op_manifest: tuple[AotOpContext, ...]


@dataclass(frozen=True)
class FirmwareRenderContext:
    sync: SyncContext
    transport: TransportContext
    memory: MemoryContext
    pmu: PmuContext
    power_window: PowerWindowContext
    engine: EngineContext

    @classmethod
    def from_pipeline_context(
        cls,
        ctx: "PipelineContext",
        *,
        arena_regions: list["ArenaRegion"] | None = None,
    ) -> "FirmwareRenderContext":
        assert ctx.soc is not None
        assert ctx.board is not None
        assert ctx.engine_artifacts is not None

        config = ctx.config
        soc = ctx.soc
        artifacts = ctx.engine_artifacts
        engine_type = artifacts.engine_type
        arena_region = ctx.arena_region or Placement.TCM
        weights_region = ctx.weights_region or Placement.MRAM
        aot_arena_regions = tuple(arena_regions or ())
        power_sync_enabled = config.power.enabled and config.power.mode == "external"
        profiling_backends = tuple(soc.profiling_backends)
        clock = ctx.run_metadata.platform
        assert clock is not None
        perf_mode_mhz = clock.cpu_clock_mhz
        burst_base_mhz = soc.capabilities.clock.direct_burst_base_mhz
        resolver_plan = build_resolver_plan(
            engine_type=engine_type,
            engine_config=config.engine.config,
            model_analysis=ctx.model_analysis,
        )
        resource_variable_count = sum(
            1
            for layer in (ctx.model_analysis.layers if ctx.model_analysis else ())
            if layer.op == "VAR_HANDLE"
        )
        aot_manifest = tuple(
            AotOpContext(id=int(op["id"]), op_type=str(op["op_type"]))
            for op in (artifacts.aot_op_manifest or [])
        )
        pmu_passes = tuple(_resolve_pmu_passes(config, soc))
        transport = config.target.transport
        printf_linkage = "static " if engine_type is EngineType.HELIA_AOT else ""
        return cls(
            sync=SyncContext(
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=config.power.sync_gpio_pin,
                lockstep=config.power.lockstep,
                state_gpio_pin=config.power.state_gpio_pin,
                go_gpio_pin=config.power.go_gpio_pin,
            ),
            transport=TransportContext(
                transport=transport,
                usb_serial_marker=usb_marker_serial(
                    ctx.resolved_jlink_serial or config.target.jlink_serial
                ),
                usb_serial_product=USB_MARKER_PRODUCT,
                printf_linkage=printf_linkage,
            ),
            memory=MemoryContext(
                model_location=config.model.model_location,
                arena_region=arena_region,
                weights_region=weights_region,
                arena_size=config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES,
                model_size=config.model.path.stat().st_size if config.model.path.exists() else 0,
                arena_regions=aot_arena_regions,
                allocate_arenas=artifacts.aot_allocate_arenas,
                has_dcache=soc.capabilities.memory.has_dcache,
                manages_shared_ssram_power=soc.capabilities.memory.has_shared_ssram_power_domain,
                force_shared_sram=config.profiling.force_shared_sram,
            ),
            pmu=PmuContext(
                pmu_passes=pmu_passes,
                pmu_pass_names=tuple(p.name for p in pmu_passes),
                profiling_backends=profiling_backends,
                has_armv8m_pmu="armv8m-pmu" in profiling_backends,
                cmsis_device_header=soc.cmsis_header,
                perf_mode_symbol=clock.cpu_perf_tier,
                perf_mode_mhz=perf_mode_mhz,
                apollo3_burst=burst_base_mhz is not None and perf_mode_mhz > burst_base_mhz,
            ),
            power_window=PowerWindowContext(
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                clean_warmup=max(1, config.profiling.warmup),
                clean_iters=max(1, config.profiling.iterations),
                window_mode=config.profiling.window_mode,
                window_target_ms=_effective_window_target_ms(config),
                window_min=config.profiling.window_min,
                window_max=config.profiling.window_max,
                clean_window_probe=config.profiling.clean_window_probe,
                clean_window_trace=config.profiling.clean_window_trace,
                extreme_mode=config.profiling.extreme_mode,
                heartbeat_enabled=config.target.heartbeat.enabled,
                heartbeat_every_n_ops=(
                    config.target.heartbeat.every_n_ops if config.target.heartbeat.enabled else 0
                ),
                heartbeat_every_ms=(
                    config.target.heartbeat.every_ms if config.target.heartbeat.enabled else 0
                ),
                clean_window_timer=soc.capabilities.clock.clean_window_timer,
                gate_debug_domain_in_window=soc.capabilities.clock.gate_debug_domain_in_window,
            ),
            engine=EngineContext(
                engine_type=engine_type,
                engine_header=artifacts.engine_header,
                resolver_mode=resolver_plan.mode,
                resolver_max_ops=resolver_plan.max_ops,
                resolver_registrations=tuple(resolver_plan.registrations),
                resource_variable_count=resource_variable_count,
                aot_prefix=artifacts.aot_prefix or "",
                aot_op_manifest=aot_manifest,
            ),
        )

    def to_template_vars(self) -> dict[str, object]:
        """Flatten typed fields to the legacy Jinja variable names."""
        return {
            "power_sync_enabled": self.sync.power_sync_enabled,
            "sync_gpio_pin": self.sync.sync_gpio_pin,
            "lockstep": self.sync.lockstep,
            "state_gpio_pin": self.sync.state_gpio_pin,
            "go_gpio_pin": self.sync.go_gpio_pin,
            "transport": self.transport.transport,
            "usb_serial_marker": self.transport.usb_serial_marker,
            "usb_serial_product": self.transport.usb_serial_product,
            "printf_linkage": self.transport.printf_linkage,
            "model_location": self.memory.model_location,
            "arena_region": self.memory.arena_region,
            "weights_region": self.memory.weights_region,
            "arena_size": self.memory.arena_size,
            "model_size": self.memory.model_size,
            "arena_regions": self.memory.arena_regions,
            "allocate_arenas": self.memory.allocate_arenas,
            "has_dcache": self.memory.has_dcache,
            "manages_shared_ssram_power": self.memory.manages_shared_ssram_power,
            "force_shared_sram": self.memory.force_shared_sram,
            "pmu_passes": self.pmu.pmu_passes,
            "pmu_pass_names": self.pmu.pmu_pass_names,
            "profiling_backends": self.pmu.profiling_backends,
            "has_armv8m_pmu": self.pmu.has_armv8m_pmu,
            "cmsis_device_header": self.pmu.cmsis_device_header,
            "perf_mode_symbol": self.pmu.perf_mode_symbol,
            "perf_mode_mhz": self.pmu.perf_mode_mhz,
            "apollo3_burst": self.pmu.apollo3_burst,
            "iterations": self.power_window.iterations,
            "warmup": self.power_window.warmup,
            "clean_warmup": self.power_window.clean_warmup,
            "clean_iters": self.power_window.clean_iters,
            "window_mode": self.power_window.window_mode,
            "window_target_ms": self.power_window.window_target_ms,
            "window_min": self.power_window.window_min,
            "window_max": self.power_window.window_max,
            "clean_window_probe": self.power_window.clean_window_probe,
            "clean_window_trace": self.power_window.clean_window_trace,
            "extreme_mode": self.power_window.extreme_mode,
            "heartbeat_enabled": self.power_window.heartbeat_enabled,
            "heartbeat_every_n_ops": self.power_window.heartbeat_every_n_ops,
            "heartbeat_every_ms": self.power_window.heartbeat_every_ms,
            "clean_window_timer": self.power_window.clean_window_timer,
            "gate_debug_domain_in_window": self.power_window.gate_debug_domain_in_window,
            "engine_header": self.engine.engine_header,
            "resolver_mode": self.engine.resolver_mode,
            "resolver_max_ops": self.engine.resolver_max_ops,
            "resolver_registrations": self.engine.resolver_registrations,
            "resource_variable_count": self.engine.resource_variable_count,
            "aot_prefix": self.engine.aot_prefix,
            "aot_op_manifest": self.engine.aot_op_manifest,
        }


def _effective_window_target_ms(config: "ProfileConfig") -> int:
    target_ms = config.profiling.window_target_ms
    if config.power.enabled and config.profiling.window_mode == "auto":
        target_ms = max(target_ms, DEFAULT_POWER_WINDOW_TARGET_MS)
    return target_ms


def _resolve_pmu_passes(config: Any, soc: Any | None = None) -> list[PmuPassContext]:
    profiling = config.profiling
    if soc is not None:
        supported_groups = supported_groups_for_domains(soc.profiling_domains)
        try:
            if profiling.pmu_counters is not None:
                validate_group_selection(profiling.pmu_counters, supported_groups=supported_groups)
            else:
                validate_group_selection(
                    resolve_legacy_presets(profiling.pmu_presets),
                    supported_groups=supported_groups,
                )
        except ValueError as exc:
            raise FirmwareError(
                str(exc),
                hint=(
                    f"Target '{soc.name}' supports PMU groups: "
                    f"{', '.join(supported_groups) if supported_groups else 'none'}."
                ),
            ) from exc

    if profiling.pmu_counters is not None:
        counters = resolve_counters(profiling.pmu_counters)
        passes = plan_passes(counters)
        return [
            PmuPassContext(
                name=p.name,
                custom=True,
                event_ids=tuple(f"0x{c.event_id:04X}" for c in p.counters),
                counter_names=tuple(c.name for c in p.counters),
                num_counters=len(p.counters),
                c_enum=None,
                group=p.group,
            )
            for p in passes
        ]

    result: list[PmuPassContext] = []
    for preset_name in profiling.pmu_presets:
        c_enum = _PMU_PRESET_MAP.get(preset_name, "NSX_PMU_PRESET_ML_DEFAULT")
        counters = resolve_counters(resolve_legacy_presets([preset_name]))
        result.append(
            PmuPassContext(
                name=preset_name,
                custom=False,
                event_ids=(),
                counter_names=tuple(c.name for c in counters),
                num_counters=len(counters),
                c_enum=c_enum,
                group=preset_name,
            )
        )
    if not result:
        counters = resolve_counters(resolve_legacy_presets(["ml_default"]))
        result = [
            PmuPassContext(
                name="ml_default",
                custom=False,
                event_ids=(),
                counter_names=tuple(c.name for c in counters),
                num_counters=len(counters),
                c_enum="NSX_PMU_PRESET_ML_DEFAULT",
                group="ml_default",
            )
        ]
    return result
