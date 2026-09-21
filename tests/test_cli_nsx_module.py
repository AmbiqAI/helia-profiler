import subprocess
import sys


def _run_hpx(*args: str) -> subprocess.CompletedProcess[str]:
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
    def test_missing_colon(self):
        r = _run_hpx("--nsx-module", "nsx-core")
        assert r.returncode != 0
        assert "NAME:KEY=VALUE" in r.stderr

    def test_missing_equals(self):
        r = _run_hpx("--nsx-module", "nsx-core:pathonly")
        assert r.returncode != 0
        assert "KEY=VALUE" in r.stderr

    def test_invalid_key(self):
        r = _run_hpx("--nsx-module", "nsx-core:branch=main")
        assert r.returncode != 0
        assert "'path', 'ref', or 'version'" in r.stderr

    def test_valid_path(self):
        """Only checks parse-time success; missing-model failure comes later."""
        r = _run_hpx("--nsx-module", "nsx-core:path=/tmp/nsx-core")
        assert "NAME:KEY=VALUE" not in r.stderr
        assert "KEY=VALUE" not in r.stderr

    def test_valid_ref(self):
        r = _run_hpx("--nsx-module", "nsx-cmsis-core:ref=feat/new-cmsis")
        assert "NAME:KEY=VALUE" not in r.stderr

    def test_valid_version(self):
        r = _run_hpx("--nsx-module", "nsx-gpio:version=2.0.0")
        assert "NAME:KEY=VALUE" not in r.stderr
