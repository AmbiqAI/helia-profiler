"""Contracts binding each engine to its artifact type (#162 Phase 2).

``EngineArtifacts`` used to be one flat bundle: a 7-field common core plus
20 engine-specific fields defaulted to ``None``.  A field set by the wrong
adapter — or read for the wrong engine — was invisible until a render or a
build misbehaved.  Now each adapter returns its own subtype, so a
wrong-engine read is an ``AttributeError`` at the access site.

These tests pin the three things that split can silently get wrong:

* every engine has exactly one artifact type, and each type pins its
  ``engine_type`` (a mismatch is a ``ValueError``, not a default);
* the four ``resolved_*`` identity properties reproduce the pre-split
  field routing in ``dependencies.py`` exactly — the workspace
  fingerprint must not move for any engine;
* engine-specific fields are absent from the other engines' types.
"""

from __future__ import annotations

from typing import get_type_hints

import pytest

from helia_profiler.engines import TFLM_ENGINE_HEADER, EngineType, get_adapter
from helia_profiler.engines.base import (
    EngineArtifacts,
    ExecutorchArtifacts,
    HeliaAotArtifacts,
    HeliaRtArtifacts,
    TflmArtifacts,
)

#: The one artifact type each engine produces.  Asserted complete over
#: ``EngineType`` below, so a new engine cannot land without one.
ARTIFACT_TYPE_FOR_ENGINE: dict[EngineType, type[EngineArtifacts]] = {
    EngineType.TFLM: TflmArtifacts,
    EngineType.HELIA_RT: HeliaRtArtifacts,
    EngineType.HELIA_AOT: HeliaAotArtifacts,
    EngineType.EXECUTORCH: ExecutorchArtifacts,
}


def _required_kwargs(engine_type: EngineType) -> dict[str, object]:
    """Sentinel values for every field the engine's type requires."""
    required: dict[EngineType, dict[str, object]] = {
        EngineType.TFLM: {"engine_header": TFLM_ENGINE_HEADER},
        EngineType.HELIA_RT: {
            "engine_header": TFLM_ENGINE_HEADER,
            "engine_backend": "sentinel-backend",
            "heliart_version": "sentinel-rt-version",
            "heliart_variant": "sentinel-variant",
            "heliart_toolchain_tag": "sentinel-toolchain",
        },
        EngineType.HELIA_AOT: {
            "engine_header": "sentinel_model.h",
            "aot_prefix": "sentinel",
            "aot_module_name": "sentinel-module",
            "aot_cmake_target": "nsx::sentinel",
            "helia_aot_version": "sentinel-aot-version",
        },
        EngineType.EXECUTORCH: {
            "engine_header": "nsx_executorch.h",
            "executorch_method_arena_size": 1,
            "executorch_planned_arena_size": 2,
            "executorch_temporary_arena_size": 3,
            "executorch_input_size": 4,
            "executorch_output_size": 5,
        },
    }
    return dict(required[engine_type])


def _build(engine_type: EngineType, **overrides: object) -> EngineArtifacts:
    """Construct the artifact type for *engine_type* with sentinel values."""
    kwargs = {**_required_kwargs(engine_type), **overrides}
    return ARTIFACT_TYPE_FOR_ENGINE[engine_type](**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# One type per engine, pinned at construction
# ---------------------------------------------------------------------------


def test_every_engine_has_exactly_one_artifact_type():
    assert set(ARTIFACT_TYPE_FOR_ENGINE) == set(EngineType)
    assert len(set(ARTIFACT_TYPE_FOR_ENGINE.values())) == len(EngineType)


@pytest.mark.parametrize("engine_type", list(EngineType))
def test_artifact_type_pins_its_engine_type(engine_type: EngineType):
    artifacts = _build(engine_type)
    assert artifacts.engine_type is engine_type
    assert type(artifacts)._PINNED_ENGINE_TYPE is engine_type


@pytest.mark.parametrize("engine_type", list(EngineType))
def test_mismatched_engine_type_raises(engine_type: EngineType):
    wrong = next(other for other in EngineType if other is not engine_type)
    kwargs = {**_required_kwargs(engine_type), "engine_type": wrong}
    with pytest.raises(ValueError, match="pinned to engine_type"):
        ARTIFACT_TYPE_FOR_ENGINE[engine_type](**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("engine_type", list(EngineType))
def test_adapter_prepare_returns_its_engine_artifact_type(engine_type: EngineType):
    """The adapter's declared ``prepare()`` return type is the engine's type.

    ``prepare()`` itself needs an AOT compiler / a pinned ExecuTorch
    checkout, so the pairing is pinned at the type level: the adapter
    registry and the artifact registry must agree for every engine.
    """
    adapter = get_adapter(engine_type)
    assert adapter.engine_type is engine_type
    declared = get_type_hints(type(adapter).prepare)["return"]
    assert declared is ARTIFACT_TYPE_FOR_ENGINE[engine_type]


@pytest.mark.parametrize("engine_type", list(EngineType))
def test_engine_header_has_no_cross_engine_default(engine_type: EngineType):
    """Every adapter must state its own header — none is inherited."""
    kwargs = {
        key: value
        for key, value in vars(_build(engine_type)).items()
        if key != "engine_header" and not key.startswith("_")
    }
    with pytest.raises(TypeError, match="engine_header"):
        ARTIFACT_TYPE_FOR_ENGINE[engine_type](**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Identity properties == the pre-split fingerprint field routing
# ---------------------------------------------------------------------------

#: What the flat ``EngineArtifacts`` actually held per engine before the
#: split: only the owning adapter ever set its own fields, everything else
#: kept the ``None`` default.
_LEGACY_FIELDS: dict[EngineType, dict[str, str | None]] = {
    EngineType.TFLM: {},
    EngineType.HELIA_RT: {
        "engine_backend": "sentinel-backend",
        "heliart_version": "sentinel-rt-version",
        "heliart_variant": "sentinel-variant",
        "heliart_toolchain_tag": "sentinel-toolchain",
    },
    EngineType.HELIA_AOT: {"helia_aot_version": "sentinel-aot-version"},
    EngineType.EXECUTORCH: {},
}


def _legacy_fingerprint_identity(engine_type: EngineType) -> dict[str, str | None]:
    """The exact expressions ``dependencies.py`` used before the split."""
    fields = _LEGACY_FIELDS[engine_type]
    engine_backend = fields.get("engine_backend")
    heliart_version = fields.get("heliart_version")
    heliart_variant = fields.get("heliart_variant")
    heliart_toolchain_tag = fields.get("heliart_toolchain_tag")
    helia_aot_version = fields.get("helia_aot_version")
    return {
        "resolved_backend": engine_backend,
        "resolved_variant": heliart_variant,
        # The coalesce is load-bearing: heliaAOT never set heliart_version,
        # so this yielded the helia-aot compiler version for that engine.
        "resolved_version": heliart_version or helia_aot_version,
        "toolchain_tag": heliart_toolchain_tag,
    }


@pytest.mark.parametrize("engine_type", list(EngineType))
def test_identity_properties_reproduce_legacy_fingerprint_routing(engine_type: EngineType):
    artifacts = _build(engine_type)
    assert {
        "resolved_backend": artifacts.resolved_backend,
        "resolved_variant": artifacts.resolved_variant,
        "resolved_version": artifacts.resolved_version,
        "toolchain_tag": artifacts.resolved_toolchain_tag,
    } == _legacy_fingerprint_identity(engine_type)


def test_identity_routing_is_engine_specific():
    """Spelled out per engine, so a wrong-but-consistent routing still fails."""
    tflm = _build(EngineType.TFLM)
    assert (tflm.resolved_backend, tflm.resolved_version) == (None, None)
    assert (tflm.resolved_variant, tflm.resolved_toolchain_tag) == (None, None)

    rt = _build(EngineType.HELIA_RT)
    assert rt.resolved_backend == "sentinel-backend"
    assert rt.resolved_version == "sentinel-rt-version"
    assert rt.resolved_variant == "sentinel-variant"
    assert rt.resolved_toolchain_tag == "sentinel-toolchain"

    aot = _build(EngineType.HELIA_AOT)
    assert aot.resolved_version == "sentinel-aot-version"
    assert (aot.resolved_backend, aot.resolved_variant) == (None, None)
    assert aot.resolved_toolchain_tag is None

    et = _build(EngineType.EXECUTORCH)
    assert (et.resolved_backend, et.resolved_version) == (None, None)
    assert (et.resolved_variant, et.resolved_toolchain_tag) == (None, None)


# ---------------------------------------------------------------------------
# Wrong-engine reads are AttributeErrors, not silent defaults
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("engine_type", "foreign_field"),
    [
        (EngineType.TFLM, "aot_prefix"),
        (EngineType.TFLM, "executorch_planned_arena_size"),
        (EngineType.TFLM, "heliart_version"),
        (EngineType.HELIA_RT, "aot_op_manifest"),
        (EngineType.HELIA_RT, "executorch_io_region"),
        (EngineType.HELIA_AOT, "heliart_variant"),
        (EngineType.HELIA_AOT, "executorch_method_arena_size"),
        (EngineType.EXECUTORCH, "aot_prefix"),
        (EngineType.EXECUTORCH, "heliart_toolchain_tag"),
    ],
)
def test_foreign_engine_field_access_raises(engine_type: EngineType, foreign_field: str):
    artifacts = _build(engine_type)
    with pytest.raises(AttributeError):
        getattr(artifacts, foreign_field)


def test_common_core_stays_on_the_base():
    """Fields every engine produces are readable without narrowing."""
    for engine_type in EngineType:
        artifacts = _build(engine_type)
        assert isinstance(artifacts, EngineArtifacts)
        assert artifacts.engine_header
        assert artifacts.extra_modules == []
        assert artifacts.cmake_vars == {}
        assert artifacts.source_files == []
        assert artifacts.include_dirs == []
        assert artifacts.static_libs == []
        assert artifacts.memory_plan is None
