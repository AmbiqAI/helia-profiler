from __future__ import annotations

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
    assert "helia_profiler/evaluation/model_analysis.py" in names
    assert "helia_profiler/results/models.py" in names
    assert "helia_profiler/results/artifacts.py" in names
    assert "helia_profiler/results/manifest.py" in names
    assert "helia_profiler/deps/compatibility.py" in names
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
        "e71a1be178d546c9226aafa4b82fe3313a9ff7d865c1ee54d5902a425208777c",
    ]


# ---------------------------------------------------------------------------
# #229 D2 — layering contracts for the vocabulary leaf and light package inits
# ---------------------------------------------------------------------------


def _module_level_hpx_imports(path: Path) -> list[str]:
    """Names of intra-hpx modules imported at module level (TYPE_CHECKING
    blocks and function bodies excluded) — AST-based, so string mentions
    and lazy imports do not count."""
    import ast

    tree = ast.parse(path.read_text())
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = ast.dump(node.test)
            if "TYPE_CHECKING" in test:
                for child in ast.walk(node):
                    guarded.add(id(child))
    found: list[str] = []
    for node in tree.body:  # module level only
        if id(node) in guarded:
            continue
        if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
            found.append("." * node.level + (node.module or ""))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("helia_profiler"):
            found.append(node.module)
        elif isinstance(node, ast.Import):
            found.extend(a.name for a in node.names if a.name.startswith("helia_profiler"))
    return found


def test_vocab_is_a_leaf():
    """vocab.py is the bottom layer: any hpx import here rebuilds the very
    cycles it exists to break (#229 D2)."""
    path = Path("src/helia_profiler/vocab.py")
    assert _module_level_hpx_imports(path) == []


def test_engines_package_init_stays_stdlib_light():
    """`from ..engines import EngineType` is cycle-safe for wire/, results/
    and config/ ONLY while engines/__init__ imports nothing from hpx at
    module level (adapters load lazily). This pin is why EngineType did not
    need an engines/types.py split (#229 D2 refinement)."""
    path = Path("src/helia_profiler/engines/__init__.py")
    assert _module_level_hpx_imports(path) == []


def test_platform_never_imports_the_config_layer():
    """The silicon-info package must not know the config resolver exists —
    the old lazy `config.Toolchain` import was the one documented cycle,
    inverted in #229 D2 (platform owns the toolchain-name map)."""
    offenders = {}
    for path in sorted(Path("src/helia_profiler/platform").glob("*.py")):
        hits = [
            imp
            for imp in _module_level_hpx_imports(path)
            if imp.endswith("config") or ".config" in imp or imp == "..config"
        ]
        # Also reject lazy/function-level config imports: AST-walk everything.
        import ast

        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and "config" in node.module.split("."):
                hits.append(("." * (node.level or 0)) + node.module)
        if hits:
            offenders[path.name] = hits
    assert not offenders, f"platform/ imports the config layer: {offenders}"
