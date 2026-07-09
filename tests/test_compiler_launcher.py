"""Tests for the optional CMake compiler launcher (sccache/ccache) support.

Covers config parsing of ``build.compiler_launcher`` and the firmware-side
resolver ``_resolve_compiler_launcher`` (auto-detect, explicit, disabled, env
override, and the not-found error path).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helia_profiler.config import BuildConfig
from helia_profiler.errors import ConfigError, FirmwareError
from helia_profiler.firmware import _resolve_compiler_launcher


def _config_with_launcher(value: str, toolchain: str = "arm-none-eabi-gcc") -> SimpleNamespace:
    """Minimal stand-in exposing ``config.build.compiler_launcher`` and toolchain."""
    return SimpleNamespace(
        build=SimpleNamespace(compiler_launcher=value),
        target=SimpleNamespace(toolchain=toolchain),
    )


class TestBuildConfigParsing:
    def test_defaults_to_auto(self):
        assert BuildConfig().compiler_launcher == "auto"

    def test_explicit_tool(self):
        cfg = BuildConfig(compiler_launcher="sccache")
        assert cfg.compiler_launcher == "sccache"

    def test_false_maps_to_none(self):
        cfg = BuildConfig(compiler_launcher=False)
        assert cfg.compiler_launcher == "none"

    def test_null_maps_to_none(self):
        cfg = BuildConfig(compiler_launcher=None)
        assert cfg.compiler_launcher == "none"

    def test_non_string_rejected(self):
        with pytest.raises(ConfigError):
            BuildConfig(compiler_launcher=123)


class TestResolveCompilerLauncher:
    def test_disabled_values_return_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        for value in ("none", "off", "false", "disabled", ""):
            assert _resolve_compiler_launcher(_config_with_launcher(value)) is None

    def test_auto_returns_none_when_nothing_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr("helia_profiler.firmware.shutil.which", lambda _name: None)
        assert _resolve_compiler_launcher(_config_with_launcher("auto")) is None

    def test_auto_prefers_sccache(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        found = {"sccache": "/usr/bin/sccache", "ccache": "/usr/bin/ccache"}
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which", lambda name: found.get(name)
        )
        result = _resolve_compiler_launcher(_config_with_launcher("auto"))
        assert result == "/usr/bin/sccache"

    def test_auto_falls_back_to_ccache(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        found = {"ccache": "/usr/bin/ccache"}
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which", lambda name: found.get(name)
        )
        result = _resolve_compiler_launcher(_config_with_launcher("auto"))
        assert result == "/usr/bin/ccache"

    def test_explicit_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which",
            lambda name: "/usr/bin/sccache" if name == "sccache" else None,
        )
        result = _resolve_compiler_launcher(_config_with_launcher("sccache"))
        assert result == "/usr/bin/sccache"

    def test_explicit_missing_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr("helia_profiler.firmware.shutil.which", lambda _name: None)
        with pytest.raises(FirmwareError):
            _resolve_compiler_launcher(_config_with_launcher("sccache"))


class TestLauncherToolchainCompatibility:
    """sccache cannot wrap armclang; auto-detect must skip it and explicit
    requests must be disabled (with a warning) rather than break the build."""

    def test_auto_skips_sccache_for_armclang(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        found = {"sccache": "/usr/bin/sccache", "ccache": "/usr/bin/ccache"}
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which", lambda name: found.get(name)
        )
        result = _resolve_compiler_launcher(
            _config_with_launcher("auto", toolchain="armclang")
        )
        assert result == "/usr/bin/ccache"

    def test_auto_returns_none_when_only_sccache_and_armclang(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which",
            lambda name: "/usr/bin/sccache" if name == "sccache" else None,
        )
        result = _resolve_compiler_launcher(
            _config_with_launcher("auto", toolchain="armclang")
        )
        assert result is None

    def test_auto_keeps_sccache_for_gcc(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        found = {"sccache": "/usr/bin/sccache", "ccache": "/usr/bin/ccache"}
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which", lambda name: found.get(name)
        )
        result = _resolve_compiler_launcher(
            _config_with_launcher("auto", toolchain="arm-none-eabi-gcc")
        )
        assert result == "/usr/bin/sccache"

    def test_explicit_sccache_disabled_for_armclang(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which",
            lambda name: "/usr/bin/sccache" if name == "sccache" else None,
        )
        result = _resolve_compiler_launcher(
            _config_with_launcher("sccache", toolchain="armclang")
        )
        assert result is None

    def test_explicit_ccache_allowed_for_armclang(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.delenv("HPX_COMPILER_LAUNCHER", raising=False)
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which",
            lambda name: "/usr/bin/ccache" if name == "ccache" else None,
        )
        result = _resolve_compiler_launcher(
            _config_with_launcher("ccache", toolchain="armclang")
        )
        assert result == "/usr/bin/ccache"

    def test_env_overrides_config(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HPX_COMPILER_LAUNCHER", "none")
        # Config says auto + sccache present, but env disables it.
        monkeypatch.setattr(
            "helia_profiler.firmware.shutil.which", lambda _name: "/usr/bin/sccache"
        )
        assert _resolve_compiler_launcher(_config_with_launcher("auto")) is None
