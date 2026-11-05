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
    analyze_aot_operators,
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
    from ..engines.helia_aot.torch_program import aot_converter, is_torch_program
    from ..errors import ConfigError, EngineError

    path = Path(model_path)
    if not path.is_file():
        raise ConfigError(f"Model file not found: {path}")
    engine_type = EngineType(engine)
    torch_program = is_torch_program(path)
    if torch_program and engine_type is not EngineType.HELIA_AOT:
        raise ConfigError(
            f"A PyTorch program ({path.name}) can only be analyzed with the helia-aot engine.",
            hint="Pass --engine helia-aot.",
        )
    if not torch_program and not is_available():
        raise ConfigError(
            "ai-edge-litert is not installed.",
            hint="Install with: pip install 'helia-profiler[analysis]'",
        )

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

    from ..engines.helia_aot.compile import aot_platform_for_board, convert_with_views

    try:
        from helia_aot import ConvertArgs
        from helia_aot.defines import ModuleType
    except ImportError as exc:
        raise ConfigError(
            "helia-aot import failed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        ) from exc

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        convert_args = ConvertArgs(
            model={"path": str(path)},
            module={"path": tmp, "type": ModuleType("nsx").value},
            platform={"name": aot_platform_for_board(board) or board},
            result_views=True,
        )
        try:
            result = convert_with_views(aot_converter(path, convert_args))
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"AOT compilation failed: {exc}") from exc

        source = None if torch_program else analyze_model(path)
        return analyze_aot_operators(result.operators, result.tensors, source)
