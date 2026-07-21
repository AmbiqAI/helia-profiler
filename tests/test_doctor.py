"""Tests for typed, configuration-aware dependency reporting."""

from __future__ import annotations

from pathlib import Path

from helia_profiler.config import Toolchain, Transport
from helia_profiler.doctor import inspect_environment
from helia_profiler.engines import EngineType


def _which_all(name: str) -> str:
    return f"/usr/bin/{name}"


def test_inspect_environment_reports_missing_required_python(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: None)

    result = inspect_environment()

    assert not result.ok
    assert {check.name for check in result.missing_required} == {"neuralspotx", "pylink"}


def test_inspect_environment_uses_selected_toolchain_and_transport(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment(
        toolchain=Toolchain.ARMCLANG,
        transport=Transport.USB_CDC,
    )
    names = {check.name for check in result.checks}

    assert result.ok
    assert {"armclang", "fromelf"} <= names
    assert "arm-none-eabi-gcc" not in names
    assert "pylink" not in names


def test_inspect_environment_requires_aot_only_for_aot_engine(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr(
        "helia_profiler.doctor.find_spec",
        lambda name: None if name == "helia_aot" else object(),
    )

    rt = inspect_environment(engine=EngineType.HELIA_RT)
    aot = inspect_environment(engine=EngineType.HELIA_AOT)

    assert rt.ok
    assert not aot.ok
    assert [check.name for check in aot.missing_required] == ["helia_aot"]


def test_inspect_environment_validates_atfe_root(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in (
        "clang",
        "clang++",
        "llvm-ar",
        "llvm-objcopy",
        "llvm-size",
        "llvm-nm",
    ):
        (bin_dir / name).touch()
    monkeypatch.setenv("ATFE_ROOT", str(tmp_path))
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment(toolchain=Toolchain.ATFE)

    check = next(check for check in result.checks if check.name == "ATFE_ROOT")
    assert check.available
    assert check.path == str(bin_dir)
