"""Tests for --nsx-module CLI argument parsing."""

import subprocess
import sys


def _run_hpx(*args: str) -> subprocess.CompletedProcess[str]:
    """Run hpx profile with given extra args, expecting failure."""
    cmd = [
        sys.executable,
        "-c",
        "from helia_profiler.cli import main; main()",
        "profile",
        "fake.tflite",
        *args,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


class TestNsxModuleParsing:
    """Validate --nsx-module format enforcement."""

    def test_missing_colon(self):
        """Should reject --nsx-module without NAME:KEY=VALUE format."""
        r = _run_hpx("--nsx-module", "nsx-core")
        assert r.returncode != 0
        assert "NAME:KEY=VALUE" in r.stderr

    def test_missing_equals(self):
        """Should reject value part without KEY=VALUE."""
        r = _run_hpx("--nsx-module", "nsx-core:pathonly")
        assert r.returncode != 0
        assert "KEY=VALUE" in r.stderr

    def test_invalid_key(self):
        """Should reject unrecognized key."""
        r = _run_hpx("--nsx-module", "nsx-core:branch=main")
        assert r.returncode != 0
        assert "'path', 'ref', or 'version'" in r.stderr

    def test_valid_path(self):
        """Valid --nsx-module path=... should not fail at parse time.

        It will fail later (no model file), but not at arg-parsing stage.
        """
        r = _run_hpx("--nsx-module", "nsx-core:path=/tmp/nsx-core")
        # Should get past parsing — will fail on missing model, not parsing
        assert "NAME:KEY=VALUE" not in r.stderr
        assert "KEY=VALUE" not in r.stderr

    def test_valid_ref(self):
        """Valid --nsx-module ref=... should not fail at parse time."""
        r = _run_hpx("--nsx-module", "nsx-cmsis-core:ref=feat/new-cmsis")
        assert "NAME:KEY=VALUE" not in r.stderr

    def test_valid_version(self):
        """Valid --nsx-module version=... should not fail at parse time."""
        r = _run_hpx("--nsx-module", "nsx-gpio:version=2.0.0")
        assert "NAME:KEY=VALUE" not in r.stderr
