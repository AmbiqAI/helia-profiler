import sys
from dataclasses import replace

import pytest

from helia_profiler.errors import PowerError
from helia_profiler.hostenv import doctor
from helia_profiler.power.joulescope.device import _check_environment


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
        ("js320", (3, 11), None, None),
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
    result = doctor.inspect_environment(joulescope_devices=devices)
    check = next(c for c in result.versions if c.name == "js320_environment")
    assert result.ok and check.ok is expected
    assert check.installed and (driver or "unknown") in check.installed
    if expected is False:
        assert check.hint and "Python >= 3.12" in check.hint
        with pytest.raises(PowerError, match="Python 3.11"):
            _check_environment(family)
    elif family:
        _check_environment(family)
    if expected is None:
        assert check.hint and "unknown" in check.hint


def test_doctor_reports_stale_shim(monkeypatch, tmp_path):
    entry = tmp_path / ("Scripts/hpx.exe" if sys.platform == "win32" else "bin/hpx")
    entry.parent.mkdir()
    entry.touch()
    monkeypatch.setattr(sys, "prefix", str(tmp_path))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(doctor, "_dependency_specs", lambda *_: ())
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/pyenv/shims/hpx")
    result = doctor.inspect_environment()
    assert result.ok
    assert any(c.name == "hpx_launcher" and c.ok is False for c in result.versions)


@pytest.mark.parametrize(
    "launcher,in_venv,expected",
    [
        ("/pyenv/shims/hpx", True, False),
        ("/venv/bin/hpx", True, True),
        ("/pyenv/shims/hpx", False, None),
        (None, True, None),
    ],
)
def test_launcher_metadata_controls(facts, monkeypatch, launcher, in_venv, expected):
    monkeypatch.setattr(
        doctor, "_environment", lambda _=None: replace(facts, launcher=launcher, in_venv=in_venv)
    )
    checks = [c for c in doctor.inspect_environment().versions if c.name == "hpx_launcher"]
    assert (checks[0].ok if checks else None) is expected
