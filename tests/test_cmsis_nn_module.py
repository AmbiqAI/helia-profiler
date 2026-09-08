"""Local ns-cmsis-nn module validation and vendoring."""

from pathlib import Path

import pytest

from helia_profiler.config import ProfileConfig, load_config
from helia_profiler.engines.cmsis_nn import cmsis_nn_module_ref
from helia_profiler.errors import EngineError


def _config(engine_config: dict[str, str]) -> ProfileConfig:
    return load_config(
        None,
        {
            "model": {"path": str(Path(__file__).parent / "fixtures" / "kws_ref_model.tflite")},
            "engine": {"type": "helia-rt", "config": engine_config},
            "target": {"board": "apollo510_evb"},
        },
    )


@pytest.mark.parametrize("revision", ["V.18.0", "V.19.1.0", "V.20.0", None])
@pytest.mark.parametrize("route", ["config", "environment"])
def test_local_module_accepts_header_revisions(
    tmp_path: Path,
    fake_cmsis_nn: Path,
    monkeypatch: pytest.MonkeyPatch,
    revision: str | None,
    route: str,
) -> None:
    header = fake_cmsis_nn / "Include" / "arm_nnfunctions.h"
    header.write_text(f"/* $Revision: {revision} */\n" if revision else "/* no revision */\n")
    monkeypatch.delenv("CMSIS_NN_PATH", raising=False)
    engine_config = {}
    if route == "config":
        engine_config["cmsis_nn_path"] = str(fake_cmsis_nn)
    else:
        monkeypatch.setenv("CMSIS_NN_PATH", str(fake_cmsis_nn))

    module = cmsis_nn_module_ref(_config(engine_config), tmp_path / "work")

    assert module.local
    assert module.name == "nsx-cmsis-nn"
    assert (module.path / "Include" / header.name).read_bytes() == header.read_bytes()
    assert (module.path / "Source" / "stub.c").is_file()
    assert (module.path / "nsx-module.yaml").read_bytes() == (
        fake_cmsis_nn / "nsx" / "nsx-module.yaml"
    ).read_bytes()
    assert (module.path / "nsx" / "CMakeLists.txt").read_bytes() == (
        fake_cmsis_nn / "nsx" / "CMakeLists.txt"
    ).read_bytes()


@pytest.mark.parametrize("missing", ["CMakeLists.txt", "nsx-module.yaml", "both"])
def test_local_module_requires_native_build_files(
    tmp_path: Path, fake_cmsis_nn: Path, missing: str
) -> None:
    (fake_cmsis_nn / "Include" / "arm_nnfunctions.h").write_text("/* $Revision: V.19.1.0 */\n")
    for name in ("CMakeLists.txt", "nsx-module.yaml"):
        if missing in (name, "both"):
            (fake_cmsis_nn / "nsx" / name).unlink()

    with pytest.raises(EngineError, match="missing native nsx/ module") as exc:
        cmsis_nn_module_ref(_config({"cmsis_nn_path": str(fake_cmsis_nn)}), tmp_path / "work")

    assert exc.value.hint is not None
    assert "nsx/CMakeLists.txt and nsx/nsx-module.yaml" in exc.value.hint
