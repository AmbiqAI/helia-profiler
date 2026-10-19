"""Board selection agrees across AOT compile and analysis consumers."""

from __future__ import annotations

import logging
import sys
from enum import StrEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.helia_aot.compile import _resolve_aot_platform
from helia_profiler.errors import EngineError
from helia_profiler.evaluation import engine_analysis
from helia_profiler.modelcost import ModelAnalysis
from helia_profiler.platform import list_boards
from tests.aot_views import Result

_TARGETS = (
    ("apollo3p_evb", "apollo3p_evb"),
    ("apollo3p_evb_cygnus", None),
    ("apollo4p_evb", "apollo4p_evb"),
    ("apollo4l_evb", "apollo4l_evb"),
    ("apollo4l_blue_evb", "apollo4l_blue_evb"),
    ("apollo4p_blue_kbr_evb", "apollo4p_blue_kbr_evb"),
    ("apollo4p_blue_kxr_evb", "apollo4p_blue_kxr_evb"),
    ("apollo510_evb", "apollo510_evb"),
    ("apollo510b_evb", "apollo510b_evb"),
    ("apollo5b_evb", "apollo510_evb"),
    ("apollo330mP_evb", "apollo330mp_evb"),
    ("apollo510dL_evb", "apollo510l_evb"),
    ("atomiq110_fpga_turbo", "atomiq110"),
)


def _config(board: str, override: str | None = None):
    target: dict[str, object] = {"board": board}
    if board == "unknown_463_board":
        target["custom_boards"] = {board: {"soc": "apollo510", "channel": "dev"}}
    return load_config(
        None,
        {
            "model": {"path": "m.tflite"},
            "target": target,
            "engine": {
                "type": "helia-aot",
                "config": {"platform_name": override} if override is not None else {},
            },
        },
    )


@pytest.fixture
def analysis_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Replace the external compiler boundary, retaining the HPX consumer."""
    compiler = Mock()
    for name, attributes in {
        "helia_aot": {"AotConverter": compiler, "ConvertArgs": SimpleNamespace},
        "helia_aot.defines": {"ModuleType": StrEnum("ModuleType", {"NSX": "nsx"})},
    }.items():
        module = ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
    compiler.return_value.convert.return_value = Result()
    result = ModelAnalysis(layers=[], total_macs=0, total_ops=0, num_parameters=0)
    monkeypatch.setattr(engine_analysis, "is_available", lambda: True)
    monkeypatch.setattr(engine_analysis, "is_aot_available", lambda: True)
    monkeypatch.setattr(engine_analysis, "analyze_model", lambda _path: None)
    monkeypatch.setattr(engine_analysis, "analyze_aot_operators", lambda *_args: result)
    model = tmp_path / "model.tflite"
    model.write_bytes(b"compiler-boundary input")
    return model, compiler, result


def test_table_covers_builtin_boards():
    assert {board for board, _target in _TARGETS} == {board.name for board in list_boards()}


@pytest.mark.parametrize(("board", "target"), _TARGETS)
def test_compile_and_analysis_targets(board, target, analysis_boundary, caplog):
    model, compiler, result = analysis_boundary
    with caplog.at_level(logging.WARNING, logger="hpx"):
        if target is None:
            with pytest.raises(EngineError, match="No heliaAOT platform mapping"):
                _resolve_aot_platform(_config(board))
            compiler.return_value.convert.side_effect = ValueError("Unknown platform")
            with pytest.raises(EngineError, match="Unknown platform"):
                engine_analysis.analyze_for_engine(model, engine="helia-aot", board=board)
        else:
            assert _resolve_aot_platform(_config(board)) == target
            assert (
                engine_analysis.analyze_for_engine(model, engine="helia-aot", board=board) is result
            )
    config = compiler.call_args.kwargs["config"]
    assert config.platform == {"name": target or board}
    assert config.model == {"path": str(model)}
    assert config.module["type"] == "nsx"
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    if board in {"apollo5b_evb", "atomiq110_fpga_turbo"}:
        assert len(warnings) == 2
        assert all("Memory sizes and capabilities may differ" in w.message for w in warnings)
    else:
        assert not warnings


@pytest.mark.parametrize("override", ["apollo510b_evb", "CUSTOM_463"])
@pytest.mark.parametrize("board", ["apollo330mP_evb", "unknown_463_board"])
def test_explicit_compile_override_takes_precedence(board, override, caplog):
    assert _resolve_aot_platform(_config(board, override)) == override
    assert not caplog.records


def test_unknown_compile_board_refuses():
    with pytest.raises(EngineError, match="No heliaAOT platform mapping"):
        _resolve_aot_platform(_config("unknown_463_board"))


@pytest.mark.parametrize("board", ["apollo510l_evb", "APOLLO510B_EVB", "atomiq110", "CUSTOM_463"])
def test_native_analysis_names_pass_to_aot(board, analysis_boundary):
    model, compiler, result = analysis_boundary
    assert engine_analysis.analyze_for_engine(model, engine="helia-aot", board=board) is result
    assert compiler.call_args.kwargs["config"].platform == {"name": board}
    assert compiler.call_args.kwargs["config"].result_views is True


def test_unknown_analysis_board_preserves_aot_refusal(analysis_boundary):
    model, compiler, _result = analysis_boundary
    compiler.return_value.convert.side_effect = ValueError("Unknown platform")
    with pytest.raises(EngineError, match="Unknown platform"):
        engine_analysis.analyze_for_engine(model, engine="helia-aot", board="unknown_463_board")


def test_convert_with_views_returns_the_result():
    from helia_profiler.engines.helia_aot.compile import convert_with_views

    result = Result()
    assert convert_with_views(SimpleNamespace(convert=lambda: result)) is result


def _converter(result: object) -> SimpleNamespace:
    return SimpleNamespace(config=SimpleNamespace(), convert=lambda: result)


def test_a_conversion_result_without_views_is_refused():
    from helia_profiler.engines.helia_aot.compile import convert_with_views

    public_only = SimpleNamespace(output=None, prefix="aot", plan=None, report=None, work_dir=None)
    with pytest.raises(
        EngineError, match="missing operators, tensors, regions, placements"
    ) as refused:
        convert_with_views(_converter(public_only))
    assert refused.value.hint and "hpx runtimes list" in refused.value.hint


def test_a_result_with_an_unset_view_is_refused():
    from helia_profiler.engines.helia_aot.compile import convert_with_views

    partial = SimpleNamespace(operators=(), tensors=(), regions=None, placements={})
    with pytest.raises(EngineError, match=r"\(missing regions\)"):
        convert_with_views(_converter(partial))


def test_callers_keep_the_refusal_and_its_hint(analysis_boundary):
    model, compiler, _ = analysis_boundary
    compiler.return_value.convert.return_value = SimpleNamespace(output=None, plan=None)
    with pytest.raises(EngineError, match="^heliaAOT returned no conversion views") as refused:
        engine_analysis.analyze_for_engine(model, engine="helia-aot", board="apollo510_evb")
    assert refused.value.hint
