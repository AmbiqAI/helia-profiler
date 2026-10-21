"""Each semantic hash kind's real basis keeps the shape its version promises.

Adding or removing a key in what a kind hashes changes every hash of that
kind, so it needs a new version in ``KIND_VERSIONS`` and a new entry here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import helia_profiler as hpx
from helia_profiler import _semantic_hash
from helia_profiler._fixture_build import FixtureTimingScope
from helia_profiler._identity_hash import build_hash
from helia_profiler._semantic_hash import KIND_VERSIONS
from helia_profiler.fixture_capture import FixtureCaptureResult
from helia_profiler.fixture_identity import fixture_run_identity
from helia_profiler.profile_identity import _observation_hash
from helia_profiler.results import FirmwareMeta, PmuResult

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "results" / "run-api-synthetic"

#: (kind, version) -> every top-level shape that version hashes. A mapping's
#: shape is its keys; a list's is the length of each entry, as ``[n]``.
BASES: dict[tuple[str, int], set[frozenset[str]]] = {
    ("intent", 1): {
        frozenset(
            {
                "settings",
                "model_sha256",
                "baseline_fingerprint",
                "qualification",
                "overrides",
                "local_inputs",
                "toolchain",
                "environment",
                "runtime",
            }
        )
    },
    ("build", 1): {frozenset({"image", "lock", "runtime_archive", "model"})},
    ("observation", 1): {
        # A profile's PMU result.
        frozenset({"meta", "presets", "layers", "overflow_detected", "groups"}),
        # A fixture or run capture.
        frozenset(
            {
                "state",
                "timing_scope",
                "output",
                "status",
                "checksum",
                "timing",
                "memory",
                "artifacts",
                "error",
                "outputs",
                "arena_scan",
                "psram_weights",
                "rail_energy",
            }
        ),
    },
    ("binding", 1): {
        frozenset(
            {
                "plan",
                "lifecycle",
                "timing_scope",
                "state_policy",
                "runtime_manifest",
                "observe_ram",
                "observe_aot_arenas",
                "power_sync",
            }
        )
    },
    ("inputs", 1): {frozenset({"[2]"})},
}


def _shape(value: Any) -> frozenset[str]:
    if isinstance(value, Mapping):
        return frozenset(value)
    return frozenset(f"[{len(item)}]" for item in value)


@pytest.fixture
def observed(monkeypatch: pytest.MonkeyPatch) -> dict[str, set[frozenset[str]]]:
    """Every basis hashed while the test runs, by kind."""
    seen: dict[str, set[frozenset[str]]] = {}
    real = _semantic_hash.basis

    def spy(kind: Any, value: Any) -> bytes:
        seen.setdefault(kind, set()).add(_shape(value))
        return real(kind, value)

    monkeypatch.setattr(_semantic_hash, "basis", spy)
    return seen


def test_every_kind_hashes_the_shape_its_version_records(observed):
    # The retained build hashes an intent and a binding when it loads, and its
    # identity hashes the pinned inputs.
    compiled = hpx.load_build(_EXAMPLE / "build.json", roots={"run": _EXAMPLE})
    compiled.identity
    fixture_run_identity(
        compiled.build, FixtureCaptureResult("success", FixtureTimingScope.INVOKE_ONLY)
    )
    _observation_hash(PmuResult(meta=FirmwareMeta()))
    build_hash(
        image_sha256="0" * 64, lock=b"targets: {}\n", runtime_archive=None, model_sha256="1" * 64
    )

    assert set(observed) == set(KIND_VERSIONS)
    for kind, shapes in observed.items():
        version = KIND_VERSIONS[kind]
        recorded = BASES.get((kind, version))
        assert recorded is not None, f"Record the {kind} basis keys for version {version} here"
        assert shapes == recorded, (
            f"The {kind} basis changed shape: bump KIND_VERSIONS[{kind!r}] past {version} "
            "and record the new keys under the new version"
        )
