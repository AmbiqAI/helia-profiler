from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path


MAX_SOURCE_LINES = 1000


def test_source_modules_stay_below_size_ceiling() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    oversized = {
        path.relative_to(repo_root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in (repo_root / "src" / "helia_profiler").rglob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_SOURCE_LINES
    }

    assert not oversized, (
        f"Source modules exceed {MAX_SOURCE_LINES} lines; extract a cohesive responsibility: "
        f"{oversized}"
    )


def test_wheel_contains_only_canonical_evaluation_modules(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    wheel_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(wheel_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "helia_profiler/evaluation/validity.py" in names
    assert "helia_profiler/evaluation/comparability.py" in names
    assert "helia_profiler/evaluation/comparison_profile.py" in names
    assert "helia_profiler/evaluation/compare.py" in names
    assert "helia_profiler/evaluation/model_analysis.py" in names
    assert "helia_profiler/results/models.py" in names
    assert "helia_profiler/results/artifacts.py" in names
    assert "helia_profiler/results/manifest.py" in names
    assert "helia_profiler/validity.py" not in names
    assert "helia_profiler/comparability.py" not in names
    assert "helia_profiler/comparison_profile.py" not in names
    assert "helia_profiler/compare.py" not in names
    assert "helia_profiler/model_analysis.py" not in names
    assert "helia_profiler/results.py" not in names
    assert "helia_profiler/artifacts.py" not in names
    assert "helia_profiler/result_manifest.py" not in names
    assert "helia_profiler/data/run_summary.schema.v1.json" in names
    assert "helia_profiler/data/run_metadata.schema.v1.json" in names
    assert "helia_profiler/data/profile_results.schema.v1.json" in names
    assert "helia_profiler/data/session_intent.schema.v1.json" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.c" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.h" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT_ConfDefaults.h" in names
