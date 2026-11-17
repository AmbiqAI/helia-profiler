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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import ProfileConfig

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
    config: ProfileConfig | None = None,
) -> ModelAnalysis:
    """Analyze the selected engine; optional resolved config retains its full target intent."""
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
    if config is not None and (
        config.engine.type is not engine_type
        or config.target.board != board
        or config.model.path.resolve() != path.resolve()
    ):
        raise ConfigError("Analysis arguments differ from the supplied resolved configuration")
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

    from ..engines.helia_aot.compile import (
        _compiler_target,
        _merged_aot_args,
        aot_platform_for_board,
        convert_with_views,
        effective_aot_args,
    )
    from ..engines.helia_aot.target import resolve_target, verify_conversion
    from ..platform import get_board

    if config is None:
        try:
            get_board(board)
        except ValueError:
            pass
        else:
            from ..config import load_config

            config = load_config(
                None,
                {
                    "model": {"path": path},
                    "target": {"board": board},
                    "engine": {"type": "helia-aot"},
                },
            )
    target = _compiler_target(config, _merged_aot_args(config)) if config is not None else None
    args = effective_aot_args(config, target=target) if config is not None else {}

    try:
        from helia_aot import AotConverter, ConvertArgs
        from helia_aot.defines import ModuleType
    except ImportError as exc:
        raise ConfigError(
            "helia-aot import failed.",
            hint="Install with: pip install 'helia-profiler[aot]'",
        ) from exc

    with tempfile.TemporaryDirectory(prefix="hpx_aot_") as tmp:
        values: dict[str, Any] = {
            **args,
            "model": {**args.get("model", {}), "path": str(path)},
            "module": {**args.get("module", {}), "path": tmp, "type": ModuleType("nsx").value},
            "platform": target.options()
            if target is not None
            else {"name": aot_platform_for_board(board) or board},
            "result_views": True,
        }
        convert_args = ConvertArgs(**values)
        target = resolve_target(target, convert_args.platform) if target is not None else None
        try:
            result = convert_with_views(AotConverter(config=convert_args))
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"AOT compilation failed: {exc}") from exc

        if target is not None:
            verify_conversion(target, result)

        return analyze_aot_operators(result.operators, result.tensors, analyze_model(path))
