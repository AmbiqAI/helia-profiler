"""The public fixture surface matches its golden, and every change carries a version bump.

Regenerate with ``HPX_UPDATE_SNAPSHOTS=1`` after bumping ``FIXTURE_API_VERSION``:
minor for additions only, major when a name, field or parameter is removed or changed.
"""

from __future__ import annotations

import dataclasses
import enum
import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

import pytest

import helia_profiler.fixture as fixture

GOLDEN = Path(__file__).with_name("fixture_api_v1.json")

#: Exported dataclasses that are not frozen yet, with the reason.
_MUTABLE = {"ToolchainInfo": "shared results model filled by the build stage"}


def _describe(obj: Any) -> dict[str, Any]:
    if isinstance(obj, type) and issubclass(obj, enum.Enum):
        base = "str" if issubclass(obj, str) else "int" if issubclass(obj, int) else "object"
        return {"kind": "enum", "base": base, "members": {m.name: m.value for m in obj}}
    if isinstance(obj, type) and dataclasses.is_dataclass(obj):
        fields = []
        for f in dataclasses.fields(obj):
            if f.default is not dataclasses.MISSING:
                default = repr(f.default)
            elif f.default_factory is not dataclasses.MISSING:
                default = "<factory>"
            else:
                default = "<required>"
            fields.append([f.name, str(f.type), default])
        return {"kind": "dataclass", "frozen": obj.__dataclass_params__.frozen, "fields": fields}
    if isinstance(obj, type) and getattr(obj, "_is_protocol", False):
        methods = {
            name: str(inspect.signature(member))
            for name, member in sorted(vars(obj).items())
            if inspect.isfunction(member) and not name.startswith("_")
        }
        return {"kind": "protocol", "methods": methods}
    if inspect.isfunction(obj):
        return {"kind": "function", "signature": str(inspect.signature(obj))}
    return {"kind": "constant", "value": json.loads(json.dumps(obj))}


def describe_surface() -> dict[str, Any]:
    return {name: _describe(getattr(fixture, name)) for name in sorted(fixture.__all__)}


def _required_bump(old: dict[str, Any], new: dict[str, Any]) -> str | None:
    if old == new:
        return None
    additive = all(name in new and new[name] == entry for name, entry in old.items())
    return "minor" if additive else "major"


def test_public_fixture_surface_matches_golden() -> None:
    current = {"api_version": list(fixture.FIXTURE_API_VERSION), "surface": describe_surface()}
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else None
    if os.environ.get("HPX_UPDATE_SNAPSHOTS"):
        if golden is not None:
            bump = _required_bump(golden["surface"], current["surface"])
            old_major, old_minor = golden["api_version"]
            new_major, new_minor = current["api_version"]
            if bump == "major" and new_major <= old_major:
                pytest.fail("Surface changed incompatibly: bump the FIXTURE_API_VERSION major")
            if bump == "minor" and (new_major, new_minor) <= (old_major, old_minor):
                pytest.fail("Surface grew: bump the FIXTURE_API_VERSION minor")
        GOLDEN.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert golden is not None, f"Missing {GOLDEN.name}; regenerate with HPX_UPDATE_SNAPSHOTS=1"
    assert current == golden, (
        "Public fixture API differs from its golden: bump FIXTURE_API_VERSION and regenerate"
    )


def test_version_bump_rule_distinguishes_additions_from_changes() -> None:
    old = {"a": {"kind": "function", "signature": "(x)"}}
    assert _required_bump(old, dict(old)) is None
    assert _required_bump(old, {**old, "b": {"kind": "constant", "value": 1}}) == "minor"
    assert _required_bump(old, {"a": {"kind": "function", "signature": "(x, y)"}}) == "major"
    assert _required_bump(old, {}) == "major"


def test_public_records_are_frozen_dataclasses() -> None:
    mutable = {
        name
        for name in fixture.__all__
        if dataclasses.is_dataclass(obj := getattr(fixture, name))
        and not obj.__dataclass_params__.frozen  # type: ignore[union-attr]
    }
    assert mutable == set(_MUTABLE)


def test_version_is_a_major_minor_pair() -> None:
    version = fixture.FIXTURE_API_VERSION
    assert isinstance(version, tuple) and len(version) == 2
    assert all(isinstance(part, int) and part >= 0 for part in version)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("helia_profiler.fixture_analysis", "FixtureTensor"),
        ("helia_profiler.fixture_capture", "capture_fixture"),
        ("helia_profiler.fixture_capture", "FixtureCaptureRequest"),
        ("helia_profiler.fixture_capture", "FixtureTarget"),
        ("helia_profiler.fixture_metrics", "inspect_fixture_footprint"),
        ("helia_profiler.fixture_observation", "summarize_fixture_measurements"),
        ("helia_profiler.fixture_runtime", "FixtureFile"),
    ],
)
def test_existing_module_paths_resolve_to_the_public_objects(module: str, name: str) -> None:
    assert getattr(importlib.import_module(module), name) is getattr(fixture, name)
