"""Shared test fixtures for heliaPROFILER."""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.engines.helia_rt import HELIART_VERSION

# Path to the test fixtures directory
FIXTURES_DIR = Path(__file__).parent / "fixtures"
KWS_MODEL_PATH = FIXTURES_DIR / "kws_ref_model.tflite"


@pytest.fixture(autouse=True)
def _isolate_engine_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear ambient engine-resolution env vars so tests are deterministic.

    Without this, an interactive shell that exported HELIART_SOURCE_PATH
    (etc.) for a real board run will leak into the test process and flip
    code paths under test.
    """
    for name in (
        "HELIART_SOURCE_PATH",
        "HELIART_DIST_PATH",
        "CMSIS_NN_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


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
                (dist / "lib" / f"libhelia-rt-{core}-{tc}-{variant}.a").write_bytes(b"\x00")
    tf_dir = dist / "tensorflow" / "lite" / "micro"
    tf_dir.mkdir(parents=True)
    (tf_dir / "helia_rt_version.h").write_text(f'#define HELIA_RT_VERSION "v{HELIART_VERSION}"\n')
    (tf_dir / "micro_log.cc").write_text("// stub\n")
    cortex_dir = tf_dir / "cortex_m_generic"
    cortex_dir.mkdir()
    (cortex_dir / "debug_log.cc").write_text("// stub\n")
    (dist / "third_party").mkdir()
    (dist / "third_party" / "flatbuffers").mkdir()
    (dist / "signal").mkdir()
    # Copy the bundled test snapshot of heliaRT's nsx/ module into the dist
    # so that _install_nsx_module finds the upstream-style files. This
    # mirrors what real heliaRT >= 1.16.0 release zips ship.
    import shutil

    nsx_src = FIXTURES_DIR / "heliart_nsx"
    nsx_dst = dist / "nsx"
    nsx_dst.mkdir()
    shutil.copy2(nsx_src / "CMakeLists.txt", nsx_dst / "CMakeLists.txt")
    shutil.copy2(nsx_src / "nsx-module.yaml", nsx_dst / "nsx-module.yaml")
    return dist


@pytest.fixture()
def fake_source_tree(tmp_path: Path) -> Path:
    """Create a minimal fake heliaRT *source* tree (source-build mode).

    Mirrors the structure expected by ``_install_nsx_module_source``:
    - ``nsx/CMakeLists.txt`` and ``nsx/nsx-module.yaml`` (source-build style)
    - ``cmake/helia_rt_sources.cmake`` (presence sentinel)
    - ``tensorflow/lite/micro/helia_rt_version.h``
    """
    src = tmp_path / "heliart_src"
    src.mkdir()
    (src / "nsx").mkdir()
    (src / "nsx" / "CMakeLists.txt").write_text(
        "# source-build heliaRT nsx CMakeLists (test stub)\n"
        "add_library(nsx_helia_rt INTERFACE)\n"
        "add_library(nsx::helia_rt ALIAS nsx_helia_rt)\n"
    )
    (src / "nsx" / "nsx-module.yaml").write_text(
        f'schema_version: 1\nmodule:\n  name: nsx-helia-rt\n  version: "{HELIART_VERSION}"\n'
    )
    (src / "cmake").mkdir()
    (src / "cmake" / "helia_rt_sources.cmake").write_text("# stub\n")
    tf_dir = src / "tensorflow" / "lite" / "micro"
    tf_dir.mkdir(parents=True)
    (tf_dir / "helia_rt_version.h").write_text(f'#define HELIA_RT_VERSION "v{HELIART_VERSION}"\n')
    return src


@pytest.fixture()
def fake_cmsis_nn(tmp_path: Path) -> Path:
    """Create a minimal fake ns-cmsis-nn tree with a native nsx/ module.

    Mirrors the structure of ns-cmsis-nn >= v7.23.0 which ships a native
    ``nsx/CMakeLists.txt`` and ``nsx/nsx-module.yaml``.
    """
    nn = tmp_path / "ns_cmsis_nn"
    nn.mkdir()
    (nn / "Include").mkdir()
    (nn / "Include" / "arm_nnfunctions.h").write_text("// stub\n")
    (nn / "Source").mkdir()
    (nn / "Source" / "stub.c").write_text("// stub\n")
    (nn / "cmake").mkdir()
    (nn / "cmake" / "ns_cmsis_nn.cmake").write_text("# stub\n")
    nsx = nn / "nsx"
    nsx.mkdir()
    (nsx / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.15)\n"
        "add_library(nsx_cmsis_nn INTERFACE)\n"
        "add_library(nsx::cmsis_nn ALIAS nsx_cmsis_nn)\n"
    )
    (nsx / "nsx-module.yaml").write_text(
        'schema_version: 1\nmodule:\n  name: nsx-cmsis-nn\n  version: "7.23.0"\n'
    )
    return nn
