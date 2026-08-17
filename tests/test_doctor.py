"""Tests for typed, configuration-aware dependency reporting."""

from __future__ import annotations

from pathlib import Path

from helia_profiler.config import Toolchain, Transport
from helia_profiler.doctor import DoctorVersionCheck, check_versions, inspect_environment
from helia_profiler.engines import EngineType
from helia_profiler.errors import CaptureError, ConfigError


def _which_all(name: str) -> str:
    return f"/usr/bin/{name}"


def _jlink_found() -> str:
    return "/usr/bin/JLinkExe"


def test_inspect_environment_reports_missing_required_python(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: None)

    result = inspect_environment()

    assert not result.ok
    assert {check.name for check in result.missing_required} == {"neuralspotx", "pylink"}


def test_inspect_environment_uses_selected_toolchain_and_transport(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
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
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
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
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment(toolchain=Toolchain.ATFE)

    check = next(check for check in result.checks if check.name == "ATFE_ROOT")
    assert check.available
    assert check.path == str(bin_dir)


def test_inspect_environment_finds_jlink_beyond_path_lookup(monkeypatch) -> None:
    # Windows installs name the commander JLink.exe, not JLinkExe, so the
    # J-Link check must use full probe discovery rather than a bare which().
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "helia_profiler.doctor.find_jlink_exe",
        lambda: r"C:\Program Files\SEGGER\JLink_V960\JLink.exe",
    )
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment()

    check = next(check for check in result.checks if check.name == "JLinkExe")
    assert check.available
    assert check.path == r"C:\Program Files\SEGGER\JLink_V960\JLink.exe"


def test_inspect_environment_reports_missing_jlink(monkeypatch) -> None:
    def _raise() -> str:
        raise CaptureError("JLinkExe not found")

    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _raise)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment()

    assert not result.ok
    assert [check.name for check in result.missing_required] == ["JLinkExe"]


# ---------------------------------------------------------------------------
# Version checks and machine-readable (--json) output.
# ---------------------------------------------------------------------------


def test_check_versions_reports_hpx_own_version() -> None:
    versions = check_versions()

    hpx_check = next(v for v in versions if v.name == "hpx")
    assert hpx_check.ok is True
    assert hpx_check.installed
    assert hpx_check.required is None


def test_check_versions_matches_neuralspotx_against_baseline(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor._package_version", lambda _name: "0.7.17")

    versions = check_versions()

    neuralspotx_check = next(v for v in versions if v.name == "neuralspotx")
    assert neuralspotx_check.installed == "0.7.17"
    assert neuralspotx_check.required == "==0.7.17"
    assert neuralspotx_check.ok is True
    assert neuralspotx_check.hint is None


def test_check_versions_flags_neuralspotx_mismatch(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor._package_version", lambda _name: "0.1.0")

    versions = check_versions()

    neuralspotx_check = next(v for v in versions if v.name == "neuralspotx")
    assert neuralspotx_check.installed == "0.1.0"
    assert neuralspotx_check.ok is False
    assert neuralspotx_check.hint is not None
    assert "0.7.17" in neuralspotx_check.hint


def test_check_versions_neuralspotx_unknown_when_not_installed(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor._package_version", lambda _name: None)

    versions = check_versions()

    neuralspotx_check = next(v for v in versions if v.name == "neuralspotx")
    assert neuralspotx_check.installed is None
    assert neuralspotx_check.ok is None


def test_check_versions_never_raises_when_baseline_unavailable(monkeypatch) -> None:
    def _raise(*_args, **_kwargs):
        raise ConfigError("no baseline")

    monkeypatch.setattr("helia_profiler.compatibility.load_compatibility_baseline", _raise)

    versions = check_versions()

    assert {v.name for v in versions} == {"hpx", "cmake", "arm-none-eabi-gcc"}


def test_check_versions_flags_cmake_below_minimum(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.toolchain_probe.cmake_version", lambda *, timeout_s: "cmake version 3.10.0")

    versions = check_versions()

    cmake_check = next(v for v in versions if v.name == "cmake")
    assert cmake_check.installed == "3.10.0"
    assert cmake_check.ok is False
    assert "Upgrade CMake" in cmake_check.hint


def test_check_versions_cmake_unknown_when_banner_unparsable(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.toolchain_probe.cmake_version", lambda *, timeout_s: "")

    versions = check_versions()

    cmake_check = next(v for v in versions if v.name == "cmake")
    assert cmake_check.installed is None
    assert cmake_check.ok is None
    assert cmake_check.hint is None


def test_check_versions_ok_when_cmake_meets_minimum(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.toolchain_probe.cmake_version", lambda *, timeout_s: "cmake version 3.24.0")

    versions = check_versions()

    cmake_check = next(v for v in versions if v.name == "cmake")
    assert cmake_check.installed == "3.24.0"
    assert cmake_check.ok is True
    assert cmake_check.hint is None


def test_check_versions_compiler_unknown_when_missing(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.toolchain_probe.compiler_version", lambda *_a, **_kw: "")

    versions = check_versions()

    compiler_check = next(v for v in versions if v.name == "arm-none-eabi-gcc")
    assert compiler_check.installed is None
    assert compiler_check.ok is None


def test_doctor_version_check_to_dict_roundtrips_all_fields() -> None:
    check = DoctorVersionCheck(
        label="CMake", name="cmake", installed="3.24.0", required=">=3.24", ok=True, hint=None
    )

    assert check.to_dict() == {
        "label": "CMake",
        "name": "cmake",
        "installed": "3.24.0",
        "required": ">=3.24",
        "ok": True,
        "hint": None,
    }


def test_inspect_environment_include_versions_false_by_default(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment()

    assert result.versions == ()


def test_inspect_environment_include_versions_true_populates_versions(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment(include_versions=True)

    assert len(result.versions) >= 1
    assert {v.name for v in result.versions} >= {"hpx"}


def test_doctor_result_to_dict_is_json_safe(monkeypatch) -> None:
    import json

    monkeypatch.setattr("helia_profiler.doctor.shutil.which", _which_all)
    monkeypatch.setattr("helia_profiler.doctor.find_jlink_exe", _jlink_found)
    monkeypatch.setattr("helia_profiler.doctor.find_spec", lambda _name: object())

    result = inspect_environment(include_versions=True)
    payload = json.dumps(result.to_dict())
    reloaded = json.loads(payload)

    assert reloaded["ok"] is True
    assert isinstance(reloaded["checks"], list)
    assert isinstance(reloaded["versions"], list)


def test_doctor_result_version_mismatches_only_lists_failures(monkeypatch) -> None:
    monkeypatch.setattr("helia_profiler.doctor._package_version", lambda _name: "0.0.1")

    result = inspect_environment(include_versions=True)

    mismatches = result.version_mismatches
    assert mismatches
    assert all(check.ok is False for check in mismatches)
    assert any(check.name == "neuralspotx" for check in mismatches)
