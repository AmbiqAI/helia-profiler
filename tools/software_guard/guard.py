"""Deny device imports and unguarded process launches in software-only probes."""

from __future__ import annotations

import importlib.abc
import contextvars
import inspect
import os
import subprocess
import sys
import types
from pathlib import Path

DEVICE_MODULES = frozenset({"serial", "pylink", "pyjoulescope_driver", "joulescope", "usb"})
BOOTSTRAP = str(Path(__file__).resolve().parent)
_validated_launch = contextvars.ContextVar("hpx_validated_launch", default=False)
_popen_signature = inspect.signature(subprocess.Popen)


class SoftwareOnlyViolation(RuntimeError):
    """An operation needs a fake or an explicitly separate hardware workflow."""


def reject(*args, **kwargs):
    raise SoftwareOnlyViolation("software-only: device operation requires a fake")


class DeviceImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in DEVICE_MODULES:
            raise SoftwareOnlyViolation(f"software-only: device import blocked: {fullname}")
        return None


def _serial_stub() -> None:
    serial = types.ModuleType("serial")
    serial.__path__ = []
    serial.Serial = reject
    serial.serial_for_url = reject
    serial.SerialException = type("SerialException", (Exception,), {})
    tools = types.ModuleType("serial.tools")
    tools.__path__ = []
    ports = types.ModuleType("serial.tools.list_ports")
    ports.comports = reject
    ports.grep = reject
    serial.tools = tools
    tools.list_ports = ports
    sys.modules.update({"serial": serial, "serial.tools": tools, "serial.tools.list_ports": ports})


def _check_child(executable, arguments, cwd, environment) -> None:
    env = os.environ if environment is None else environment
    if not isinstance(arguments, (list, tuple)) or not arguments:
        raise SoftwareOnlyViolation("software-only: shell/string command blocked")
    if Path(os.fsdecode(executable)).absolute() != Path(sys.executable).absolute():
        raise SoftwareOnlyViolation("software-only: external command requires a fake")
    # Accept only the current interpreter with startup settings that retain sitecustomize.
    if arguments[0] != sys.executable:
        raise SoftwareOnlyViolation("software-only: alternate interpreter argv blocked")
    for arg in arguments[1:]:
        if arg in {"-c", "-m", "--"} or not arg.startswith("-"):
            break
        if arg not in {"-u", "-B"}:
            raise SoftwareOnlyViolation("software-only: unsupported Python startup option")
    if env.get("PYTHONPATH", "").split(os.pathsep)[0] != BOOTSTRAP:
        raise SoftwareOnlyViolation("software-only: child must inherit guarded PYTHONPATH")
    if env.get("PYTHONSAFEPATH") != "1":
        raise SoftwareOnlyViolation("software-only: child must inherit PYTHONSAFEPATH=1")


def _audit(event: str, args: tuple) -> None:
    if event == "subprocess.Popen":
        if not _validated_launch.get():
            _check_child(*args)
    elif event == "os.posix_spawn" and _validated_launch.get():
        return
    elif event in {"os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile"}:
        raise SoftwareOnlyViolation(f"software-only: process escape blocked: {event}")


class GuardedPopen(subprocess.Popen):
    """Validate argv before Windows converts it to a command-line string."""

    def __init__(self, *args, **kwargs):
        bound = _popen_signature.bind(*args, **kwargs).arguments
        argv = bound["args"]
        if bound.get("shell") or not isinstance(argv, (list, tuple)) or not argv:
            raise SoftwareOnlyViolation("software-only: shell/string command blocked")
        _check_child(bound.get("executable") or argv[0], argv, bound.get("cwd"), bound.get("env"))
        token = _validated_launch.set(True)
        try:
            super().__init__(*args, **kwargs)
        finally:
            _validated_launch.reset(token)


def install() -> None:
    """Install before importing any project or device module; children inherit it."""
    if "_hpx_software_guard" in sys.modules:
        return
    loaded = DEVICE_MODULES.intersection(name.split(".")[0] for name in sys.modules)
    if loaded or "helia_profiler" in sys.modules:
        raise SoftwareOnlyViolation(
            f"software-only: device modules already loaded: {sorted(loaded)}"
        )
    sys.meta_path.insert(0, DeviceImports())
    _serial_stub()
    previous = os.environ.get("PYTHONPATH", "")
    source = str(Path(__file__).resolve().parents[2] / "src")
    os.environ["PYTHONPATH"] = os.pathsep.join(filter(None, (BOOTSTRAP, source, previous)))
    os.environ["PYTHONSAFEPATH"] = "1"
    sys.addaudithook(_audit)
    subprocess.Popen = GuardedPopen
    sys.modules["_hpx_software_guard"] = sys.modules[__name__]
