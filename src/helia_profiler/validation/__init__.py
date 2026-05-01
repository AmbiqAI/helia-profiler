"""Hardware-in-the-loop validation harness for heliaPROFILER.

This subpackage defines a declarative matrix of canonical test cases
(MLPerf Tiny models × engines × power) that the ``hpx validate`` CLI
executes against real hardware.  It is designed so that adding boards
or new benchmark categories later is additive, not structural.

Public surface:
    - ``MODELS``       — registry of canonical test models
    - ``BOARDS``       — registry of supported boards
    - ``CaseSpec``     — a single (model, engine, power, board) case
    - ``build_matrix`` — expand filters into the list of cases to run
"""

from __future__ import annotations

from .matrix import (
    BOARDS,
    ENGINES,
    MODELS,
    BoardSpec,
    CaseSpec,
    ModelSpec,
    build_matrix,
)

__all__ = [
    "BOARDS",
    "ENGINES",
    "MODELS",
    "BoardSpec",
    "CaseSpec",
    "ModelSpec",
    "build_matrix",
]
