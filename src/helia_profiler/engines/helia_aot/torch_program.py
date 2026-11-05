"""PyTorch programs as heliaAOT input, through helia-torch.

heliaAOT reads LiteRT (``.tflite``) models. helia-torch (nsx-executorch's
``nsx_cortex_m`` package) converts a quantized PyTorch program to heliaAOT's
AIR with the frontend behind its ExecuTorch delegate, and hands that to
heliaAOT's converter in-process -- heliaAOT has no AIR file format. So the
helia-aot engine also accepts a ``.pt2``: a PT2E-quantized
``torch.export.ExportedProgram`` saved with ``torch.export.save()``, for
example the program helia-torch's ``quantize()`` returns. The firmware,
memory placement and per-layer profiling are the same as for a ``.tflite``.

The whole program must convert: helia-torch refuses a model with an operator
heliaAOT cannot compile (profile that one with the executorch engine and the
heliaAOT delegate instead). Needs helia-torch with its heliaAOT extra in the
environment hpx runs in: ``pip install 'helia-torch[helia-aot]'``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import EngineError

#: File suffix of a saved ``torch.export.ExportedProgram``.
TORCH_PROGRAM_SUFFIX = ".pt2"
#: A ``.pt2`` is a zip archive.
TORCH_PROGRAM_MAGIC = b"PK\x03\x04"

_INSTALL_HINT = (
    "Install helia-torch with its heliaAOT extra in the environment hpx runs in: "
    "pip install 'helia-torch[helia-aot]' (nsx-executorch's aot/ package)."
)


def is_torch_program(path: str | Path) -> bool:
    """True when *path* names a saved PyTorch program rather than a ``.tflite``."""
    return Path(path).suffix.lower() == TORCH_PROGRAM_SUFFIX


class TorchProgramConverter:
    """``AotConverter``'s ``convert()`` for a ``.pt2``: helia-torch builds the AIR, heliaAOT the module.

    *convert_args* are the same heliaAOT ``ConvertArgs`` hpx builds for a ``.tflite`` (module, platform,
    memory placement, ``result_views``); helia-torch only replaces the model loader and adds the operator
    rules that keep the generated code bit-exact with ExecuTorch's kernels.
    """

    def __init__(self, model_path: Path, convert_args: Any):
        self.model_path = Path(model_path)
        self.convert_args = convert_args

    def convert(self) -> Any:
        try:
            import torch
            from nsx_cortex_m.helia_aot import NotFullyDelegableError, convert_air, program_to_air
        except ImportError as exc:
            raise EngineError(
                f"Profiling a PyTorch program ({self.model_path.name}) with heliaAOT needs helia-torch: {exc}",
                hint=_INSTALL_HINT,
            ) from exc

        try:
            program = torch.export.load(self.model_path)
        except Exception as exc:
            raise EngineError(
                f"Could not load {self.model_path} as a saved torch.export program: {exc}",
                hint="Save the PT2E-quantized program with torch.export.save(program, 'model.pt2').",
            ) from exc
        name = getattr(self.convert_args.model, "name", None) or self.model_path.stem
        try:
            air_program = program_to_air(program, name=name)
        except NotFullyDelegableError as exc:
            raise EngineError(
                str(exc),
                hint="Profile this model with engine.type executorch and a PTE exported with "
                "helia-torch's heliaAOT delegate, which leaves these operators in ExecuTorch.",
            ) from exc
        except ValueError as exc:
            raise EngineError(
                f"{self.model_path.name}: {exc}",
                hint="Pass the PT2E-quantized program (helia-torch quantize()), not the float model.",
            ) from exc
        return convert_air(air_program.air, self.convert_args)


def aot_converter(model_path: Path, convert_args: Any) -> Any:
    """The heliaAOT converter for *model_path*: ``AotConverter`` for a ``.tflite``, helia-torch for a ``.pt2``."""
    if is_torch_program(model_path):
        return TorchProgramConverter(model_path, convert_args)
    from helia_aot import AotConverter

    return AotConverter(config=convert_args)
