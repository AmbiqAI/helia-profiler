"""Typed firmware template render context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..config import DEFAULT_ARENA_SIZE_BYTES, CleanWindowProbe, Transport
from ..engines import EngineType
from ..engines.base import ExecutorchArtifacts, HeliaAotArtifacts, HeliaMlArtifacts
from ..errors import FirmwareError, PipelineError
from ..placement import Placement
from ..platform.counters import (
    plan_passes,
    resolve_counters,
    supported_groups_for_domains,
    validate_group_selection,
)
from ..target.lifecycle import resolve_power_lockstep
from ..transport.usb_identity import USB_MARKER_PRODUCT, usb_marker_serial
from .op_resolver import build_resolver_plan

if TYPE_CHECKING:
    from ..config import ProfileConfig
    from ..pipeline import PipelineContext
    from ..engines.base import ArenaRegion


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
class PowerMonitorContext:
    """On-target power monitor (INA228) render inputs.

    ``power_monitor`` is ``None`` for every run without a ``power.ina228``
    config block, and all template content is gated on it — non-monitor
    renders stay byte-identical (WP2). Physical quantities are carried as
    scaled integers so the rendered C literals are exact and the render
    digest is stable across float formatting.
    """

    power_monitor: str | None
    # True when the monitor is the measurement of record (driver: ina228):
    # a setup failure is then terminal. False makes it a bystander — a
    # missing or mis-wired chip logs a diagnostic and the run continues
    # without a monitor payload, because the actual measurement belongs to
    # an external instrument.
    ina228_required: bool = False
    ina228_i2c_iom: int = 0
    ina228_i2c_address: int = 0
    ina228_i2c_speed_hz: int = 0
    ina228_shunt_micro_ohms: int = 0
    ina228_max_current_ma: int = 0
    ina228_conversion_time_us: int = 0
    ina228_averaging_count: int = 0
    ina228_adc_range: int = 0
    # Expected SHUNT_CAL register value, precomputed host-side. Carried into
    # the calibration_id for provenance and range-checked at render time.
    ina228_shunt_cal: int = 0
    # SHUNT_CAL = 13107.2e6 * CURRENT_LSB * R_shunt (x4 at ADCRANGE=1), so
    # firmware recovers the effective CURRENT_LSB as SHUNT_CAL / this. Kept
    # as a rendered double literal so the scaling tracks the register that is
    # actually programmed rather than the nominal configuration.
    ina228_current_lsb_divisor: str = "1.0"
    ina228_calibration_id: str = ""

    @classmethod
    def from_config(cls, config: "ProfileConfig") -> "PowerMonitorContext":
        power = config.power
        # The presence of a power.ina228 block — not the driver name — decides
        # whether firmware talks to a monitor. `driver` selects who *measures*
        # (on-device vs host instrument), so `driver: joulescope` with an
        # ina228 block builds identical firmware and lets an external
        # instrument observe the monitor's own cost. This must stay in step
        # with the module-selection gate in firmware/__init__.py: when the two
        # disagreed, runs silently built no monitor at all while appearing to
        # configure one, which invalidated a bench sweep.
        ina = power.ina228
        if not power.monitor_selected or ina is None:
            return cls(power_monitor=None)
        shunt_ohms = ina.resolved_shunt_ohms
        shunt_micro_ohms = round(shunt_ohms * 1_000_000)
        max_current_ma = round(ina.max_current_a * 1_000)
        # ADCRANGE=1 quarters the shunt full scale (±163.84 mV -> ±40.96 mV)
        # for 4x resolution; select it whenever the configured worst-case
        # shunt drop fits, otherwise stay on the wide range.
        adc_range = 1 if ina.max_current_a * shunt_ohms <= 0.04096 else 0
        # SHUNT_CAL = 13107.2e6 * CURRENT_LSB * R_shunt (x4 when ADCRANGE=1),
        # CURRENT_LSB = max_current / 2^19. 15-bit register.
        current_lsb = ina.max_current_a / (1 << 19)
        current_lsb_divisor = 13107.2e6 * shunt_ohms * (4 if adc_range else 1)
        shunt_cal = round(13107.2e6 * current_lsb * shunt_ohms) * (4 if adc_range else 1)
        if not 0 < shunt_cal <= 0x7FFF:
            raise FirmwareError(
                f"INA228 SHUNT_CAL {shunt_cal} out of range for "
                f"shunt={shunt_ohms} ohm, max_current={ina.max_current_a} A; "
                "adjust power.ina228.max_current_a."
            )
        board_tag = f":{ina.board}" if ina.board is not None else ""
        calibration_id = ina.calibration_id or (
            f"ina228{board_tag}:r{shunt_micro_ohms}uohm:i{max_current_ma}ma:adc{adc_range}"
        )
        return cls(
            power_monitor="ina228",
            ina228_required=power.driver == "ina228",
            ina228_i2c_iom=ina.i2c_iom,
            ina228_i2c_address=ina.resolved_i2c_address,
            ina228_i2c_speed_hz=ina.i2c_speed_hz,
            ina228_shunt_micro_ohms=shunt_micro_ohms,
            ina228_max_current_ma=max_current_ma,
            ina228_conversion_time_us=ina.conversion_time_us,
            ina228_averaging_count=ina.averaging_count,
            ina228_adc_range=adc_range,
            ina228_shunt_cal=shunt_cal,
            ina228_current_lsb_divisor=repr(current_lsb_divisor),
            ina228_calibration_id=calibration_id,
        )


@dataclass(frozen=True)
class TransportContext:
    transport: Transport
    usb_serial_marker: str | None
    usb_serial_product: str
    printf_linkage: str


@dataclass(frozen=True)
class MemoryContext:
    arena_region: Placement
    weights_region: Placement
    arena_size: int
    model_size: int
    arena_regions: tuple["ArenaRegion", ...]
    allocate_arenas: bool
    has_dcache: bool
    manages_shared_ssram_power: bool
    ssram_full_power_enum: str
    force_shared_sram: bool
    psram_clock_hz: int


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
    #: ``soc.pmu_max_ops`` — sizes the per-layer instrumentation storage in
    #: every engine's firmware (kMaxLayers / kMaxOps).
    pmu_max_ops: int


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
    #: Effective timer for the dedicated power binary's measured window —
    #: ``SocCapabilities.power_window_timer``, which owns the predicate.  The
    #: templates select between this and ``clean_window_timer`` on
    #: ``power_only`` alone and carry no policy of their own.
    power_window_timer: str
    #: ``SocCapabilities.clean_window_needs_probe_attach`` — the profile
    #: binary's DWT-timed window only advances while a debugger holds the core
    #: debug power domain up, so it must not open before the host attach
    #: completes.  The capability owns the predicate; the templates combine it
    #: with the transport (only RTT exposes a host-attach signal) and
    #: ``power_only``, exactly as they do for the two window timers above.
    clean_window_needs_probe_attach: bool
    gate_debug_domain_in_window: bool
    broad_peripheral_shutdown: bool
    crypto_otp_shutdown: bool
    has_radio_subsystem: bool
    ble_reset_gpio_pin: int | None


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
    executorch_method_arena_size: int
    executorch_planned_arena_size: int
    executorch_temporary_arena_size: int
    executorch_input_size: int
    executorch_output_size: int
    # Resolved per-buffer regions, always "tcm" or "sram".
    executorch_planned_arena_region: str
    executorch_method_arena_region: str
    executorch_temporary_arena_region: str
    executorch_io_region: str

    # heliaML-only: run entry-point call shape ("scores" | "class" | "value").
    helia_ml_run_shape: str = ""


@dataclass(frozen=True)
class FirmwareRenderContext:
    sync: SyncContext
    transport: TransportContext
    memory: MemoryContext
    pmu: PmuContext
    power_window: PowerWindowContext
    power_monitor: PowerMonitorContext
    engine: EngineContext

    @classmethod
    def from_pipeline_context(
        cls,
        ctx: "PipelineContext",
        *,
        arena_regions: list["ArenaRegion"] | None = None,
    ) -> "FirmwareRenderContext":
        config = ctx.config
        soc = ctx.resolved_soc
        board = ctx.resolved_board
        artifacts = ctx.prepared_artifacts
        engine_type = artifacts.engine_type
        arena_region = ctx.arena_region or Placement.TCM
        weights_region = ctx.weights_region or Placement.MRAM
        aot_arena_regions = tuple(arena_regions or ())
        power_sync_enabled = config.power.gated_external_capture
        profiling_backends = tuple(soc.profiling_backends)
        clock = ctx.run_metadata.platform
        if clock is None:
            # A sub-field of the (non-optional) run_metadata, so no
            # PipelineContext accessor covers it — same stage-ordering
            # precondition, stated the same way.
            raise PipelineError(
                "ctx.run_metadata.platform is not available — "
                "ResolvePlatformStage has not run.",
                hint="ResolvePlatformStage must run before firmware render "
                "context construction. This is a bug in heliaPROFILER — "
                "please file an issue.",
            )
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
        # Engine-specific render inputs, narrowed once here. EngineContext
        # keeps a field per engine's inputs, so an engine that does not own a
        # given field renders the same neutral value it always has: aot_prefix
        # "", no operator manifest, arenas allocated by the engine, ExecuTorch
        # sizes 0, and every ExecuTorch buffer following the resolved arena
        # region.
        if isinstance(artifacts, (HeliaAotArtifacts, HeliaMlArtifacts)):
            # Both are generated modules: the template names their entry
            # points through the prefix and binds their arenas itself.
            # Only heliaAOT carries a per-operator manifest.
            aot_prefix = artifacts.aot_prefix
            allocate_arenas = artifacts.aot_allocate_arenas
            aot_manifest = tuple(
                AotOpContext(id=int(op["id"]), op_type=str(op["op_type"]))
                for op in (artifacts.aot_op_manifest or [])
            ) if isinstance(artifacts, HeliaAotArtifacts) else ()
        else:
            aot_prefix = ""
            allocate_arenas = True
            aot_manifest = ()

        default_executorch_region = _executorch_default_region(arena_region)
        if isinstance(artifacts, ExecutorchArtifacts):
            method_arena_size = artifacts.executorch_method_arena_size
            planned_arena_size = artifacts.executorch_planned_arena_size
            temporary_arena_size = artifacts.executorch_temporary_arena_size
            input_size = artifacts.executorch_input_size
            output_size = artifacts.executorch_output_size
            # Per-buffer overrides win; otherwise every runtime buffer follows
            # the run's resolved arena region (which the memory planner keeps
            # within tcm/sram for ExecuTorch).
            planned_arena_region = (
                artifacts.executorch_planned_arena_region or default_executorch_region
            )
            method_arena_region = (
                artifacts.executorch_method_arena_region or default_executorch_region
            )
            temporary_arena_region = (
                artifacts.executorch_temporary_arena_region or default_executorch_region
            )
            io_region = artifacts.executorch_io_region or default_executorch_region
        else:
            method_arena_size = 0
            planned_arena_size = 0
            temporary_arena_size = 0
            input_size = 0
            output_size = 0
            planned_arena_region = default_executorch_region
            method_arena_region = default_executorch_region
            temporary_arena_region = default_executorch_region
            io_region = default_executorch_region

        pmu_passes = tuple(_resolve_pmu_passes(config, soc))
        transport = config.target.transport
        # Both single-C-entry engines compile main.cc alone, so hpx_printf
        # needs no external linkage.
        printf_linkage = (
            "static "
            if engine_type in (EngineType.HELIA_AOT, EngineType.HELIA_ML)
            else ""
        )
        return cls(
            sync=SyncContext(
                power_sync_enabled=power_sync_enabled,
                sync_gpio_pin=config.power.sync_gpio_pin,
                lockstep=resolve_power_lockstep(ctx),
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
                arena_region=arena_region,
                weights_region=weights_region,
                arena_size=config.model.arena_size or DEFAULT_ARENA_SIZE_BYTES,
                model_size=config.model.path.stat().st_size if config.model.path.exists() else 0,
                arena_regions=aot_arena_regions,
                allocate_arenas=allocate_arenas,
                has_dcache=soc.capabilities.memory.has_dcache,
                manages_shared_ssram_power=soc.capabilities.memory.has_shared_ssram_power_domain,
                ssram_full_power_enum=soc.ssram_full_power_enum,
                force_shared_sram=config.profiling.force_shared_sram,
                psram_clock_hz=config.target.psram.clock_hz,
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
                pmu_max_ops=soc.pmu_max_ops,
            ),
            power_window=PowerWindowContext(
                iterations=config.profiling.iterations,
                warmup=config.profiling.warmup,
                clean_warmup=max(1, config.profiling.warmup),
                clean_iters=max(1, config.profiling.iterations),
                window_mode=config.profiling.window_mode,
                window_target_ms=config.effective_window_target_ms,
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
                power_window_timer=soc.capabilities.power_window_timer,
                clean_window_needs_probe_attach=(
                    soc.capabilities.clean_window_needs_probe_attach
                ),
                gate_debug_domain_in_window=soc.capabilities.clock.gate_debug_domain_in_window,
                broad_peripheral_shutdown=soc.capabilities.clock.broad_peripheral_shutdown,
                crypto_otp_shutdown=soc.capabilities.clock.crypto_otp_shutdown,
                has_radio_subsystem=soc.has_radio_subsystem,
                ble_reset_gpio_pin=board.ble_reset_gpio_pin,
            ),
            power_monitor=PowerMonitorContext.from_config(config),
            engine=EngineContext(
                engine_type=engine_type,
                engine_header=artifacts.engine_header,
                resolver_mode=resolver_plan.mode,
                resolver_max_ops=resolver_plan.max_ops,
                resolver_registrations=tuple(resolver_plan.registrations),
                resource_variable_count=resource_variable_count,
                aot_prefix=aot_prefix,
                aot_op_manifest=aot_manifest,
                executorch_method_arena_size=method_arena_size,
                executorch_planned_arena_size=planned_arena_size,
                executorch_temporary_arena_size=temporary_arena_size,
                executorch_input_size=input_size,
                executorch_output_size=output_size,
                executorch_planned_arena_region=planned_arena_region,
                executorch_method_arena_region=method_arena_region,
                executorch_temporary_arena_region=temporary_arena_region,
                executorch_io_region=io_region,
                helia_ml_run_shape=getattr(artifacts, "helia_ml_run_shape", None) or "",
            ),
        )

    @property
    def power_binary_needs_gpio(self) -> bool:
        """Whether the dedicated power binary needs the nsx-gpio module.

        Two independent consumers: the GPIO lock-step handshake (external
        mode) and the BLE-controller reset drive on Blue-variant boards
        (see ``_ble_reset.j2``), which is emitted regardless of mode. Both
        the ``nsx_gpio.h`` include and the CMake link line read this single
        property, and ``firmware/__init__.py`` selects the module on the
        same basis — when the link condition was narrower than the include,
        an internal-mode run on a Blue board fetched nsx-gpio, emitted the
        header, and failed to compile with 'nsx_gpio.h: No such file'.
        """
        return (
            self.sync.power_sync_enabled
            or self.power_window.ble_reset_gpio_pin is not None
        )

    def to_template_vars(self, *, power_only: bool = False) -> dict[str, object]:
        """Flatten typed fields to the legacy Jinja variable names.

        ``power_only`` selects which binary this render produces: the
        transport-attached PMU-phase binary (False, the default) or the
        dedicated free-running power binary (True). It is passed here rather
        than stored on the context because one context renders both binaries
        of a run — and the window-timer resolution below depends on it.
        """
        return {
            "power_only": power_only,
            **resolve_window_timer(
                clean_window_probe=self.power_window.clean_window_probe,
                power_only=power_only,
                power_window_timer=self.power_window.power_window_timer,
                clean_window_timer=self.power_window.clean_window_timer,
            ),
            "power_sync_enabled": self.sync.power_sync_enabled,
            "power_binary_needs_gpio": self.power_binary_needs_gpio,
            "sync_gpio_pin": self.sync.sync_gpio_pin,
            "lockstep": self.sync.lockstep,
            "state_gpio_pin": self.sync.state_gpio_pin,
            "go_gpio_pin": self.sync.go_gpio_pin,
            "transport": self.transport.transport,
            "usb_serial_marker": self.transport.usb_serial_marker,
            "usb_serial_product": self.transport.usb_serial_product,
            "printf_linkage": self.transport.printf_linkage,
            "arena_region": self.memory.arena_region,
            "weights_region": self.memory.weights_region,
            "arena_size": self.memory.arena_size,
            "model_size": self.memory.model_size,
            "arena_regions": self.memory.arena_regions,
            "allocate_arenas": self.memory.allocate_arenas,
            "has_dcache": self.memory.has_dcache,
            "manages_shared_ssram_power": self.memory.manages_shared_ssram_power,
            "ssram_full_power_enum": self.memory.ssram_full_power_enum,
            "force_shared_sram": self.memory.force_shared_sram,
            "psram_clock_hz": self.memory.psram_clock_hz,
            "pmu_passes": self.pmu.pmu_passes,
            "pmu_pass_names": self.pmu.pmu_pass_names,
            "profiling_backends": self.pmu.profiling_backends,
            "has_armv8m_pmu": self.pmu.has_armv8m_pmu,
            "cmsis_device_header": self.pmu.cmsis_device_header,
            "perf_mode_symbol": self.pmu.perf_mode_symbol,
            "perf_mode_mhz": self.pmu.perf_mode_mhz,
            "apollo3_burst": self.pmu.apollo3_burst,
            "pmu_max_ops": self.pmu.pmu_max_ops,
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
            "power_window_timer": self.power_window.power_window_timer,
            "clean_window_needs_probe_attach": (
                self.power_window.clean_window_needs_probe_attach
            ),
            "gate_debug_domain_in_window": self.power_window.gate_debug_domain_in_window,
            "broad_peripheral_shutdown": self.power_window.broad_peripheral_shutdown,
            "crypto_otp_shutdown": self.power_window.crypto_otp_shutdown,
            "has_radio_subsystem": self.power_window.has_radio_subsystem,
            "ble_reset_gpio_pin": self.power_window.ble_reset_gpio_pin,
            "power_monitor": self.power_monitor.power_monitor,
            "ina228_required": self.power_monitor.ina228_required,
            "ina228_i2c_iom": self.power_monitor.ina228_i2c_iom,
            "ina228_i2c_address": self.power_monitor.ina228_i2c_address,
            "ina228_i2c_speed_hz": self.power_monitor.ina228_i2c_speed_hz,
            "ina228_shunt_micro_ohms": self.power_monitor.ina228_shunt_micro_ohms,
            "ina228_max_current_ma": self.power_monitor.ina228_max_current_ma,
            "ina228_conversion_time_us": self.power_monitor.ina228_conversion_time_us,
            "ina228_averaging_count": self.power_monitor.ina228_averaging_count,
            "ina228_adc_range": self.power_monitor.ina228_adc_range,
            "ina228_shunt_cal": self.power_monitor.ina228_shunt_cal,
            "ina228_current_lsb_divisor": self.power_monitor.ina228_current_lsb_divisor,
            "ina228_calibration_id": self.power_monitor.ina228_calibration_id,
            # Wire-protocol spelling of the engine, emitted as HPX_ENGINE= by
            # every firmware template (_main_base.cc.j2).  The host parser
            # takes any HPX_(\w+)=value line, so the hyphenated EngineType
            # values are underscored here rather than shipped as-is.
            "engine_wire_name": self.engine.engine_type.wire_name,
            "engine_header": self.engine.engine_header,
            "resolver_mode": self.engine.resolver_mode,
            "resolver_max_ops": self.engine.resolver_max_ops,
            "resolver_registrations": self.engine.resolver_registrations,
            "resource_variable_count": self.engine.resource_variable_count,
            "aot_prefix": self.engine.aot_prefix,
            "aot_op_manifest": self.engine.aot_op_manifest,
            "executorch_method_arena_size": self.engine.executorch_method_arena_size,
            "executorch_planned_arena_size": self.engine.executorch_planned_arena_size,
            "executorch_temporary_arena_size": (
                self.engine.executorch_temporary_arena_size
            ),
            "executorch_input_size": self.engine.executorch_input_size,
            "executorch_output_size": self.engine.executorch_output_size,
            "executorch_planned_arena_region": self.engine.executorch_planned_arena_region,
            "executorch_method_arena_region": self.engine.executorch_method_arena_region,
            "executorch_temporary_arena_region": (
                self.engine.executorch_temporary_arena_region
            ),
            "executorch_io_region": self.engine.executorch_io_region,
            "helia_ml_run_shape": self.engine.helia_ml_run_shape,
        }


def resolve_window_timer(
    *,
    clean_window_probe: str,
    power_only: bool,
    power_window_timer: str,
    clean_window_timer: str,
) -> dict[str, object]:
    """Resolve which clock times the measured window — host-side, once (#118).

    The measured window must not be timed by a clock the binary cannot read.
    ``DWT->CYCCNT`` lives in the CoreSight debug domain, which the dedicated
    power binary either powers down itself or simply has nothing holding up
    (it free-runs with no debugger asserting CDBGPWRUPREQ) — reads are then
    frozen or garbage and the reported window duration is wrong (#106, #107).
    The per-SoC half of that predicate lives in
    ``SocCapabilities.power_window_timer``; this function owns the
    per-render half: pick the power binary's answer under ``power_only``,
    the family preference (``clean_window_timer``, finer DWT resolution
    under an attached debugger) otherwise.

    The opt-in busy_loop probe is pinned to STIMER on every family — a
    deliberate simplification, not a per-family necessity: only Apollo5 and
    the Apollo3/4 power binaries actually lose DWT, but one clock for all
    three trades DWT's cycle resolution for STIMER's ~30.5 us tick,
    negligible on a multi-millisecond window and cheap next to a second
    code path whose only reachable configuration is the one family
    combination that does not need it. What the probe used to do instead —
    inherit the per-family answer — meant calibrating against an
    already-dead DWT on AP3/AP4 power binaries, which fabricated the
    reported window duration (#112).

    This resolution used to live as three ``{% set %}`` lines duplicated
    verbatim at the top of ``main.cc.j2`` and ``main_aot.cc.j2`` — the
    exact drift vector ``SocCapabilities.power_window_timer`` was created
    to close, one layer up (#118). The templates now read the resolved
    names and carry no policy of their own.
    """
    # ``==`` rather than ``is``: the parameter is annotated ``str`` because
    # this is the render boundary — production passes the config's
    # ``CleanWindowProbe`` member through, while hand-built render contexts
    # pass the bare wire string.  A ``StrEnum`` member compares equal to its
    # value, so one comparison covers both.
    busy_loop_probe = clean_window_probe == CleanWindowProbe.BUSY_LOOP
    window_timer = (
        "stimer"
        if busy_loop_probe
        else (power_window_timer if power_only else clean_window_timer)
    )
    return {
        "busy_loop_probe": busy_loop_probe,
        "window_timer": window_timer,
        "use_stimer_window": window_timer == "stimer",
    }


def _executorch_default_region(arena_region: Placement) -> str:
    """RAM region every ExecuTorch runtime buffer follows unless overridden.

    The adapter rejects non-RAM model.arena_location values for ExecuTorch,
    so anything else here is a planner artifact; clamp it to tcm.
    """
    return arena_region.value if arena_region.value in ("tcm", "sram") else "tcm"


def _resolve_pmu_passes(config: Any, soc: Any | None = None) -> list[PmuPassContext]:
    profiling = config.profiling
    if soc is not None:
        supported_groups = supported_groups_for_domains(soc.profiling_domains)
        try:
            validate_group_selection(profiling.pmu_counters, supported_groups=supported_groups)
        except ValueError as exc:
            raise FirmwareError(
                str(exc),
                hint=(
                    f"Target '{soc.name}' supports PMU groups: "
                    f"{', '.join(supported_groups) if supported_groups else 'none'}."
                ),
            ) from exc

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
