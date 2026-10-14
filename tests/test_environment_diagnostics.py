import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest
from rich.console import Console

from helia_profiler.console import HpxConsole
from helia_profiler.errors import PowerError
from helia_profiler.hostenv import doctor
from helia_profiler.power.joulescope import device


@pytest.fixture
def facts(monkeypatch, tmp_path):
    entry = tmp_path / "bin" / "hpx"
    entry.parent.mkdir()
    entry.touch()
    env = doctor._Environment(
        (3, 11), "/venv/bin/python", "2.2.12", None, "/pyenv/shims/hpx", entry, True
    )
    monkeypatch.setattr(doctor, "_dependency_specs", lambda *_: ())
    monkeypatch.setattr(doctor, "_environment", lambda devices=None: replace(env, devices=devices))
    return env


@pytest.mark.parametrize(
    "family,python,driver,expected",
    [
        ("js320", (3, 11), "2.2.12", False),
        ("js320", (3, 12), "2.3.4", True),
        ("js110", (3, 11), "2.2.12", True),
        ("js220", (3, 11), "2.2.12", True),
        ("js320", (3, 11), None, False),
        ("js320", (3, 12), None, None),
        (None, (3, 11), "2.2.12", None),
        ("", (3, 11), "2.2.12", True),
    ],
)
def test_doctor_and_selected_power_policy(facts, monkeypatch, family, python, driver, expected):
    devices = None if family is None else (family,) if family else ()
    monkeypatch.setattr(
        doctor,
        "_environment",
        lambda devices=None: replace(facts, devices=devices, python=python, driver=driver),
    )
    monkeypatch.setattr(
        device,
        "sys",
        SimpleNamespace(
            version_info=SimpleNamespace(major=python[0], minor=python[1]),
            executable=facts.interpreter,
        ),
    )
    result = doctor.inspect_environment(joulescope_devices=devices)
    check = next(c for c in result.versions if c.name == "js320_environment")
    assert result.ok and check.ok is expected
    assert check.installed and (driver or "unknown") in check.installed
    if expected is False:
        assert check.hint and "Python >= 3.12" in check.hint
        with pytest.raises(PowerError, match="Python 3.11"):
            device._check_environment(family)
    elif family:
        device._check_environment(family)
    if expected is None:
        assert check.hint and "unknown" in check.hint


def test_doctor_reports_unverified_shim(monkeypatch, tmp_path):
    entry = tmp_path / ("Scripts/hpx.exe" if sys.platform == "win32" else "bin/hpx")
    entry.parent.mkdir()
    entry.touch()
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(doctor, "_dependency_specs", lambda *_: ())
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/pyenv/shims/hpx")
    result = doctor.inspect_environment()
    assert result.ok
    check = next(c for c in result.versions if c.name == "hpx_launcher")
    assert check.ok is None and check.hint and str(entry) in check.hint


@pytest.mark.parametrize(
    "launcher,in_venv,expected",
    [
        ("/pyenv/shims/hpx", True, None),
        ("current", True, True),
        ("/pyenv/shims/hpx", False, None),
        (None, True, None),
    ],
)
def test_launcher_metadata_controls(facts, monkeypatch, launcher, in_venv, expected):
    if launcher == "current":
        launcher = str(facts.expected_launcher)
    monkeypatch.setattr(
        doctor, "_environment", lambda _=None: replace(facts, launcher=launcher, in_venv=in_venv)
    )
    checks = [c for c in doctor.inspect_environment().versions if c.name == "hpx_launcher"]
    assert (checks[0].ok if checks else None) is expected


def test_launcher_same_entrypoint_is_not_reported_as_stale(facts, tmp_path):
    launcher = tmp_path / "shims" / "hpx"
    launcher.parent.mkdir()
    launcher.hardlink_to(facts.expected_launcher)
    check = doctor._launcher_check(replace(facts, launcher=str(launcher)))
    assert check.ok is True
    assert check.hint is None


def test_shim_target_is_unknown_without_resolution(facts):
    check = doctor._launcher_check(facts)
    assert check.ok is None
    assert check.hint and "not verified" in check.hint


def test_missing_environment_entrypoint_does_not_suggest_unusable_command(facts, monkeypatch):
    env = replace(facts, expected_launcher=facts.expected_launcher.with_name("missing"))
    monkeypatch.setattr(doctor, "_environment", lambda _=None: env)
    assert not any(c.name == "hpx_launcher" for c in doctor.inspect_environment().versions)


def test_doctor_preserves_literal_paths_and_hints():
    path = "/tmp/[red]/python"
    hint = "Run /tmp/[blue]/hpx"
    result = doctor.DoctorResult(
        (), (doctor.DoctorVersionCheck("Environment", "environment", path, None, None, hint),)
    )
    console = HpxConsole()
    console._console = Console(record=True, width=200)
    console.print_doctor(result)
    output = console._console.export_text()
    assert path in output
    assert hint in output
