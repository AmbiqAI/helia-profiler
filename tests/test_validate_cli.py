"""Unit tests for the hpx validate CLI surface (no hardware required)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

HPX = shutil.which("hpx")

requires_hpx = pytest.mark.skipif(
    HPX is None,
    reason="`hpx` console script not on PATH (install heliaPROFILER first)",
)


def _run_hpx(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [HPX, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@requires_hpx
class TestValidateList:
    def test_list_default_shows_full_matrix(self):
        proc = _run_hpx("validate", "--list")
        assert proc.returncode == 0, proc.stderr
        assert "16 case(s) would run" in proc.stdout
        assert "kws" in proc.stdout
        assert "vww" in proc.stdout
        assert "ic" in proc.stdout
        assert "ad" in proc.stdout

    def test_list_engine_alias_aot(self):
        proc = _run_hpx("validate", "--list", "--engines", "aot", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "4 case(s)" in proc.stdout
        assert "helia-aot" in proc.stdout

    def test_list_power_off(self):
        proc = _run_hpx("validate", "--list", "--power", "off")
        assert proc.returncode == 0, proc.stderr
        assert "8 case(s)" in proc.stdout

    def test_list_unknown_model_fails(self):
        proc = _run_hpx("validate", "--list", "--models", "nope")
        assert proc.returncode != 0
        assert "Unknown model" in proc.stderr

    def test_list_unknown_engine_fails(self):
        proc = _run_hpx("validate", "--list", "--engines", "tflite")
        assert proc.returncode != 0
        assert "unknown engine" in proc.stderr.lower()

    def test_help_mentions_validate(self):
        proc = _run_hpx("--help")
        assert proc.returncode == 0
        assert "validate" in proc.stdout
