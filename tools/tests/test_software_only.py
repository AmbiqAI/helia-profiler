"""Exercise the launcher using inert vendor modules in fresh interpreters."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "tools" / "software_only.py"


@pytest.fixture
def sandbox(tmp_path):
    marker = tmp_path / "vendor-imported"
    for name in ("pylink", "pyjoulescope_driver", "joulescope", "usb", "serial"):
        package = tmp_path / name
        package.mkdir()
        package.joinpath("__init__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            "def Serial(*a, **k):\n    raise RuntimeError('INERT DEVICE SENTINEL')\n"
        )
    return tmp_path, marker


def run_probe(sandbox, source, *, guarded=True):
    directory, marker = sandbox
    probe = directory / "probe.py"
    probe.write_text(source)
    env = dict(os.environ, PYTHONPATH=str(directory), PYTHONSAFEPATH="1")
    command = [sys.executable, str(LAUNCHER), "python"] if guarded else [sys.executable]
    return subprocess.run(
        command + [str(probe)], env=env, capture_output=True, text=True, timeout=60
    )


@pytest.mark.parametrize("module", ["pylink", "pyjoulescope_driver", "joulescope", "usb"])
def test_guard_precedes_vendor_import_and_negative_control(sandbox, module):
    source = f"import {module}\n"
    result = run_probe(sandbox, source)
    assert result.returncode != 0
    assert "software-only: device import blocked" in result.stderr
    assert not sandbox[1].exists()
    control = run_probe(sandbox, source, guarded=False)
    assert control.returncode == 0
    assert sandbox[1].exists()


def test_retained_callback_cannot_reach_serial_after_late_patch(sandbox):
    source = """import serial
import types
open_device = serial.Serial
def capture():
    return open_device()
replay = types.FunctionType(capture.__code__, dict(capture.__globals__))
open_device = lambda: None
serial.Serial = lambda: None
replay()
"""
    result = run_probe(sandbox, source)
    assert result.returncode != 0
    assert "software-only: device operation requires a fake" in result.stderr
    assert not sandbox[1].exists()
    control = run_probe(sandbox, source, guarded=False)
    assert "INERT DEVICE SENTINEL" in control.stderr
    assert sandbox[1].exists()


@pytest.mark.parametrize("close_fds", [True, False])
def test_children_and_grandchildren_inherit_guard(sandbox, close_fds):
    inner = "import serial; serial.Serial()"
    child = (
        "import subprocess, sys; "
        f"p = subprocess.run([sys.executable, '-c', {inner!r}], capture_output=True, text=True); "
        "assert p.returncode != 0 and 'software-only:' in p.stderr; print('grandchild blocked')"
    )
    result = run_probe(
        sandbox,
        (
            "import subprocess, sys\n"
            f"p = subprocess.run([sys.executable, '-c', {child!r}], "
            f"close_fds={close_fds!r}, capture_output=True, text=True)\n"
            "assert p.returncode == 0, p.stderr\nprint(p.stdout)\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert "grandchild blocked" in result.stdout
    assert not sandbox[1].exists()


def test_child_uses_the_same_checkout(sandbox):
    child = "import helia_profiler; print(helia_profiler.__file__)"
    result = run_probe(
        sandbox,
        "import subprocess, sys\n"
        f"p = subprocess.run([sys.executable, '-c', {child!r}], capture_output=True, text=True)\n"
        "assert p.returncode == 0, p.stderr\nprint(p.stdout)\n",
    )
    assert result.returncode == 0, result.stderr
    assert str(ROOT / "src" / "helia_profiler" / "__init__.py") in result.stdout
    assert not sandbox[1].exists()


@pytest.mark.parametrize("options", ["['-S']", "['-I']", "['-E']", "['-sS']"])
def test_child_startup_bypasses_rejected(sandbox, options):
    child = f"from pathlib import Path; Path({str(sandbox[1])!r}).touch()"
    result = run_probe(
        sandbox,
        (
            "import subprocess, sys\n"
            f"subprocess.run([sys.executable] + {options} + ['-c', {child!r}])\n"
        ),
    )
    assert result.returncode != 0
    assert "software-only: unsupported Python startup option" in result.stderr
    assert not sandbox[1].exists()


def test_child_stripped_environment_rejected(sandbox):
    child = f"from pathlib import Path; Path({str(sandbox[1])!r}).touch()"
    result = run_probe(
        sandbox,
        (f"import subprocess, sys\nsubprocess.run([sys.executable, '-c', {child!r}], env={{}})\n"),
    )
    assert result.returncode != 0
    assert "software-only: child must inherit" in result.stderr
    assert not sandbox[1].exists()


@pytest.mark.parametrize(
    "operation",
    [
        "subprocess.run(['a-command-that-must-never-start'])",
        "subprocess.run('a-command-that-must-never-start', shell=True)",
        "os.system('a-command-that-must-never-start')",
        "sys.audit('os.posix_spawn', sys.executable, [sys.executable], {})",
        "os.execv(sys.executable, [sys.executable, '-c', 'import pylink'])",
    ],
)
def test_process_escape_rejected_before_launch(sandbox, operation):
    result = run_probe(sandbox, f"import subprocess, os, sys\n{operation}\n")
    assert result.returncode != 0
    assert "software-only:" in result.stderr
    assert "FileNotFoundError" not in result.stderr
    assert not sandbox[1].exists()


def test_preloaded_module_rejected(sandbox):
    source = (
        "import runpy, sys\nimport pylink\n"
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
        "from software_guard.guard import install\ninstall()\n"
    )
    result = run_probe(sandbox, source, guarded=False)
    assert "software-only: device modules already loaded" in result.stderr
    assert sandbox[1].exists()


def test_pytest_collection_is_guarded(sandbox):
    directory, marker = sandbox
    test = directory / "test_probe.py"
    test.write_text("import pylink\ndef test_never_runs(): assert False\n")
    env = dict(os.environ, PYTHONPATH=str(directory), PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "pytest", "--confcutdir", str(directory), str(test)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "software-only: device import blocked" in result.stdout
    assert not marker.exists()


def test_fake_normal_and_degraded_capture_paths(sandbox):
    source = """import sys, types
from pathlib import Path
import pytest
clock = types.ModuleType('pyjoulescope_driver.time64')
clock.SECOND = 1 << 30
vendor = types.ModuleType('pyjoulescope_driver')
vendor.time64 = clock
sys.modules['pyjoulescope_driver'] = vendor
sys.modules['pyjoulescope_driver.time64'] = clock
raise SystemExit(pytest.main([
    'tests/test_power.py::TestStreamedGateSelection',
    'tests/test_power.py::TestMissedGateWarningNamesTheFix',
    'tests/test_power.py::TestGatedCaptureContracts', '-q',
]))
"""
    result = run_probe(sandbox, source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "26 passed" in result.stdout
    assert not sandbox[1].exists()


def test_windows_audit_conversion_is_allowed_only_inside_validated_launch(sandbox):
    source = """import subprocess, sys
from unittest.mock import patch
guard = sys.modules['_hpx_software_guard']
called = []
def windows_init(self, *args, **kwargs):
    sys.audit('subprocess.Popen', None, 'python.exe -c pass', None, None)
    called.append(True)
with patch.object(guard.GuardedPopen.__bases__[0], '__init__', windows_init):
    subprocess.Popen([sys.executable, '-c', 'pass'])
assert called == [True]
try:
    sys.audit('subprocess.Popen', None, 'python.exe -c pass', None, None)
except guard.SoftwareOnlyViolation:
    pass
else:
    raise AssertionError('unvalidated string audit allowed')
"""
    result = run_probe(sandbox, source)
    assert result.returncode == 0, result.stderr
    assert not sandbox[1].exists()
