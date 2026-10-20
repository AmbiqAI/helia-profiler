"""What a run is built for: a model's typed inputs and outputs, and what HPX can build.

:func:`analyze_run_model` reads the public inputs and outputs of a LiteRT
model, in model order, so a caller can pin one input file per tensor in a
:class:`~helia_profiler.TypedFixture`. :func:`capabilities` reports the
boards, clock, engines and IO element types :func:`~helia_profiler.build`
and :func:`~helia_profiler.run_build` support.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .errors import ConfigError

if TYPE_CHECKING:
    from ._fixture_facts import FixtureCapabilities
    from .engines import EngineType
    from .fixture_analysis import TypedFixtureModelAnalysis


def capabilities() -> FixtureCapabilities:
    """The targets, engines and IO element types a run can be built and captured with.

    It reports what this HPX implements, not whether a runtime release is
    qualified on a board; :func:`~helia_profiler.qualification` answers that.
    """
    from ._fixture_facts import fixture_capabilities

    return fixture_capabilities()


def analyze_run_model(path: str | Path) -> TypedFixtureModelAnalysis:
    """Read a LiteRT model's public inputs and outputs without selecting an engine.

    Args:
        path: The ``.tflite`` model.

    Returns:
        The input and output tensors in model order, with their dtype, shape
        and quantization, and the model's graph facts.

    Raises:
        ConfigError: A run cannot pin inputs for this model, for example
            because an input or output shape is dynamic.
    """
    from .fixture_analysis import analyze_typed_fixture_model

    try:
        return analyze_typed_fixture_model(Path(path))
    except ValueError as exc:
        raise ConfigError(f"Cannot build a run of {path}: {exc}") from exc


def analyze_run_model_for_engine(
    path: str | Path, engine: EngineType | str
) -> TypedFixtureModelAnalysis:
    """Read a model's public inputs and outputs as *engine*'s fixture integration admits them.

    Args:
        path: The ``.tflite`` model.
        engine: The engine the run builds with.

    Returns:
        The same analysis as :func:`analyze_run_model`, after the engine has
        admitted the model.

    Raises:
        ConfigError: *engine* is not an engine name, or the engine does not
            admit the model.
    """
    from .engines import EngineType
    from .fixture_analysis import analyze_typed_fixture_model_for_engine

    try:
        selected = EngineType(engine)
    except ValueError as exc:
        names = ", ".join(member.value for member in EngineType)
        raise ConfigError(f"Unknown engine {engine!r}", hint=f"Use one of {names}.") from exc
    try:
        return analyze_typed_fixture_model_for_engine(Path(path), engine=selected)
    except ValueError as exc:
        raise ConfigError(f"{selected.value} cannot build a run of {path}: {exc}") from exc
