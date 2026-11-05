"""The helia-aot engine's PyTorch (.pt2) input, converted by helia-torch.

helia-torch (torch, ExecuTorch) is not an hpx dependency, so these tests stand
in a fake ``nsx_cortex_m.helia_aot`` and ``torch.export.load``; the real
conversion is covered by helia-torch's own tests and an on-board run.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler.config import load_config
from helia_profiler.engines.helia_aot.torch_program import (
    TORCH_PROGRAM_MAGIC,
    TorchProgramConverter,
    aot_converter,
    is_torch_program,
)
from helia_profiler.errors import ConfigError, EngineError
from helia_profiler.pipeline import PipelineContext
from helia_profiler.stages.preflight import PreflightStage

pytestmark = pytest.mark.usefixtures("installed_python_dependencies")


class _NotFullyDelegableError(Exception):
    pass


class _FakeHeliaTorch:
    """Records what the converter asks helia-torch for."""

    def __init__(self, refuse: str | None = None, float_model: bool = False):
        self.refuse = refuse
        self.float_model = float_model
        self.calls: list[tuple] = []

    def install(self, monkeypatch) -> None:
        helia_aot = types.ModuleType("nsx_cortex_m.helia_aot")
        helia_aot.NotFullyDelegableError = _NotFullyDelegableError

        def program_to_air(program, *, name):
            self.calls.append(("program_to_air", program, name))
            if self.refuse:
                raise _NotFullyDelegableError(self.refuse)
            if self.float_model:
                raise ValueError("example_inputs are needed to quantize a float model")
            return types.SimpleNamespace(air=f"air:{name}")

        def convert_air(air, convert_args):
            self.calls.append(("convert_air", air, convert_args))
            return "conversion-result"

        helia_aot.program_to_air = program_to_air
        helia_aot.convert_air = convert_air
        package = types.ModuleType("nsx_cortex_m")
        package.helia_aot = helia_aot
        monkeypatch.setitem(sys.modules, "nsx_cortex_m", package)
        monkeypatch.setitem(sys.modules, "nsx_cortex_m.helia_aot", helia_aot)

        torch = types.ModuleType("torch")
        torch.export = types.SimpleNamespace(load=lambda path: ("program", Path(path).name))
        monkeypatch.setitem(sys.modules, "torch", torch)


def _convert_args(name: str | None = None):
    return types.SimpleNamespace(model=types.SimpleNamespace(name=name))


def test_is_torch_program():
    assert is_torch_program("models/kws.pt2") and is_torch_program(Path("KWS.PT2"))
    assert not is_torch_program("kws.tflite") and not is_torch_program("kws.pte")


def test_aot_converter_picks_helia_torch_for_pt2(tmp_path):
    converter = aot_converter(tmp_path / "kws.pt2", _convert_args())
    assert isinstance(converter, TorchProgramConverter)


def test_aot_converter_keeps_litert_for_tflite(tmp_path):
    pytest.importorskip("helia_aot")
    args = _convert_args()
    with patch("helia_aot.AotConverter") as fake:
        converter = aot_converter(tmp_path / "kws.tflite", args)
    fake.assert_called_once_with(config=args)
    assert converter is fake.return_value


def test_converts_through_helia_torch(tmp_path, monkeypatch):
    fake = _FakeHeliaTorch()
    fake.install(monkeypatch)
    args = _convert_args()
    result = TorchProgramConverter(tmp_path / "kws.pt2", args).convert()
    assert result == "conversion-result"
    assert fake.calls == [
        ("program_to_air", ("program", "kws.pt2"), "kws"),
        ("convert_air", "air:kws", args),
    ]


def test_module_name_comes_from_the_convert_args(tmp_path, monkeypatch):
    fake = _FakeHeliaTorch()
    fake.install(monkeypatch)
    TorchProgramConverter(tmp_path / "kws.pt2", _convert_args("hpx_model")).convert()
    assert fake.calls[0][2] == "hpx_model"


def test_partially_supported_model_is_an_engine_error(tmp_path, monkeypatch):
    _FakeHeliaTorch(
        refuse="1 operator(s) cannot be compiled by heliaAOT: aten_sigmoid_default"
    ).install(monkeypatch)
    with pytest.raises(EngineError, match="aten_sigmoid_default") as info:
        TorchProgramConverter(tmp_path / "kws.pt2", _convert_args()).convert()
    assert "executorch" in info.value.hint


def test_float_program_is_an_engine_error(tmp_path, monkeypatch):
    _FakeHeliaTorch(float_model=True).install(monkeypatch)
    with pytest.raises(EngineError, match="example_inputs") as info:
        TorchProgramConverter(tmp_path / "kws.pt2", _convert_args()).convert()
    assert "quantize()" in info.value.hint


def test_missing_helia_torch_is_an_engine_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "nsx_cortex_m", None)
    monkeypatch.setitem(sys.modules, "nsx_cortex_m.helia_aot", None)
    with pytest.raises(EngineError, match="needs helia-torch") as info:
        TorchProgramConverter(tmp_path / "kws.pt2", _convert_args()).convert()
    assert "helia-torch[helia-aot]" in info.value.hint


# -- preflight ---------------------------------------------------------------------------------------------


def _ctx(tmp_path: Path, content: bytes, engine: dict) -> PipelineContext:
    model = tmp_path / "kws.pt2"
    model.write_bytes(content)
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": engine,
            "output": {"dir": str(tmp_path / "out")},
            "work_dir": str(tmp_path / "work"),
        },
    )
    return PipelineContext(config=config, work_dir=tmp_path / "work")


def _tools(name: str, path: str | None = None) -> str:
    return f"/usr/bin/{name}"


def test_preflight_accepts_a_pt2_for_helia_aot(tmp_path):
    ctx = _ctx(tmp_path, TORCH_PROGRAM_MAGIC + b"\x00" * 512, {"type": "helia-aot"})
    with patch("shutil.which", side_effect=_tools):
        PreflightStage().run(ctx)


def test_preflight_rejects_a_pt2_that_is_not_a_zip(tmp_path):
    ctx = _ctx(tmp_path, b"\x00\x00\x00\x00TFL3" + b"\x00" * 512, {"type": "helia-aot"})
    with patch("shutil.which", side_effect=_tools):
        with pytest.raises(ConfigError, match="saved PyTorch program"):
            PreflightStage().run(ctx)


@pytest.mark.parametrize("engine", ["helia-rt", "litert"])
def test_preflight_rejects_a_pt2_for_interpreters(tmp_path, engine):
    ctx = _ctx(tmp_path, TORCH_PROGRAM_MAGIC + b"\x00" * 512, {"type": engine})
    with patch("shutil.which", side_effect=_tools):
        with pytest.raises(ConfigError, match=r"\.tflite extension"):
            PreflightStage().run(ctx)


def test_preflight_rejects_a_pt2_for_the_npu(tmp_path):
    ctx = _ctx(
        tmp_path,
        TORCH_PROGRAM_MAGIC + b"\x00" * 512,
        {"type": "helia-aot", "backend": "ethos_u"},
    )
    with patch("shutil.which", side_effect=_tools):
        with pytest.raises(ConfigError, match="Vela-compiled"):
            PreflightStage().run(ctx)
