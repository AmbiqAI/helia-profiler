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


@pytest.mark.parametrize("relative", [False, True])
def test_standalone_sibling_imports_preserve_checkout_and_guard(sandbox, relative):
    directory, marker = sandbox
    probes = directory / "standalone probes"
    probes.mkdir()
    (probes / "helper.py").write_text("VALUE = 42\n")
    package = probes / "helpers"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 43\n")
    competing = probes / "helia_profiler"
    competing.mkdir()
    (competing / "__init__.py").write_text("raise AssertionError('wrong checkout')\n")
    probe = probes / "probe.py"
    probe.write_text(
        "import helper, helpers, importlib.util\n"
        "from pathlib import Path\n"
        "assert helper.VALUE == 42\n"
        "assert helpers.VALUE == 43\n"
        f"assert Path(importlib.util.find_spec('helia_profiler').origin) == "
        f"Path({str(ROOT / 'src' / 'helia_profiler' / '__init__.py')!r})\n"
        "try:\n    import pylink\n"
        "except RuntimeError as error:\n"
        "    assert 'software-only: device import blocked' in str(error)\n"
        "else:\n    raise AssertionError('vendor guard missing')\n"
        "print('sibling helper and checkout verified')\n"
    )
    argument = probe.relative_to(directory) if relative else probe
    result = subprocess.run(
        [sys.executable, str(LAUNCHER), "python", str(argument)],
        cwd=directory,
        env=dict(os.environ, PYTHONPATH=str(directory), PYTHONSAFEPATH="1"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "sibling helper and checkout verified" in result.stdout
    assert not marker.exists()


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


def run_port_probe(directory, operation, *, guarded):
    """Exercise inert discovery APIs or the real ports function without HPX package startup."""
    directory.mkdir()
    imported = directory / "serial-imported"
    called = directory / "enumeration-called"
    package = directory / "serial" / "tools"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(imported)!r}).touch()\n"
    )
    (package / "__init__.py").write_text("")
    (package / "list_ports.py").write_text(
        "from pathlib import Path\n"
        f"def comports():\n    Path({str(called)!r}).touch()\n    return []\n"
        f"def grep(pattern):\n    Path({str(called)!r}).touch()\n    yield from ()\n"
    )
    if operation == "hpx":
        source = (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('guard_test_ports', "
            f"{str(ROOT / 'src' / 'helia_profiler' / 'transport' / 'ports.py')!r})\n"
            "ports = importlib.util.module_from_spec(spec)\n"
            "sys.modules[spec.name] = ports\nspec.loader.exec_module(ports)\n"
            "assert ports.list_serial_ports(include_all=True) == ()\n"
        )
    else:
        source = "from serial.tools import list_ports\n"
        source += (
            "assert list_ports.comports() == []\n"
            if operation == "comports"
            else "assert list(list_ports.grep('.*')) == []\n"
        )
    probe = directory / "probe.py"
    probe.write_text(source)
    command = [sys.executable, str(LAUNCHER), "python"] if guarded else [sys.executable]
    result = subprocess.run(
        command + [str(probe)],
        env=dict(os.environ, PYTHONPATH=str(directory), PYTHONSAFEPATH="1"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, imported, called


@pytest.mark.parametrize("operation", ["comports", "grep", "hpx"])
def test_port_enumeration_requires_fake_before_discovery(tmp_path, operation):
    control, imported, called = run_port_probe(tmp_path / "control", operation, guarded=False)
    assert control.returncode == 0, control.stderr
    assert imported.exists()
    assert called.exists()

    result, imported, called = run_port_probe(tmp_path / "guarded", operation, guarded=True)
    assert result.returncode != 0
    assert "SoftwareOnlyViolation: software-only: device operation requires a fake" in result.stderr
    assert not imported.exists()
    assert not called.exists()


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
    assert "software-only: device or HPX modules already loaded" in result.stderr
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
    assert " passed" in result.stdout
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


@pytest.mark.parametrize(
    "callback_argument", ["preexec_fn=callback", "positional", "preexec_fn=False"]
)
def test_preexec_rejected_before_native_launch(sandbox, callback_argument):
    arguments = "[sys.executable, '-c', 'pass'], " + callback_argument
    if callback_argument == "positional":
        arguments = "[sys.executable, '-c', 'pass'], -1, None, None, None, None, callback"
    source = f"""import subprocess, sys
from pathlib import Path
from unittest.mock import patch
guard = sys.modules['_hpx_software_guard']
def callback():
    raise AssertionError('CALLBACK SENTINEL')
def native_launch(self, *args, **kwargs):
    Path({str(sandbox[1])!r}).touch()
with patch.object(guard.GuardedPopen.__bases__[0], '__init__', native_launch):
    subprocess.Popen({arguments})
"""
    result = run_probe(sandbox, source)
    assert result.returncode != 0
    assert "software-only: preexec_fn callback blocked" in result.stderr
    assert not sandbox[1].exists()


@pytest.mark.parametrize("module", ["helia_profiler", "helia_profiler.example"])
def test_preloaded_hpx_diagnostic(sandbox, module):
    source = (
        "import sys, types\n"
        f"sys.modules[{module!r}] = types.ModuleType({module!r})\n"
        f"sys.path.insert(0, {str(ROOT / 'tools')!r})\n"
        "from software_guard.guard import install\ninstall()\n"
    )
    result = run_probe(sandbox, source, guarded=False)
    assert result.returncode != 0
    assert "device or HPX modules already loaded: ['helia_profiler']" in result.stderr
    assert not sandbox[1].exists()


@pytest.mark.parametrize(
    "prefix", ["[guard.BOOTSTRAP]", "[guard.BOOTSTRAP, unrelated, expected_source]"]
)
def test_child_source_prefix_cannot_be_omitted_or_displaced(sandbox, prefix):
    marker = sandbox[1]
    source = f"""import os, subprocess, sys
from pathlib import Path
guard = sys.modules['_hpx_software_guard']
unrelated = {str(sandbox[0])!r}
expected_source = {str(ROOT / "src")!r}
env = dict(os.environ, PYTHONPATH=os.pathsep.join({prefix}))
subprocess.run([sys.executable, '-c', {f"from pathlib import Path; Path({str(marker)!r}).touch()"!r}], env=env)
"""
    result = run_probe(sandbox, source)
    assert result.returncode != 0
    assert "software-only: child must inherit guarded PYTHONPATH" in result.stderr
    assert not marker.exists()


def test_launch_allowance_is_bound_to_its_process(sandbox):
    source = """import os, subprocess, sys
from unittest.mock import patch
guard = sys.modules['_hpx_software_guard']
parent_pid = os.getpid()
def inherited_init(self, *args, **kwargs):
    with patch.object(os, 'getpid', return_value=parent_pid + 1):
        for event, payload in [
            ('os.posix_spawn', (sys.executable, [], {})),
            ('subprocess.Popen', (None, 'python.exe -c pass', None, None)),
        ]:
            try:
                sys.audit(event, *payload)
            except guard.SoftwareOnlyViolation:
                pass
            else:
                raise AssertionError('another process inherited the launch allowance')
with patch.object(guard.GuardedPopen.__bases__[0], '__init__', inherited_init):
    subprocess.Popen([sys.executable, '-c', 'pass'])
"""
    result = run_probe(sandbox, source)
    assert result.returncode == 0, result.stderr
    assert not sandbox[1].exists()
