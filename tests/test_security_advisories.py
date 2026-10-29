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

import pytest
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
        ("certifi", "2026.4.22", "2026.10.19"),
        ("urllib3", "2.7.0", "2.8.0"),
        ("tornado", "6.5.9", "6.6"),
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


@pytest.mark.parametrize(
    ("name", "first_patched", "last_vulnerable"),
    [("certifi", "2026.10.19", "2026.10.18"), ("urllib3", "2.8.0", "2.7.0")],
)
def test_runtime_floors_protect_published_installations(
    name: str, first_patched: str, last_vulnerable: str
) -> None:
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirements = {
        canonicalize_name(req.name): req
        for req in map(Requirement, manifest["project"]["dependencies"])
    }
    requirement = requirements[canonicalize_name(name)]
    assert requirement.marker is None
    assert requirement.specifier.contains(first_patched)
    assert not requirement.specifier.contains(last_vulnerable)
    assert canonicalize_name("tornado") not in requirements


def test_standalone_zoo_environment_has_runtime_security_floors() -> None:
    source = (REPO_ROOT / "tools" / "zoo_registry.py").read_text(encoding="utf-8")
    metadata = source.split("# /// script\n", 1)[1].split("# ///", 1)[0]
    manifest = tomllib.loads("\n".join(line.removeprefix("# ") for line in metadata.splitlines()))
    requirements = {
        canonicalize_name(req.name): req for req in map(Requirement, manifest["dependencies"])
    }
    for name in ("certifi", "urllib3"):
        assert requirements[canonicalize_name(name)].specifier == _security_floors()[name].specifier


def test_ci_installers_require_patched_certificate_trust() -> None:
    import yaml

    installers = []
    for workflow in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        for job in document.get("jobs", {}).values():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                    version = step.get("with", {}).get("version")
                    assert version, f"{workflow.name} leaves uv trust unpinned"
                    assert Version(version) >= Version("0.13.4")
                    installers.append(version)
    assert installers
    manifest = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    requirement = Requirement("uv" + manifest["tool"]["uv"]["required-version"])
    assert not requirement.specifier.contains("0.13.3")
    assert requirement.specifier.contains("0.13.4")
