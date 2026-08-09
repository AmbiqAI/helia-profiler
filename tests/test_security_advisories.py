"""Regression guard for advisory-driven dependency floors.

`pyproject.toml` declares `[tool.uv] constraint-dependencies` floors for
transitive packages with published security advisories. These tests assert the
floors are actually honoured by `uv.lock`, so a bad merge or a hand-edited lock
cannot silently reintroduce a vulnerable version.

One package can hold several `[[package]]` entries in the lock — uv forks the
resolution per marker, and this repo already forks `pyjoulescope-driver` across
Python 3.12. Every fork is checked: the stale fork is exactly where an old
vulnerable version survives.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"


def _floors_of(manifest: dict[str, Any]) -> dict[str, Requirement]:
    constraints = manifest["tool"]["uv"]["constraint-dependencies"]
    return {canonicalize_name(req.name): req for req in map(Requirement, constraints)}


def _versions_of(lock: dict[str, Any]) -> dict[str, list[Version]]:
    """Every locked version per package, including per-marker resolution forks."""
    versions: dict[str, list[Version]] = {}
    for package in lock["package"]:
        versions.setdefault(canonicalize_name(package["name"]), []).append(
            Version(package["version"])
        )
    return versions


def _violations(
    floors: dict[str, Requirement], locked: dict[str, list[Version]]
) -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    for name, requirement in floors.items():
        below = [
            str(version)
            for version in locked.get(name, ())
            if not requirement.specifier.contains(version)
        ]
        if below:
            violations[name] = below
    return violations


def _security_floors() -> dict[str, Requirement]:
    return _floors_of(tomllib.loads(PYPROJECT.read_text(encoding="utf-8")))


def _locked_versions() -> dict[str, list[Version]]:
    return _versions_of(tomllib.loads(UV_LOCK.read_text(encoding="utf-8")))


def test_security_floors_are_declared() -> None:
    """The advisories from issue #91 stay pinned until every path outgrows them.

    Dropping a floor here is legitimate once nothing resolves below it — but it
    is a deliberate act, so delete the matching assertion rather than letting
    the constraint quietly disappear.
    """
    floors = _security_floors()

    for name, last_vulnerable, first_patched in (
        ("idna", "3.14", "3.15"),
        ("pydantic-settings", "2.14.1", "2.14.2"),
    ):
        assert name in floors, (
            f"security floor for {name!r} is gone from [tool.uv] constraint-dependencies; "
            "if that removal is intentional, drop it from this test too"
        )
        assert floors[name].specifier.contains(first_patched)
        assert not floors[name].specifier.contains(last_vulnerable)


def test_locked_versions_satisfy_security_floors() -> None:
    violations = _violations(_security_floors(), _locked_versions())

    assert not violations, (
        "uv.lock resolves packages below their declared security floor "
        f"({violations}); re-run `uv lock` instead of editing the lock by hand"
    )


def test_every_locked_fork_is_checked_against_the_floor() -> None:
    """A patched fork must not mask a vulnerable one, in either file order."""
    floors = _floors_of({"tool": {"uv": {"constraint-dependencies": ["idna>=3.15"]}}})
    forks = [{"name": "idna", "version": "3.13"}, {"name": "idna", "version": "3.18"}]

    for ordered in (forks, list(reversed(forks))):
        violations = _violations(floors, _versions_of({"package": ordered}))
        assert violations == {"idna": ["3.13"]}


def test_every_security_floor_is_still_load_bearing() -> None:
    """A floor for a package nobody depends on is dead weight — drop it."""
    unused = sorted(set(_security_floors()) - set(_locked_versions()))

    assert not unused, (
        f"constraint-dependencies floors no longer apply to any locked package: {unused}"
    )
