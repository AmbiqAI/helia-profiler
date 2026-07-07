from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
import sys

import pytest

from helia_profiler.cli import inspect_cmds as cli
from helia_profiler.errors import CaptureError
from helia_profiler.target.probe.jlink import (
    JLinkProbe,
    JLinkProbeMatch,
    create_debug_memory_session,
)
from helia_profiler.platform import CoreArch


def test_probes_list_prints_connected_probes(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.list_connected_probes",
        lambda: [JLinkProbe(serial="1160002204", product="J-Link OB", connection="USB")],
    )

    cli._cmd_probes_list(Namespace(board=None, inspect=False, json=False))

    out = capsys.readouterr().out
    assert "1160002204" in out
    assert "J-Link OB" in out


def test_probes_list_inspects_against_board(monkeypatch, capsys) -> None:
    probe = JLinkProbe(serial="1160002204", product="J-Link OB", connection="USB")
    monkeypatch.setattr("helia_profiler.target.probe.jlink.list_connected_probes", lambda: [probe])
    monkeypatch.setattr(
        "helia_profiler.target.probe.jlink.inspect_probe_target",
        lambda probe, *, device: JLinkProbeMatch(probe=probe, detected_core=CoreArch.CORTEX_M55),
    )

    cli._cmd_probes_list(Namespace(board="apollo510_evb", inspect=True, json=False))

    out = capsys.readouterr().out
    assert "cortex-m55" in out
    assert "yes" in out


def test_probes_match_prints_resolved_serial(monkeypatch, capsys) -> None:
    monkeypatch.setattr("helia_profiler.target.probe.jlink.resolve_probe_serial", lambda **kwargs: "1160002204")

    cli._cmd_probes_match(
        Namespace(board="apollo510_evb", jlink_serial=None, json=False)
    )

    assert "apollo510_evb: 1160002204" in capsys.readouterr().out


def test_target_reset_uses_noninteractive_wrapper(monkeypatch, capsys) -> None:
    calls: list[dict[str, str | None]] = []

    def fake_reset_target(*, device: str, jlink_serial: str | None = None) -> None:
        calls.append({"device": device, "jlink_serial": jlink_serial})

    monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", fake_reset_target)

    cli._cmd_target_reset(
        Namespace(board="apollo4p_blue_kxr_evb", jlink_serial="1160001481", kind="debug")
    )

    assert calls == [{"device": "AMAP42KP-KBR", "jlink_serial": "1160001481"}]
    assert "Reset apollo4p_blue_kxr_evb" in capsys.readouterr().out


def test_ports_list_classifies_jlink_and_hpx_cdc(monkeypatch, capsys) -> None:
    ports = [
        SimpleNamespace(
            device="/dev/ttyACM0",
            description="J-Link VCOM",
            manufacturer="SEGGER",
            product="J-Link",
            serial_number="1160000174",
            interface="",
            hwid="USB VID:PID=1366:0105",
        ),
        SimpleNamespace(
            device="/dev/ttyACM1",
            description="TinyUSB CDC",
            manufacturer="Ambiq",
            product="HPX CDC",
            serial_number="HPX-1160002204",
            interface="",
            hwid="USB VID:PID=2AEC:6010",
        ),
    ]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: ports)

    cli._cmd_ports_list(Namespace(json=False, show_all=False))

    out = capsys.readouterr().out
    assert "jlink-vcom" in out
    assert "hpx-usb-cdc" in out


def test_ports_list_hides_builtin_ttys_unless_all(monkeypatch, capsys) -> None:
    ports = [
        SimpleNamespace(
            device="/dev/ttyS0",
            description="n/a",
            manufacturer="",
            product="",
            serial_number="",
            interface="",
            hwid="n/a",
        ),
        SimpleNamespace(
            device="/dev/ttyACM0",
            description="J-Link VCOM",
            manufacturer="SEGGER",
            product="J-Link",
            serial_number="1160000174",
            interface="",
            hwid="USB VID:PID=1366:1024",
        ),
    ]
    monkeypatch.setattr("serial.tools.list_ports.comports", lambda: ports)

    cli._cmd_ports_list(Namespace(json=False, show_all=False))
    out = capsys.readouterr().out
    assert "/dev/ttyACM0" in out
    assert "/dev/ttyS0" not in out

    cli._cmd_ports_list(Namespace(json=False, show_all=True))
    out = capsys.readouterr().out
    assert "/dev/ttyS0" in out


def test_probe_cli_reports_hpx_errors(monkeypatch, capsys) -> None:
    def fail() -> list[JLinkProbe]:
        raise CaptureError("JLinkExe not found", hint="install SEGGER tools")

    monkeypatch.setattr("helia_profiler.target.probe.jlink.list_connected_probes", fail)

    with pytest.raises(SystemExit) as exc_info:
        cli._cmd_probes_list(Namespace(board=None, inspect=False, json=False))

    assert exc_info.value.code == 1
    assert "JLinkExe not found" in capsys.readouterr().err


def test_create_debug_memory_session_uses_default_pylink_first(monkeypatch) -> None:
    calls: list[str] = []

    class FakeJLink:
        def __init__(self, lib=None):
            assert lib is None
            calls.append("default")

    fake_pylink = SimpleNamespace(JLink=FakeJLink)
    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)

    session = create_debug_memory_session()

    assert isinstance(session, FakeJLink)
    assert calls == ["default"]


def test_create_debug_memory_session_falls_back_to_jlinkexe_wrapper_dll(
    tmp_path, monkeypatch
) -> None:
    dll_dir = tmp_path / "JLink_V874"
    dll_dir.mkdir()
    if sys.platform.startswith("win"):
        dll = dll_dir / "JLink_x64.dll"
    elif sys.platform.startswith("darwin"):
        dll = dll_dir / "libjlinkarm.dylib"
    else:
        dll = dll_dir / "libjlinkarm.so"
    dll.write_bytes(b"fake")
    wrapper = tmp_path / "JLinkExe"
    wrapper.write_text(f"export DYLD_LIBRARY_PATH='{dll_dir}'\n")

    loaded: list[str] = []

    class FakeLibrary:
        def __init__(self, path):
            loaded.append(path)

        def dll(self):
            return object()

    class FakeJLink:
        def __init__(self, lib=None):
            if lib is None:
                raise TypeError("Expected to be given a valid DLL.")
            self.lib = lib

    fake_pylink = SimpleNamespace(
        JLink=FakeJLink,
        library=SimpleNamespace(Library=FakeLibrary),
    )
    monkeypatch.setitem(sys.modules, "pylink", fake_pylink)
    monkeypatch.setitem(sys.modules, "pylink.library", fake_pylink.library)
    monkeypatch.setattr("helia_profiler.target.probe.jlink.find_jlink_exe", lambda: str(wrapper))

    session = create_debug_memory_session()

    assert isinstance(session, FakeJLink)
    assert session.lib is not None
    assert loaded == [str(dll.resolve())]
