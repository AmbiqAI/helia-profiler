"""Declarative validation matrix.

Defines the models, boards, and engines participating in the
``hpx validate`` hardware validation suite, plus the expansion logic
that converts user filters into concrete :class:`CaseSpec` instances.

Designed so adding a board is one entry in ``BOARDS`` and adding a
benchmark is one entry in ``MODELS`` — nothing else needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..engines import EngineType
from ..platform import get_soc_for_board

# ---------------------------------------------------------------------------
# Registry types
# ---------------------------------------------------------------------------


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
    description: str = ""


@dataclass(frozen=True)
class CaseSpec:
    """A single validation case — one run end-to-end."""

    model: ModelSpec
    engine: EngineType
    power: bool  # Joulescope capture enabled
    board: BoardSpec
    attempt: int = 1
    repeat_total: int = 1

    @property
    def case_id(self) -> str:
        """Stable slug — used in report tables and output subfolders."""
        suffix = "-power" if self.power else ""
        base = f"{self.board.id}-{self.model.id}-{self.engine.short_slug}{suffix}"
        if self.repeat_total > 1:
            return f"{base}-run{self.attempt:02d}"
        return base


def _board_spec(board_id: str, display_name: str, description: str = "") -> BoardSpec:
    """Build a validation-board entry from the platform registry."""
    soc = get_soc_for_board(board_id)
    return BoardSpec(
        id=board_id,
        display_name=display_name,
        jlink_device=soc.jlink_device,
        has_psram=soc.memory.psram_kb > 0,
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
        arena_size=131072,
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
    "apollo510_evb": _board_spec(
        "apollo510_evb",
        "Apollo510 EVB",
        description="Ambiq Apollo510 evaluation board (Cortex-M55)",
    ),
    # Future boards (apollo4p_evb, apollo3p_evb, ...) plug in here.
}


# ---------------------------------------------------------------------------
# Matrix expansion
# ---------------------------------------------------------------------------


def build_matrix(
    models: list[str] | None = None,
    engines: list[str | EngineType] | None = None,
    power: str = "both",
    boards: list[str] | None = None,
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
        for model_id in model_ids:
            model = MODELS[model_id]
            for engine in engine_ids:
                for p in power_flags:
                    for attempt in range(1, repeat + 1):
                        cases.append(
                            CaseSpec(
                                model=model,
                                engine=engine,
                                power=p,
                                board=board,
                                attempt=attempt,
                                repeat_total=repeat,
                            )
                        )
    return cases
