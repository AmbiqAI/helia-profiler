"""Tests for the nsx CLI wrapper — error translation and timeout threading."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from helia_profiler import nsx
from helia_profiler.errors import BuildError


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=["nsx"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestNsxBuild:
    def test_success_returns_stdout(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed(stdout="ok")) as run_mock:
            out = nsx.build(tmp_path)
        assert out == "ok"
        call = run_mock.call_args
        assert call.kwargs["timeout"] == 300

    def test_build_respects_explicit_timeout(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed(stdout="")) as run_mock:
            nsx.build(tmp_path, timeout_s=42)
        assert run_mock.call_args.kwargs["timeout"] == 42

    def test_nonzero_exit_raises_build_error(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed(stderr="boom", returncode=2)):
            with pytest.raises(BuildError) as exc_info:
                nsx.build(tmp_path)
        err = exc_info.value
        assert "nsx build" in str(err)
        assert err.returncode == 2
        assert "boom" in (err.stderr or "")

    def test_timeout_raises_build_error_with_hint(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch(
                    "subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="nsx", timeout=10),
                ):
            with pytest.raises(BuildError) as exc_info:
                nsx.build(tmp_path, timeout_s=10)
        assert "timed out after 10s" in str(exc_info.value)
        assert exc_info.value.hint is not None

    def test_missing_binary_raises_build_error(self, tmp_path: Path):
        with patch("shutil.which", return_value=None):
            with pytest.raises(BuildError, match="nsx CLI not found"):
                nsx.build(tmp_path)

    def test_missing_binary_hint_mentions_install(self, tmp_path: Path):
        with patch("shutil.which", return_value=None):
            with pytest.raises(BuildError) as exc_info:
                nsx.build(tmp_path)
        assert exc_info.value.hint is not None
        assert "neuralspotx" in exc_info.value.hint


class TestNsxFlash:
    def test_flash_sets_sncode_env(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed()) as run_mock:
            nsx.flash(tmp_path, jlink_serial="123456", timeout_s=99)
        kwargs = run_mock.call_args.kwargs
        assert kwargs["env"]["SEGGER_SNCODE"] == "123456"
        assert kwargs["timeout"] == 99

    def test_flash_no_serial_no_env_override(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed()) as run_mock:
            nsx.flash(tmp_path)
        assert run_mock.call_args.kwargs["env"] is None


class TestNsxConfigure:
    def test_configure_uses_default_timeout(self, tmp_path: Path):
        with patch("shutil.which", return_value="/usr/bin/nsx"), \
                patch("subprocess.run", return_value=_fake_completed()) as run_mock:
            nsx.configure(tmp_path)
        assert run_mock.call_args.kwargs["timeout"] == 120
