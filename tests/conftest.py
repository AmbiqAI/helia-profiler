"""Shared test fixtures for heliaPROFILER."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.engines.helia_rt import HELIART_VERSION

# Path to the test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"
KWS_MODEL_PATH = FIXTURES_DIR / "kws_ref_model.tflite"


@pytest.fixture()
def kws_model() -> Path:
    """Return the path to the KWS reference model (.tflite).

    This is a real quantised keyword-spotting model (~53 KB) used for
    end-to-end firmware generation testing.
    """
    assert KWS_MODEL_PATH.exists(), f"KWS model fixture not found: {KWS_MODEL_PATH}"
    return KWS_MODEL_PATH


@pytest.fixture()
def fake_dist(tmp_path: Path) -> Path:
    """Create a minimal fake heliaRT distribution directory."""
    dist = tmp_path / "heliart_dist"
    dist.mkdir()
    (dist / "lib").mkdir()
    # Cover every (core, toolchain, variant) combo the test suite touches so
    # that the adapter's prebuilt-archive verification check passes.
    for core in ("cm4", "cm55"):
        for tc in ("gcc", "armclang"):
            for variant in ("release", "release-with-logs", "debug"):
                (
                    dist / "lib" / f"libhelia-rt-{core}-{tc}-{variant}.a"
                ).write_bytes(b"\x00")
    tf_dir = dist / "tensorflow" / "lite" / "micro"
    tf_dir.mkdir(parents=True)
    (tf_dir / "heliart_version.h").write_text(
        f'#define HELIART_VERSION "v{HELIART_VERSION}"\n'
    )
    # Stub source files referenced by CMakeLists.txt
    (tf_dir / "micro_log.cc").write_text("// stub\n")
    cortex_dir = tf_dir / "cortex_m_generic"
    cortex_dir.mkdir()
    (cortex_dir / "debug_log.cc").write_text("// stub\n")
    (dist / "third_party").mkdir()
    (dist / "third_party" / "flatbuffers").mkdir()
    (dist / "signal").mkdir()
    # Copy the bundled test snapshot of heliaRT's nsx/ module into the dist
    # so that _install_nsx_module finds the upstream-style files. This
    # mirrors what real heliaRT >= 1.12.2 release zips ship.
    import shutil
    nsx_src = FIXTURES_DIR / "heliart_nsx"
    nsx_dst = dist / "nsx"
    nsx_dst.mkdir()
    shutil.copy2(nsx_src / "CMakeLists.txt", nsx_dst / "CMakeLists.txt")
    shutil.copy2(nsx_src / "nsx-module.yaml", nsx_dst / "nsx-module.yaml")
    return dist
