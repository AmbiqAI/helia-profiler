"""App-local RTT sources can be regenerated from immutable package data."""

import os
from pathlib import Path
from stat import S_IWUSR

import pytest

from helia_profiler.errors import FirmwareError
from helia_profiler.firmware.segger import _bundled_segger_rtt_dir, _copy_segger_rtt


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


@pytest.mark.parametrize("link_type", ["symlink", "hardlink"])
@pytest.mark.parametrize("cross_file", [False, True])
@pytest.mark.parametrize(
    "aliased",
    [
        "RTT/SEGGER_RTT.c",
        "RTT/SEGGER_RTT.h",
        "RTT/SEGGER_RTT_ConfDefaults.h",
        "Config/SEGGER_RTT_Conf.h",
    ],
)
def test_destination_alias_does_not_modify_source(
    tmp_path: Path, link_type: str, cross_file: bool, aliased: str
) -> None:
    source = tmp_path / "source"
    app = tmp_path / "app"
    paths = [
        "RTT/SEGGER_RTT.c",
        "RTT/SEGGER_RTT.h",
        "RTT/SEGGER_RTT_ConfDefaults.h",
        "Config/SEGGER_RTT_Conf.h",
    ]
    try:
        for name in paths:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((_bundled_segger_rtt_dir() / name).read_bytes())
            path.chmod(0o444)
        before = [((source / name).read_bytes(), (source / name).stat().st_mode) for name in paths]
        dest = app / "rtt" / aliased.removeprefix("RTT/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        link_source = source / ("RTT/SEGGER_RTT.h" if cross_file else aliased)
        if cross_file and aliased == "RTT/SEGGER_RTT.h":
            link_source = source / "RTT/SEGGER_RTT.c"
        try:
            if link_type == "symlink":
                dest.symlink_to(link_source)
            else:
                dest.hardlink_to(link_source)
        except (OSError, NotImplementedError) as error:
            pytest.skip(f"{link_type} unavailable: {error}")

        with pytest.raises(FirmwareError):
            _copy_segger_rtt(app, source)

        assert [
            ((source / name).read_bytes(), (source / name).stat().st_mode) for name in paths
        ] == before
    finally:
        for name in paths:
            path = source / name
            if path.exists():
                path.chmod(0o644)


def test_fresh_copies_are_writable_with_restrictive_umask(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "rtt" / "Config").mkdir(parents=True)
    previous = os.umask(0o222)
    try:
        _copy_segger_rtt(app, _bundled_segger_rtt_dir())
        files = [path for path in (app / "rtt").rglob("*") if path.is_file()]
        assert len(files) == 4
        assert all(path.stat().st_mode & S_IWUSR for path in files)
        _copy_segger_rtt(app, _bundled_segger_rtt_dir())
    finally:
        os.umask(previous)
        for path in (app / "rtt").rglob("*"):
            if path.is_file():
                path.chmod(0o644)
