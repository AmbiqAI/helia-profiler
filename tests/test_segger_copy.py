"""App-local RTT sources can be regenerated from immutable package data."""

import os
from pathlib import Path
from stat import S_IWUSR

import pytest

from helia_profiler.firmware.segger import _copy_segger_rtt


@pytest.mark.parametrize("existing_readonly", [False, True])
def test_regenerate_from_readonly_sources(tmp_path: Path, existing_readonly: bool) -> None:
    source = tmp_path / "source"
    app = tmp_path / "app"
    contents = {
        "RTT/SEGGER_RTT.c": b"SEGGER_RTT_PUT_CB_SECTION(cb);\r\n"
        b"SEGGER_RTT_PUT_BUFFER_SECTION(buffer);\r\n",
        "RTT/SEGGER_RTT.h": b"// header\r\n",
        "RTT/SEGGER_RTT_ConfDefaults.h": b"// defaults\r\n",
        "Config/SEGGER_RTT_Conf.h": b"// config\n",
    }
    sources = [source / name for name in contents]
    destinations = [app / "rtt" / name.removeprefix("RTT/") for name in contents]
    try:
        for name, data in contents.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o444)
        source_state = [
            (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) for path in sources
        ]
        if existing_readonly:
            for path in destinations:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"// obsolete app-local copy\n")
                path.chmod(0o444)

        _copy_segger_rtt(app, source)

        for name, path in zip(contents, destinations, strict=True):
            assert path.stat().st_mode & S_IWUSR
            if name.startswith("RTT/"):
                assert path.read_bytes() == contents[name]
        config = destinations[-1].read_text(encoding="utf-8")
        assert config.startswith(contents["Config/SEGGER_RTT_Conf.h"].decode("utf-8"))
        assert "SEGGER_RTT_SECTION" in config
        for path in destinations:
            os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        mtimes = [path.stat().st_mtime_ns for path in destinations]

        _copy_segger_rtt(app, source)

        assert [path.stat().st_mtime_ns for path in destinations] == mtimes
        assert [
            (path.read_bytes(), path.stat().st_mode, path.stat().st_mtime_ns) for path in sources
        ] == source_state

        for path in sources:
            path.chmod(0o644)
            path.write_bytes(path.read_bytes() + b"// upstream change\n")
            path.chmod(0o444)
        _copy_segger_rtt(app, source)
        for path in destinations:
            assert b"// upstream change\n" in path.read_bytes()
    finally:
        for path in sources + destinations:
            if path.exists():
                path.chmod(0o644)
