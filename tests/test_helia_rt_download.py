"""heliaRT release download: archive containment and atomic cache install."""

from __future__ import annotations

import io
import zipfile
from http.client import IncompleteRead
from pathlib import Path

import pytest

from helia_profiler.engines.helia_rt import download
from helia_profiler.engines.helia_rt.artifacts import _DIST_DIRS, _is_valid_dist
from helia_profiler.errors import EngineError

URL = "https://example.invalid/helia-rt.zip"


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _dist_members(prefix: str = "helia-rt/") -> dict[str, bytes]:
    return {f"{prefix}{d}/keep.txt": b"x" for d in _DIST_DIRS}


_Resp = io.BytesIO


class _IncompleteResp(io.BytesIO):
    def read(self, *args):
        raise IncompleteRead(b"PK\x03\x04", 1024)


def _serve(monkeypatch: pytest.MonkeyPatch, resp: io.BytesIO) -> None:
    monkeypatch.setattr(download, "urlopen", lambda req, timeout: resp)


def _leftovers(dest: Path) -> list[Path]:
    return [p for p in dest.parent.iterdir() if p != dest]


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "dist"


def test_valid_archive_installs_and_strips_top_dir(dest, monkeypatch):
    _serve(monkeypatch, _Resp(_zip(_dist_members())))

    download._download_and_extract(URL, dest)

    assert _is_valid_dist(dest)
    assert (dest / "lib" / "keep.txt").read_bytes() == b"x"
    assert _leftovers(dest) == []


@pytest.mark.parametrize(
    "evil",
    ["../escaped.txt", "helia-rt/../../escaped.txt", "ABSOLUTE"],
)
def test_traversal_member_is_rejected(tmp_path, dest, monkeypatch, evil):
    target = tmp_path / "abs-escaped.txt"
    if evil == "ABSOLUTE":
        evil = target.as_posix()
    members = _dist_members()
    members[evil] = b"pwned"
    _serve(monkeypatch, _Resp(_zip(members)))

    with pytest.raises(EngineError, match="escapes the extraction directory"):
        download._download_and_extract(URL, dest)

    assert not dest.exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert not (tmp_path / "cache" / "escaped.txt").exists()
    assert not target.exists()
    assert _leftovers(dest) == []


def test_interrupted_extract_leaves_no_cache(dest, monkeypatch):
    _serve(monkeypatch, _Resp(_zip(_dist_members())))
    real_read = zipfile.ZipFile.read
    calls = {"n": 0}

    def flaky_read(self, member, pwd=None):
        calls["n"] += 1
        if calls["n"] == len(_DIST_DIRS):
            raise OSError("disk full")
        return real_read(self, member, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", flaky_read)

    with pytest.raises(OSError, match="disk full"):
        download._download_and_extract(URL, dest)

    assert not dest.exists()
    assert _leftovers(dest) == []


def test_incomplete_dist_is_not_installed(dest, monkeypatch):
    members = _dist_members()
    members.pop(f"helia-rt/{_DIST_DIRS[-1]}/keep.txt")
    _serve(monkeypatch, _Resp(_zip(members)))

    with pytest.raises(EngineError, match="missing"):
        download._download_and_extract(URL, dest)

    assert not dest.exists()
    assert _leftovers(dest) == []


@pytest.mark.parametrize(
    "resp",
    [_IncompleteResp(), _Resp(_zip(_dist_members())[:200])],
    ids=["incomplete-read", "truncated-body"],
)
def test_partial_download_raises_engine_error(dest, monkeypatch, resp):
    _serve(monkeypatch, resp)

    with pytest.raises(EngineError):
        download._download_and_extract(URL, dest)

    assert not dest.exists()


def test_stale_partial_cache_is_replaced(dest, monkeypatch):
    _serve(monkeypatch, _Resp(_zip(_dist_members())))
    (dest / "lib").mkdir(parents=True)
    (dest / "junk.txt").write_text("stale")

    download._download_and_extract(URL, dest)

    assert _is_valid_dist(dest)
    assert not (dest / "junk.txt").exists()


def test_concurrently_installed_dist_wins(dest, monkeypatch):
    _serve(monkeypatch, _Resp(_zip(_dist_members())))
    for d in _DIST_DIRS:
        (dest / d).mkdir(parents=True)
    (dest / "winner.txt").write_text("first")

    download._download_and_extract(URL, dest)

    assert (dest / "winner.txt").read_text() == "first"
    assert _leftovers(dest) == []


def test_corrupt_deflate_member_raises_engine_error(dest, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in _dist_members().items():
            zf.writestr(name, payload * 100)
    data = bytearray(buf.getvalue())
    info = zipfile.ZipFile(io.BytesIO(bytes(data))).infolist()[0]
    # First byte of the deflate stream; 0xFF encodes the reserved block type.
    data[info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)] = 0xFF
    _serve(monkeypatch, _Resp(bytes(data)))

    with pytest.raises(EngineError, match="Corrupt entry"):
        download._download_and_extract(URL, dest)

    assert not dest.exists()
    assert _leftovers(dest) == []


def test_stale_cache_removed_concurrently_is_tolerated(dest, monkeypatch):
    _serve(monkeypatch, _Resp(_zip(_dist_members())))
    (dest / "lib").mkdir(parents=True)
    real_rename = Path.rename

    def racing_rename(self, target):
        if self == dest:
            (dest / "lib").rmdir()
            dest.rmdir()
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", racing_rename)

    download._download_and_extract(URL, dest)

    assert _is_valid_dist(dest)
    assert _leftovers(dest) == []


@pytest.mark.parametrize(
    ("repo", "ref"),
    [
        ("AmbiqAI/helia-rt", "../../../victim"),
        ("../../victim", "v1"),
        ("AmbiqAI/helia-rt", "..\\..\\victim"),
        ("AmbiqAI/helia-rt", "/abs/victim"),
    ],
)
def test_cache_key_stays_under_cache_root(tmp_path, repo, ref):
    key = download._cache_key(repo, ref)

    assert (tmp_path / key).resolve().parent == tmp_path.resolve()


def test_cache_key_is_stable_for_ordinary_refs():
    assert download._cache_key("AmbiqAI/helia-rt", "helia-rt-v1.16.0") == (
        "AmbiqAI_helia-rt_helia-rt-v1.16.0"
    )


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (("AmbiqAI/helia-rt", "release/x"), ("AmbiqAI/helia-rt", "release_x")),
        (("AmbiqAI/helia-rt", "release/x"), ("AmbiqAI/helia-rt", "release:x")),
        (("AmbiqAI/helia_rt", "v1"), ("AmbiqAI/helia", "rt_v1")),
        (("A/b_c", "d"), ("A/b", "c_d")),
    ],
)
def test_cache_key_distinct_inputs_never_collide(a, b):
    assert download._cache_key(*a) != download._cache_key(*b)


def test_sanitized_key_cannot_equal_plain_key():
    sanitized = download._cache_key("AmbiqAI/helia-rt", "release/x")

    assert "+" in sanitized
    assert "+" not in download._cache_key("AmbiqAI/helia-rt", "release_x")
