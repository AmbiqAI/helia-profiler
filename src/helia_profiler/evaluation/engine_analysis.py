"""Engine-dispatched model analysis — hpx orchestration, not model cost.

``analyze_for_engine`` picks the analysis matching the configured engine
(running a real heliaAOT conversion when asked) and returns the
:class:`~helia_profiler.modelcost.ModelAnalysis` the pure core computes.
It lives here rather than in :mod:`helia_profiler.modelcost` because engine
dispatch and AOT compilation are exactly the hpx-specific edges the
contained core must not carry (#229 D4).
"""

from __future__ import annotations

from pathlib import Path

from ..modelcost.model_analysis import (
    ModelAnalysis,
    analyze_air_model,
    analyze_model,
    is_aot_available,
    is_available,
)


def analyze_for_engine(
    model_path: str | Path,
    *,
    engine: str = "tflite",
    board: str = "apollo510_evb",
) -> ModelAnalysis:
    """Analyze a model as the selected inference engine executes it."""
    from ..engines import EngineType
    from ..errors import ConfigError, EngineError

    path = Path(model_path)
    if not path.is_file():
        raise ConfigError(f"Model file not found: {path}")
    if not is_available():
        raise ConfigError(
            "ai-edge-litert is not installed.",
            hint="Install with: pip install 'helia-profiler[analysis]'",
        )

    engine_type = EngineType(engine)
    if engine_type is not EngineType.HELIA_AOT:
        result = analyze_model(path)
        if result is None:
            raise EngineError(f"Failed to analyze model: {path}")
        return result

    if not is_aot_available():
        raise ConfigError(
            "helia-aot is not installed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        )

    import tempfile

    try:
        from helia_aot.cli.defines import ConvertArgs
        from helia_aot.converter import AotConverter
        from helia_aot.defines import ModuleType
    except ImportError as exc:
        raise ConfigError(
            "helia-aot import failed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        ) from exc

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        convert_args = ConvertArgs(
            model={"path": str(path)},
            module={"path": tmp, "type": ModuleType.nsx.value},
            platform={"name": board},
        )
        try:
            context = AotConverter(config=convert_args).convert()
        except Exception as exc:
            raise EngineError(f"AOT compilation failed: {exc}") from exc

        result = analyze_air_model(context.model)
        if result is None:
            raise EngineError(f"Failed to analyze AOT-transformed model: {path}")
        return result


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


