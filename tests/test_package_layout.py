from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
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


def test_no_engine_adapter_imports_out_of_another_engines_package() -> None:
    """Shared engine logic lives in engines/, not inside one engine (issue #7).

    ``cmsis_nn_module_ref`` started inside the heliaAOT package, and heliaRT
    and ExecuTorch both grew imports reaching into it -- three engines
    depending on a fourth's internals for something none of them owns. It now
    lives in ``engines/cmsis_nn.py``; this stops the pattern returning, for
    that helper or any other.

    An engine importing its OWN subpackage is fine; so is importing from
    ``engines`` itself. What is flagged is one engine package naming another.
    """
    repo_root = Path(__file__).resolve().parent.parent
    engines_root = repo_root / "src" / "helia_profiler" / "engines"
    engine_packages = {
        entry.name
        for entry in engines_root.iterdir()
        if entry.is_dir() and not entry.name.startswith("__")
    }

    offenders: list[str] = []
    for path in sorted(engines_root.rglob("*.py")):
        rel = path.relative_to(engines_root)
        owner = rel.parts[0] if len(rel.parts) > 1 else None
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if "import" not in stripped:
                continue
            if owner is None:
                # Lives directly in engines/ -- the registry and shared
                # helpers legitimately name every adapter.
                continue
            for other in engine_packages:
                if other == owner:
                    continue  # its own package
                if f".{other} import" in stripped or f".{other}." in stripped:
                    offenders.append(f"{rel}:{line_no}: {stripped}")

    assert not offenders, (
        "these engine modules import from another engine's package; move the "
        "shared piece up into helia_profiler.engines instead:\n  "
        + "\n  ".join(offenders)
    )


def test_wheel_contains_only_canonical_evaluation_modules(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    # Build from a staged copy, never from the real checkout: with
    # cwd=repo_root, setuptools rewrites build/lib/** inside the working tree
    # on every run — gitignored, so invisible, and a stale copy of the package
    # can shadow imports (issue #151).  The version is static in pyproject.toml
    # (no SCM-derived versioning), so no git metadata needs staging; the build
    # reads pyproject.toml, the readme, the top-level license files, and src/
    # (which holds the third pyproject-listed license, the vendored SEGGER one).
    staged = tmp_path / "staged"
    staged.mkdir()
    for name in ("pyproject.toml", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(repo_root / name, staged / name)
    shutil.copytree(
        repo_root / "src",
        staged / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    wheel_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheel_dir)],
        cwd=staged,
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
    assert "helia_profiler/modelcost/model_analysis.py" in names
    assert "helia_profiler/results/models.py" in names
    assert "helia_profiler/results/artifacts.py" in names
    assert "helia_profiler/results/manifest.py" in names
    assert "helia_profiler/deps/compatibility.py" in names
    # #229 (lens on PR2): module moves must not resurrect old paths in the
    # wheel — pin FULL set equality between the staged source tree and the
    # wheel's python modules, so a stale or duplicated module ships loudly.
    wheel_modules = {n for n in names if n.startswith("helia_profiler/") and n.endswith(".py")}
    source_modules = {
        f"helia_profiler/{p.relative_to(staged / 'src' / 'helia_profiler').as_posix()}"
        for p in (staged / "src" / "helia_profiler").rglob("*.py")
    }
    assert wheel_modules == source_modules, (
        f"wheel/source module drift: only-in-wheel={sorted(wheel_modules - source_modules)} "
        f"only-in-source={sorted(source_modules - wheel_modules)}"
    )
    assert "helia_profiler/data/compatibility-baseline-v1.json" in names
    assert "helia_profiler/data/run_summary.schema.v1.json" in names
    assert "helia_profiler/data/run_metadata.schema.v1.json" in names
    assert "helia_profiler/data/profile_results.schema.v1.json" in names
    assert "helia_profiler/data/session_intent.schema.v1.json" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.c" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT.h" in names
    assert "helia_profiler/vendor/segger_rtt/RTT/SEGGER_RTT_ConfDefaults.h" in names

    installed = tmp_path / "installed"
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from helia_profiler.deps.compatibility import load_compatibility_baseline; "
                "baseline = load_compatibility_baseline(); "
                "print(baseline.neuralspotx_version); "
                "print(baseline.project('nsx-ambiq-sdk').ref); "
                "print(baseline.fingerprint)"
            ),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(installed)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert probe.stdout.splitlines() == [
        "0.7.17",
        "a9f4ec25a162f6f3700623feb691423bb5a51132",
        "8b9ae1e1aae49dbea81e5b467088f45d31742835e463aaeb8612b7e34d0fd9a4",
    ]


# ---------------------------------------------------------------------------
# #229 D2 — layering contracts for the vocabulary leaf and light package inits
# ---------------------------------------------------------------------------

_SRC = Path(__file__).resolve().parent.parent / "src" / "helia_profiler"


def _hpx_import_targets(path: Path, *, module_level_only: bool) -> list[str]:
    """Intra-hpx import targets in *path*, one entry per imported name.

    Relative imports keep their dots (``"..config"``); absolute ones their
    module path — and ``from helia_profiler import config`` reports
    ``"helia_profiler.config"``, so package-attribute imports cannot slip
    past a module-name filter. ``module_level_only=True`` restricts to
    plain top-level statements, excluding only the *body* of
    ``if TYPE_CHECKING:`` (its ``else:`` branch runs at import time and is
    included); ``False`` walks everything, lazy and guarded alike.
    """
    tree = ast.parse(path.read_text())
    if module_level_only:
        nodes: list[ast.stmt] = []
        for node in tree.body:
            if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.dump(node.test):
                nodes.extend(node.orelse)
            else:
                nodes.append(node)
    else:
        nodes = list(ast.walk(tree))

    targets: list[str] = []
    for node in nodes:
        if isinstance(node, ast.ImportFrom):
            base = "." * (node.level or 0) + (node.module or "")
            if node.level or (node.module or "").split(".")[0] == "helia_profiler":
                # `from . import x` has an empty module: join without adding
                # a dot, or a level-1 sibling import would encode as a
                # level-2 parent target (#235).
                joiner = "" if base.endswith(".") else "."
                targets.extend(f"{base}{joiner}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            targets.extend(
                alias.name
                for alias in node.names
                if alias.name.split(".")[0] == "helia_profiler"
            )
    return targets


def _names_module(target: str, module: str) -> bool:
    return module in target.lstrip(".").split(".")


def test_vocab_is_a_leaf() -> None:
    """vocab.py is the bottom layer: any hpx import here — lazy, guarded,
    or otherwise — rebuilds the very cycles it exists to break (#229 D2)."""
    assert _hpx_import_targets(_SRC / "vocab.py", module_level_only=False) == []


def test_engines_package_init_stays_stdlib_light() -> None:
    """``from ..engines import EngineType`` is cycle-safe for wire/, results/
    and config/ ONLY while engines/__init__ imports nothing from hpx at
    module level (adapters load lazily). This pin is why EngineType did not
    need an engines/types.py split (#229 D2 refinement)."""
    hits = _hpx_import_targets(_SRC / "engines" / "__init__.py", module_level_only=True)
    assert hits == [], f"engines/__init__ gained module-level hpx imports: {hits}"


def test_platform_never_imports_the_config_layer() -> None:
    """The silicon-info package must not know the config resolver exists —
    the old lazy ``config.Toolchain`` import was the one documented
    config<->platform cycle, inverted in #229 D2 (platform owns the
    toolchain-name map). Lazy and guarded imports count too."""
    offenders = {
        path.name: hits
        for path in sorted((_SRC / "platform").glob("*.py"))
        if (
            hits := [
                target
                for target in _hpx_import_targets(path, module_level_only=False)
                if _names_module(target, "config")
            ]
        )
    }
    assert not offenders, f"platform/ imports the config layer: {offenders}"


def _hpx_target_head(target: str, package: str) -> str:
    """Resolve a target to the top-level hpx module it lands in.

    From a file in ``helia_profiler.<package>``: ``".sibling"`` is the
    package itself, ``"..other.name"`` is ``other``, and an absolute target
    contributes its second segment."""
    if target.startswith(".."):
        return target.lstrip(".").split(".")[0]
    if target.startswith("."):
        return package
    return target.split(".")[1] if "." in target else target


def _closure_offenders(package: str, allowed: set[str]) -> dict[str, list[str]]:
    """Per-file hpx-import targets outside *allowed*, over the WHOLE package
    tree (rglob: a future subpackage must not escape the wall)."""
    offenders: dict[str, list[str]] = {}
    for path in sorted((_SRC / package).rglob("*.py")):
        hits = [
            target
            for target in _hpx_import_targets(path, module_level_only=False)
            if _hpx_target_head(target, package) not in allowed
        ]
        if hits:
            offenders[path.name] = hits
    return offenders


def test_platform_closure_stays_extractable() -> None:
    """#229 D4: platform/ is the silicon-info extraction seam — its modules
    may import only siblings and the ``errors`` leaf. Anything else is a
    new extraction blocker to cut deliberately, not accrete silently."""
    offenders = _closure_offenders("platform", {"platform", "errors"})
    assert not offenders, f"platform/ gained extraction blockers: {offenders}"


def test_modelcost_closure_is_sibling_only() -> None:
    """#229 D4: the model-cost core must stay free of hpx imports — its
    optional readers (litert, helia-aot) are guarded third-party deps, and
    every hpx-side concern (engine dispatch, artifacts, reporting) lives
    outside. This is the wall a future shared package ships behind."""
    offenders = _closure_offenders("modelcost", {"modelcost"})
    assert not offenders, f"modelcost/ gained hpx imports: {offenders}"
