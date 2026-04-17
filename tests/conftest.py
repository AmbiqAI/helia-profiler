"""Shared test fixtures for heliaPROFILER."""

from __future__ import annotations

from pathlib import Path

import pytest

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
    (dist / "lib" / "libtensorflow-microlite-cm55-gcc-release-with-logs.a").write_bytes(b"\x00")
    (dist / "lib" / "libtensorflow-microlite-cm4-gcc-release-with-logs.a").write_bytes(b"\x00")
    (dist / "tensorflow").mkdir()
    (dist / "tensorflow" / "lite").mkdir()
    (dist / "third_party").mkdir()
    (dist / "third_party" / "flatbuffers").mkdir()
    return dist
