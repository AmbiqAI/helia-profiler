"""hpx and heliaAOT handle a ``.tflite`` without importing PyTorch.

PyTorch (torch, torchao, ExecuTorch) and helia-torch (``nsx_cortex_m``) are
needed only for a ``.pt2``, and ``engines/helia_aot/torch_program.py`` imports
them inside the conversion. These tests run hpx in a fresh interpreter whose
import system refuses those packages and records every attempt, so a stray
top-level ``import torch`` -- in hpx or in heliaAOT -- fails here even on a
machine where torch is installed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

KWS = Path(__file__).parent / "fixtures" / "kws_ref_model.tflite"
BLOCKED = ("torch", "torchao", "executorch", "nsx_cortex_m")

# Installed before anything else in the child: any import of a blocked package
# (or one of its submodules) is recorded and fails as if it were not installed.
_PRELUDE = textwrap.dedent(
    f"""
    import importlib.abc
    import json
    import sys

    BLOCKED = {BLOCKED!r}
    attempts = []

    class _Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in BLOCKED:
                attempts.append(name)
                raise ModuleNotFoundError(f"No module named {{name!r}} (blocked by the test)", name=name)
            return None

    sys.meta_path.insert(0, _Block())
    for name in list(sys.modules):
        if name.split(".")[0] in BLOCKED:
            del sys.modules[name]
    """
)

_REPORT = textwrap.dedent(
    """
    print("RESULT " + json.dumps({"attempts": attempts, "loaded": sorted(
        name for name in sys.modules if name.split(".")[0] in BLOCKED)}))
    """
)


def _run(body: str, tmp_path: Path) -> dict:
    script = _PRELUDE + textwrap.dedent(body) + _REPORT
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    line = next(line for line in proc.stdout.splitlines() if line.startswith("RESULT "))
    return json.loads(line[len("RESULT ") :])


def test_hpx_imports_without_pytorch(tmp_path: Path):
    result = _run(
        f"""
        import helia_profiler
        import helia_profiler.cli
        import helia_profiler.engines.executorch
        import helia_profiler.engines.helia_aot.adapter
        import helia_profiler.engines.helia_aot.compile
        import helia_profiler.evaluation.engine_analysis
        import helia_profiler.stages.analyze_model
        import helia_profiler.stages.preflight
        from helia_profiler.engines.helia_aot.torch_program import is_torch_program

        assert not is_torch_program({str(KWS)!r})
        """,
        tmp_path,
    )
    assert result == {"attempts": [], "loaded": []}


def test_a_tflite_converts_through_hpx_without_pytorch(tmp_path: Path):
    pytest.importorskip("helia_aot")
    result = _run(
        f"""
        from pathlib import Path

        import helia_aot
        from helia_profiler import Session
        from helia_profiler.engines.helia_aot.compile import _run_aot_compiler, aot_platform_for_board

        board = "apollo510_evb"
        config = (
            Session.from_dict({{}})
            .with_model(Path({str(KWS)!r}), arena_location="sram", weights_location="mram")
            .with_engine("helia-aot")
            .with_target(board=board)
            .resolve()
        )
        out = Path.cwd() / "out"
        _run_aot_compiler(config, out, "hpx_aot", "hpx", aot_platform_for_board(board))
        assert any((out / "hpx_aot").rglob("*.c")), "no generated sources"
        """,
        tmp_path,
    )
    assert result == {"attempts": [], "loaded": []}


def test_a_pt2_without_helia_torch_is_an_engine_error(tmp_path: Path):
    # With PyTorch unavailable, a .pt2 fails with hpx's own error and install hint, not an ImportError.
    pt2 = tmp_path / "model.pt2"
    pt2.write_bytes(b"PK\x03\x04" + b"\0" * 64)
    result = _run(
        f"""
        from pathlib import Path

        from helia_profiler.engines.helia_aot.torch_program import TorchProgramConverter
        from helia_profiler.errors import EngineError

        try:
            TorchProgramConverter(Path({str(pt2)!r}), None).convert()
        except EngineError as exc:
            assert "helia-torch" in str(exc), exc
        else:
            raise AssertionError("expected an EngineError")
        """,
        tmp_path,
    )
    assert result["loaded"] == [] and result["attempts"], result
