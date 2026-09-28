"""Terminal status vocabulary shared by the fixed-fixture firmware render and capture."""

from __future__ import annotations

from enum import IntEnum


class FixtureStage(IntEnum):
    """Terminal ``deployment_status`` codes written by the fixed-fixture firmware."""

    SRAM_CONFIG = -2
    RESOLVER = -3
    MODEL_SCHEMA = -4
    ARENA = -5
    IO_CONTRACT = -6
    INVOKE = -7
    STIMER = -8
    TIMING_BOUND = -9

    @property
    def description(self) -> str:
        return _STAGE_DESCRIPTIONS[self]


_STAGE_DESCRIPTIONS = {
    FixtureStage.SRAM_CONFIG: "SRAM power configuration",
    FixtureStage.RESOLVER: "operator resolver registration",
    FixtureStage.MODEL_SCHEMA: "model schema check",
    FixtureStage.ARENA: "arena allocation or model init",
    FixtureStage.IO_CONTRACT: "input/output tensor contract",
    FixtureStage.INVOKE: "inference invoke",
    FixtureStage.STIMER: "STIMER start",
    FixtureStage.TIMING_BOUND: "timing bound",
}
