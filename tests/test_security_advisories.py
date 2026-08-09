"""Regression guard for advisory-driven dependency floors.

`pyproject.toml` declares `[tool.uv] constraint-dependencies` floors for
transitive packages with published security advisories. These tests assert the
floors are actually honoured by `uv.lock`, so a bad merge or a hand-edited lock
cannot silently reintroduce a vulnerable version.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"


def _security_floors() -> dict[str, Requirement]:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    constraints = manifest["tool"]["uv"]["constraint-dependencies"]
    return {canonicalize_name(req.name): req for req in map(Requirement, constraints)}


def _locked_versions() -> dict[str, Version]:
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    return {
        canonicalize_name(package["name"]): Version(package["version"])
        for package in lock["package"]
    }


def test_security_floors_are_declared() -> None:
    """The advisories from issue #91 stay pinned until every path outgrows them."""
    floors = _security_floors()

    assert floors["idna"].specifier.contains("3.15")
    assert not floors["idna"].specifier.contains("3.14")
    assert floors["pydantic-settings"].specifier.contains("2.14.2")
    assert not floors["pydantic-settings"].specifier.contains("2.14.1")


def test_locked_versions_satisfy_security_floors() -> None:
    floors = _security_floors()
    locked = _locked_versions()

    violations = {
        name: str(locked[name])
        for name, requirement in floors.items()
        if name in locked and not requirement.specifier.contains(locked[name])
    }

    assert not violations, (
        "uv.lock resolves packages below their declared security floor "
        f"({violations}); re-run `uv lock` instead of editing the lock by hand"
    )


def test_every_security_floor_is_still_load_bearing() -> None:
    """A floor for a package nobody depends on is dead weight — drop it."""
    floors = _security_floors()
    locked = _locked_versions()

    unused = sorted(set(floors) - set(locked))

    assert not unused, (
        f"constraint-dependencies floors no longer apply to any locked package: {unused}"
    )
