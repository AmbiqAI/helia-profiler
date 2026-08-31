"""Tests for cache-root resolution (cache_dirs) and the work-dir fallback.

The hardware-validation runner's service account has a read-only home, so
``~/.cache`` must be overridable (HPX_CACHE_DIR / XDG_CACHE_HOME) and an
unwritable default must degrade to a local ``.hpx-cache`` instead of
crashing every profile run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.hostenv.cache_dirs import hpx_cache_root
from helia_profiler.config import load_config
from helia_profiler.pipeline import _resolve_work_dir


class TestHpxCacheRoot:
    def test_default_is_home_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HPX_CACHE_DIR", raising=False)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        assert hpx_cache_root() == Path.home() / ".cache" / "helia-profiler"

    def test_xdg_cache_home_is_honored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HPX_CACHE_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert hpx_cache_root() == tmp_path / "xdg" / "helia-profiler"

    def test_hpx_cache_dir_wins_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "explicit"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        assert hpx_cache_root() == tmp_path / "explicit"


def _config(tmp_path: Path, **extra: object):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x1c\x00\x00\x00TFL3" + b"\x00" * 100)
    return load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "tflm"},
            "target": {"board": "apollo510_evb"},
            **extra,
        },
    )


class TestResolveWorkDirFallback:
    def test_cache_dir_override_selects_workspace_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
        wd, cleanup = _resolve_work_dir(_config(tmp_path))
        assert wd == tmp_path / "cache" / "workspaces" / "apollo510_evb-arm-none-eabi-gcc-tflm"
        assert wd.is_dir()
        assert cleanup is False

    def test_unwritable_cache_falls_back_to_local_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o555)
        if (readonly / "probe").exists() or _can_write(readonly):
            pytest.skip("filesystem does not enforce read-only directories")
        monkeypatch.setenv("HPX_CACHE_DIR", str(readonly / "cache"))
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        try:
            wd, cleanup = _resolve_work_dir(_config(tmp_path))
        finally:
            readonly.chmod(0o755)
        assert wd == cwd / ".hpx-cache" / "workspaces" / "apollo510_evb-arm-none-eabi-gcc-tflm"
        assert wd.is_dir()
        assert cleanup is False

    def test_explicit_work_dir_is_untouched_by_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path / "cache"))
        cfg = _config(tmp_path, work_dir=str(tmp_path / "explicit-work"))
        wd, cleanup = _resolve_work_dir(cfg)
        assert wd == (tmp_path / "explicit-work").resolve()
        assert cleanup is False


def _can_write(directory: Path) -> bool:
    try:
        probe = directory / "probe"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False
