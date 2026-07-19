"""Declarative validation matrix.

Defines the models, boards, and engines participating in the
``hpx validate`` hardware validation suite, plus the expansion logic
that converts user filters into concrete :class:`CaseSpec` instances.

Designed so adding a board is one entry in ``BOARDS`` and adding a
benchmark is one entry in ``MODELS`` — nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..config import Toolchain, Transport
from ..engines import EngineType
from ..platform import SocFamily, get_soc_for_board

# ---------------------------------------------------------------------------
# Registry types
# ---------------------------------------------------------------------------


class MemoryProfile(StrEnum):
    """Coarse placement profiles exercised by the hardware validation matrix."""

    AUTO = "auto"
    TCM = "tcm"
    SRAM = "sram"
    MRAM = "mram"
    PSRAM = "psram"


@dataclass(frozen=True)
class ModelSpec:
    """One canonical benchmark model."""

    id: str  # short stable ID (used on CLI + in reports)
    name: str  # human-readable name
    category: str  # MLPerf Tiny category (kws / vww / ic / ad)
    fixture_path: str  # path relative to helia-profiler root
    arena_size: int  # tensor arena in bytes (RT / TFLM)
    description: str = ""


@dataclass(frozen=True)
class BoardSpec:
    """One target board supported by the validation suite."""

    id: str  # CLI-facing ID (e.g. apollo510_evb)
    display_name: str  # human-readable name
    jlink_device: str  # device name for J-Link / probes
    has_psram: bool = False
    transports: tuple[Transport, ...] = (Transport.RTT, Transport.SWO, Transport.UART)
    toolchains: tuple[Toolchain, ...] = (
        Toolchain.ARM_NONE_EABI_GCC,
        Toolchain.ARMCLANG,
        Toolchain.ATFE,
    )
    memories: tuple[MemoryProfile, ...] = (
        MemoryProfile.AUTO,
        MemoryProfile.TCM,
        MemoryProfile.SRAM,
        MemoryProfile.MRAM,
    )
    description: str = ""


@dataclass(frozen=True)
class CaseSpec:
    """A single validation case — one run end-to-end."""

    model: ModelSpec
    engine: EngineType
    power: bool  # Joulescope capture enabled
    board: BoardSpec
    toolchain: Toolchain = Toolchain.ARM_NONE_EABI_GCC
    transport: Transport = Transport.RTT
    memory: MemoryProfile = MemoryProfile.AUTO
    jlink_serial: str | None = None
    power_serial: str | None = None
    power_gpio_pins: tuple[int, int, int] | None = None
    attempt: int = 1
    repeat_total: int = 1

    @property
    def case_id(self) -> str:
        """Stable slug — used in report tables and output subfolders."""
        suffix = "-power" if self.power else ""
        base = (
            f"{self.board.id}-{self.model.id}-{self.engine.short_slug}-"
            f"{self.toolchain.value}-{self.transport.value}-{self.memory.value}{suffix}"
        )
        if self.repeat_total > 1:
            return f"{base}-run{self.attempt:02d}"
        return base


def case_validity(case: CaseSpec) -> str | None:
    """Return a skip reason if the case is a known-unsupported combination."""
    if case.memory is MemoryProfile.PSRAM and case.transport is not Transport.RTT:
        return "psram weights require the rtt transport"
    if case.transport is Transport.USB_CDC and case.transport not in case.board.transports:
        return "usb_cdc not supported on this board"
    soc = get_soc_for_board(case.board.id)
    # Statically infeasible TCM profile: arena and weights both use DTCM,
    # AND weights into DTCM, which cannot fit when their combined size
    # exceeds it (e.g. KWS's 32 KB arena + ~53 KB weights vs Apollo3's
    # 64 KB DTCM). Weights are approximated by the fixture file size; if the
    # fixture is missing (LFS not pulled) the guard stays silent — the
    # harness already skips missing fixtures with its own reason.
    if case.memory is MemoryProfile.TCM:
        fixture = Path(case.model.fixture_path)
        if not fixture.is_absolute():
            # matrix.py lives at src/helia_profiler/validation/ — repo root is
            # three levels up from the package dir.
            fixture = Path(__file__).resolve().parents[3] / case.model.fixture_path
        weights = fixture.stat().st_size if fixture.exists() else 0
        needed = case.model.arena_size + weights
        if weights and needed > soc.memory.dtcm_kb * 1024:
            return (
                f"arena+weights (~{needed // 1024} KB) cannot fit "
                f"{soc.memory.dtcm_kb} KB DTCM"
            )
    # Apollo3 EVBs: the power-sync GPIOs (24/25/26, moved off the J-Link VCOM
    # UART pads) sit on the MSPI0 pads that external PSRAM needs, so PSRAM
    # placement and gated power capture are electrically exclusive there.
    if case.power and case.memory is MemoryProfile.PSRAM and soc.family is SocFamily.AP3:
        return "apollo3 power-sync GPIOs share the MSPI0 (PSRAM) pads"
    return None


def _board_spec(board_id: str, display_name: str, description: str = "") -> BoardSpec:
    """Build a validation-board entry from the platform registry."""
    soc = get_soc_for_board(board_id)
    transports = [Transport.RTT, Transport.SWO, Transport.UART]
    if soc.has_usb:
        transports.append(Transport.USB_CDC)
    memories = [MemoryProfile.AUTO, MemoryProfile.TCM, MemoryProfile.SRAM, MemoryProfile.MRAM]
    if soc.memory.psram_kb > 0:
        memories.append(MemoryProfile.PSRAM)
    return BoardSpec(
        id=board_id,
        display_name=display_name,
        jlink_device=soc.jlink_device,
        has_psram=soc.memory.psram_kb > 0,
        transports=tuple(transports),
        memories=tuple(memories),
        description=description,
    )


# ---------------------------------------------------------------------------
# Registries — the single source of truth for what's validated
# ---------------------------------------------------------------------------

#: Supported inference engines for validation.
ENGINES: tuple[EngineType, ...] = (EngineType.HELIA_RT, EngineType.HELIA_AOT)


#: Canonical MLPerf Tiny models shipped as LFS fixtures.
MODELS: dict[str, ModelSpec] = {
    "kws": ModelSpec(
        id="kws",
        name="Keyword Spotting (DS-CNN)",
        category="kws",
        fixture_path="tests/fixtures/mlperf_tiny/kws/kws_ref_model.tflite",
        # Hardware-measured heliaRT usage is ~23.2 KB (gcc/armclang/atfe,
        # 2026-07-04); 32 KB gives headroom while keeping TCM/SRAM placement
        # presets viable on small boards (AP3 DTCM is only 64 KB).
        arena_size=32768,
        description="MLPerf Tiny keyword spotting — DS-CNN int8",
    ),
    "vww": ModelSpec(
        id="vww",
        name="Visual Wake Words (MobileNetV1)",
        category="vww",
        fixture_path="tests/fixtures/mlperf_tiny/vww/vww_96_int8.tflite",
        arena_size=524288,
        description="MLPerf Tiny visual wake words — MobileNetV1 96x96 int8",
    ),
    "ic": ModelSpec(
        id="ic",
        name="Image Classification (ResNet CIFAR-10)",
        category="ic",
        fixture_path="tests/fixtures/mlperf_tiny/ic/ic_resnet_int8.tflite",
        arena_size=262144,
        description="MLPerf Tiny image classification — ResNet int8",
    ),
    "ad": ModelSpec(
        id="ad",
        name="Anomaly Detection (DeepAutoEncoder)",
        category="ad",
        fixture_path="tests/fixtures/mlperf_tiny/ad/ad01_int8.tflite",
        arena_size=131072,
        description="MLPerf Tiny anomaly detection — DeepAutoEncoder ToyADMX int8",
    ),
}


#: Boards supported by the validation harness.
BOARDS: dict[str, BoardSpec] = {
    "apollo3p_evb": _board_spec(
        "apollo3p_evb",
        "Apollo3 Blue Plus EVB",
        description="Ambiq Apollo3 Blue Plus evaluation board (Cortex-M4F)",
    ),
    "apollo4p_blue_kxr_evb": _board_spec(
        "apollo4p_blue_kxr_evb",
        "Apollo4 Blue Plus KXR EVB",
        description="Ambiq Apollo4 Blue Plus KXR evaluation board (Cortex-M4F)",
    ),
    "apollo510_evb": _board_spec(
        "apollo510_evb",
        "Apollo510 EVB",
        description="Ambiq Apollo510 evaluation board (Cortex-M55)",
    ),
    "apollo330mP_evb": _board_spec(
        "apollo330mP_evb",
        "Apollo330mP EVB",
        description="Ambiq Apollo330mP evaluation board (Cortex-M55)",
    ),
    # Future boards plug in here.
}


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------


def build_matrix(
    models: list[str] | None = None,
    engines: list[str | EngineType] | None = None,
    power: str = "off",
    boards: list[str] | None = None,
    toolchains: list[str | Toolchain] | None = None,
    transports: list[str | Transport] | None = None,
    memories: list[str | MemoryProfile] | None = None,
    jlink_serials: dict[str, str] | None = None,
    power_serials: dict[str, str] | None = None,
    power_gpio_pins: dict[str, tuple[int, int, int]] | None = None,
    repeat: int = 1,
) -> list[CaseSpec]:
    """Expand user filters into a concrete list of :class:`CaseSpec`.

    Parameters
    ----------
    models:
        Model IDs to include (default: all in :data:`MODELS`).
    engines:
        Engine identifiers to include (string slug or :class:`EngineType`;
        default: all in :data:`ENGINES`).
    power:
        One of ``"both"``, ``"on"``, ``"off"``.  ``"both"`` runs each
        (model, engine) case twice — with and without Joulescope.
    boards:
        Board IDs (default: all in :data:`BOARDS`).
    toolchains:
        Toolchain identifiers to include (default: each board's validation toolchains).
    transports:
        Transport identifiers to include (default: each board's supported transports).
    memories:
        Model placement presets to include (default: each board's supported placements).
    jlink_serials:
        Optional mapping of board ID to J-Link serial number for multi-board labs.
    power_serials:
        Optional mapping of board ID to Joulescope serial number for powered
        cases. This permits several instruments to remain connected.
    power_gpio_pins:
        Optional mapping of board ID to ``(gate, state, go)`` GPIO pins for
        powered cases on boards without registered power-sync wiring.

    Returns
    -------
    list[CaseSpec]
        Ordered deterministically — by board → model category → engine → power → attempt.

    Raises
    ------
    ValueError
        If any filter value is not a known registry key.
    """
    model_ids = models or list(MODELS.keys())
    board_ids = boards or list(BOARDS.keys())

    if engines is None:
        engine_ids: list[EngineType] = list(ENGINES)
    else:
        engine_ids = []
        unknown: list[str] = []
        for e in engines:
            if isinstance(e, EngineType):
                engine_ids.append(e)
                continue
            try:
                engine_ids.append(EngineType(e))
            except ValueError:
                unknown.append(str(e))
        if unknown:
            raise ValueError(f"Unknown engine(s): {unknown}. Known: {[e.value for e in ENGINES]}")
        # Reject engines outside the validation matrix (e.g. TFLM).
        out_of_matrix = [e.value for e in engine_ids if e not in ENGINES]
        if out_of_matrix:
            raise ValueError(
                f"Engine(s) not in validation matrix: {out_of_matrix}. "
                f"Known: {[e.value for e in ENGINES]}"
            )

    unknown_m = [m for m in model_ids if m not in MODELS]
    if unknown_m:
        raise ValueError(f"Unknown model(s): {unknown_m}. Known: {list(MODELS)}")
    unknown_b = [b for b in board_ids if b not in BOARDS]
    if unknown_b:
        raise ValueError(f"Unknown board(s): {unknown_b}. Known: {list(BOARDS)}")
    if power not in ("both", "on", "off"):
        raise ValueError(f"power must be 'both'|'on'|'off', got {power!r}")
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat!r}")

    toolchain_filter = _coerce_filter(
        toolchains,
        enum_type=Toolchain,
        known=tuple(Toolchain),
        label="toolchain",
    )
    transport_filter = _coerce_filter(
        transports,
        enum_type=Transport,
        known=tuple(Transport),
        label="transport",
    )
    memory_filter = _coerce_filter(
        memories,
        enum_type=MemoryProfile,
        known=tuple(MemoryProfile),
        label="memory",
    )

    power_flags: list[bool]
    if power == "both":
        power_flags = [False, True]
    elif power == "on":
        power_flags = [True]
    else:
        power_flags = [False]

    cases: list[CaseSpec] = []
    for board_id in board_ids:
        board = BOARDS[board_id]
        board_toolchains = _intersect_or_board_default(toolchain_filter, board.toolchains)
        board_transports = _intersect_or_board_default(transport_filter, board.transports)
        board_memories = _intersect_or_board_default(memory_filter, board.memories)
        for model_id in model_ids:
            model = MODELS[model_id]
            for engine in engine_ids:
                for toolchain in board_toolchains:
                    for transport in board_transports:
                        for memory in board_memories:
                            for p in power_flags:
                                for attempt in range(1, repeat + 1):
                                    cases.append(
                                        CaseSpec(
                                            model=model,
                                            engine=engine,
                                            power=p,
                                            board=board,
                                            toolchain=toolchain,
                                            transport=transport,
                                            memory=memory,
                                            jlink_serial=(jlink_serials or {}).get(board_id),
                                            power_serial=(power_serials or {}).get(board_id) if p else None,
                                            power_gpio_pins=(power_gpio_pins or {}).get(board_id) if p else None,
                                            attempt=attempt,
                                            repeat_total=repeat,
                                        )
                                    )

        if toolchain_filter is not None and not board_toolchains:
            raise ValueError(
                f"No requested toolchains are valid for board {board_id}. "
                f"Known for board: {[t.value for t in board.toolchains]}"
            )
        if transport_filter is not None and not board_transports:
            raise ValueError(
                f"No requested transports are valid for board {board_id}. "
                f"Known for board: {[t.value for t in board.transports]}"
            )
        if memory_filter is not None and not board_memories:
            raise ValueError(
                f"No requested memories are valid for board {board_id}. "
                f"Known for board: {[m.value for m in board.memories]}"
                            )
    return cases


def _coerce_filter(raw, *, enum_type, known: tuple, label: str):
    if raw is None:
        return None
    values = []
    unknown = []
    for value in raw:
        if isinstance(value, enum_type):
            if enum_type is Toolchain and value is Toolchain.GCC:
                value = Toolchain.ARM_NONE_EABI_GCC
            values.append(value)
            continue
        try:
            coerced = enum_type(value)
            if enum_type is Toolchain and coerced is Toolchain.GCC:
                coerced = Toolchain.ARM_NONE_EABI_GCC
            values.append(coerced)
        except ValueError:
            unknown.append(str(value))
    if unknown:
        raise ValueError(
            f"Unknown {label}(s): {unknown}. Known: {[value.value for value in known]}"
        )
    return tuple(values)


def _intersect_or_board_default(requested, supported: tuple):
    if requested is None:
        return supported
    return tuple(value for value in requested if value in supported)
