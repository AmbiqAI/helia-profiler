"""Every environment read that can reach a fixed-fixture build is reviewed.

That means reads in this package and in the pinned neuralspotx package. A read
that can swap a build input must be refused by ``build_fixed_fixture``; any
other read carries the reason it cannot change fixture firmware. A new read
fails here until it is classified.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

import helia_profiler
from helia_profiler._fixture_build import FIXTURE_REFUSED_ENVIRONMENT, _refuse_overrides
from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError

PACKAGE = Path(helia_profiler.__file__).resolve().parent
WHOLE = "<environment>"

#: Reads in this package that swap a build input; the fixture build refuses each one.
REFUSED = {
    ("deps/compatibility.py", "CMSIS_NN_PATH"),
    ("deps/compatibility.py", "HELIART_DIST_PATH"),
    ("deps/compatibility.py", "HELIART_SOURCE_PATH"),
    ("deps/dependencies.py", "HELIART_DIST_PATH"),
    ("deps/dependencies.py", "HELIART_SOURCE_PATH"),
    ("engines/helia_rt/adapter.py", "HELIART_DIST_PATH"),
    ("engines/helia_rt/artifacts.py", "HELIART_DIST_PATH"),
    ("engines/helia_rt/artifacts.py", "HELIART_SOURCE_PATH"),
    ("firmware/launcher.py", "HPX_COMPILER_LAUNCHER"),
    ("firmware/segger.py", "SEGGER_RTT_PATH"),
}

#: Reads in this package that cannot change fixture firmware, with the reason.
ALLOWED = {
    ("_fixture_build.py", "<name>"): "the fixture refusal check itself",
    ("_fixture_build.py", "HPX_COMPILER_LAUNCHER"): "the fixture refusal check itself",
    ("engines/helia_rt/download.py", "GITHUB_TOKEN"): "download auth; archives are hash-verified",
    ("engines/helia_rt/download.py", "GH_TOKEN"): "download auth; archives are hash-verified",
    ("hostenv/cache_dirs.py", "HPX_CACHE_DIR"): "cache location; cached content is hash-verified",
    ("hostenv/cache_dirs.py", "XDG_CACHE_HOME"): "cache location; cached content is hash-verified",
    ("hostenv/doctor.py", "ATFE_ROOT"): "doctor report only",
    ("hostenv/toolchains.py", "ATFE_ROOT"): "toolchain root; recorded in ToolchainInfo",
    (
        "power/joulescope/capture_gated.py",
        "HPX_POWER_FULLRATE_XCHECK",
    ): "power capture; fixtures refuse power",
    (
        "power/joulescope/capture_gated.py",
        "HPX_GATE_DEBUG_DUMP",
    ): "power capture; fixtures refuse power",
    (
        "power/joulescope/stats.py",
        "HPX_POWER_ALLOW_NEGATIVE",
    ): "power capture; fixtures refuse power",
    ("target/probe/jlink.py", "JLINK_PATH"): "probe tooling, not build input",
    ("target/probe/jlink.py", "ProgramFiles"): "probe tooling, not build input",
    ("target/probe/jlink.py", "ProgramFiles(x86)"): "probe tooling, not build input",
    ("target/probe/jlink.py", "<env_name>"): "probe tooling, not build input",
    ("target/probe/jlink.py", "LD_LIBRARY_PATH"): "probe tooling, not build input",
    ("validation/report.py", "HPX_SOURCE_REVISIONS_JSON"): "validation report metadata",
    ("validation/report.py", "GITHUB_EVENT_NAME"): "validation report metadata",
    ("validation/report.py", "HPX_VALIDATION_RUN_ORIGIN"): "validation report metadata",
    ("validation/report.py", "GITHUB_REPOSITORY"): "validation report metadata",
    ("validation/report.py", "GITHUB_SERVER_URL"): "validation report metadata",
    ("validation/report.py", "<name>"): "validation report metadata",
    ("validation/runner.py", "CMSIS_NN_PATH"): "validation harness; not a fixture build path",
    (
        "validation/runner.py",
        "NSX_EXECUTORCH_ROOT",
    ): "validation harness; ExecuTorch has no fixture",
    ("validation/runner.py", "<name>"): "validation harness; not a fixture build path",
    ("validation/runner.py", WHOLE): "validation harness subprocess environment",
}

#: Variables the pinned neuralspotx package reads, by name.
NSX_REFUSED = {"NSX_ALLOW_VERSION_MISMATCH", "NSX_SKIP_COMPAT_CHECK"}
NSX_ALLOWED = {
    "ATFE_ROOT": "toolchain root; recorded in ToolchainInfo",
    "JLINK_PATH": "probe tooling, not build input",
    "NSX_RESOLVE_PARALLELISM": "resolver concurrency only",
    "NSX_RESOLVE_TTL": "resolve cache lifetime; resolved commits are checked against the lock",
    "ProgramFiles": "probe tooling search path, not build input",
    "ProgramFiles(x86)": "probe tooling search path, not build input",
    "<name>": "git retry and backoff tuning only",
    "NSX_CACHE_DIR": "cache location; resolved commits are checked against the lock",
    "NSX_DISABLE_MODULE_CACHE": "cache use only; resolved commits are checked against the lock",
    "NSX_LOCK_STALE_DAYS": "lock staleness warning only",
    "XDG_CACHE_HOME": "cache location; resolved commits are checked against the lock",
    "XDG_CONFIG_HOME": "CLI first-run tutorial only",
}


def _environment_reads(root: Path) -> set[tuple[str, str]]:
    """Every environment access under *root*, keyed by module and variable.

    Covers ``os.environ`` in any form (``get``/``pop``/subscript/``in``/whole
    copies), ``os.getenv``, aliased ``os`` imports and ``from os import``.
    A loop variable over a literal tuple resolves to each name in it.
    """
    reads: set[tuple[str, str]] = set()
    for path in sorted(root.rglob("*.py")):
        module = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        os_names, environ_names, getenv_names = {"os"}, set(), set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                os_names |= {a.asname or a.name for a in node.names if a.name == "os"}
            elif isinstance(node, ast.ImportFrom) and node.module == "os":
                environ_names |= {a.asname or a.name for a in node.names if a.name == "environ"}
                getenv_names |= {a.asname or a.name for a in node.names if a.name == "getenv"}
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        parents = {
            child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)
        }

        def is_environ(node: ast.AST) -> bool:
            return (
                isinstance(node, ast.Attribute)
                and node.attr == "environ"
                and isinstance(node.value, ast.Name)
                and node.value.id in os_names
            ) or (isinstance(node, ast.Name) and node.id in environ_names)

        def names(key: ast.AST | None, at: ast.AST) -> set[str]:
            if key is None:
                return {WHOLE}
            if isinstance(key, ast.Constant):
                return {str(key.value)}
            if isinstance(key, ast.Name) and key.id in constants:
                return {constants[key.id]}
            if isinstance(key, ast.Name):
                scope = parents.get(at)
                while scope is not None:
                    if (
                        isinstance(scope, ast.For)
                        and isinstance(scope.target, ast.Name)
                        and scope.target.id == key.id
                        and isinstance(scope.iter, ast.Tuple | ast.List)
                        and all(isinstance(e, ast.Constant) for e in scope.iter.elts)
                    ):
                        return {
                            str(e.value) for e in scope.iter.elts if isinstance(e, ast.Constant)
                        }
                    scope = parents.get(scope)
            return {f"<{ast.unparse(key)}>"}

        for node in ast.walk(tree):
            found: set[str] = set()
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and is_environ(func.value)
                    and func.attr in {"get", "pop", "setdefault"}
                ):
                    found = names(node.args[0] if node.args else None, node)
                elif (
                    isinstance(func, ast.Attribute)
                    and func.attr == "getenv"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in os_names
                ) or (isinstance(func, ast.Name) and func.id in getenv_names):
                    found = names(node.args[0] if node.args else None, node)
            elif (
                isinstance(node, ast.Subscript)
                and is_environ(node.value)
                and isinstance(node.ctx, ast.Load)
            ):
                found = names(node.slice, node)
            elif isinstance(node, ast.Compare) and any(is_environ(c) for c in node.comparators):
                found = names(node.left, node)
            elif is_environ(node):
                parent = parents.get(node)
                handled = (
                    isinstance(parent, ast.Attribute)
                    and parent.attr in {"get", "pop", "setdefault"}
                    or isinstance(parent, ast.Subscript)
                    or isinstance(parent, ast.Compare)
                )
                if not handled:
                    found = {WHOLE}
            reads |= {(module, name) for name in found}
    return reads


def _nsx_reads() -> set[str]:
    neuralspotx = pytest.importorskip("neuralspotx")
    root = Path(neuralspotx.__file__).resolve().parent
    reads = {name for _, name in _environment_reads(root)}
    for path in root.rglob("*"):
        if path.suffix == ".cmake" or path.name == "CMakeLists.txt":
            reads |= set(
                re.findall(
                    r"ENV\{([A-Za-z0-9_]+)\}", path.read_text(encoding="utf-8", errors="replace")
                )
            )
    return reads


def test_every_environment_read_is_classified() -> None:
    reads = _environment_reads(PACKAGE)
    unclassified = reads - REFUSED - set(ALLOWED)
    assert not unclassified, (
        f"Classify these environment reads for fixture builds: {sorted(unclassified)}"
    )
    assert not REFUSED & set(ALLOWED)


def test_classification_has_no_stale_entries() -> None:
    assert (REFUSED | set(ALLOWED)) - _environment_reads(PACKAGE) == set()


def test_every_neuralspotx_environment_read_is_classified() -> None:
    reads = _nsx_reads()
    assert reads - NSX_REFUSED - set(NSX_ALLOWED) == set(), (
        "Classify the new neuralspotx environment reads"
    )
    assert (NSX_REFUSED | set(NSX_ALLOWED)) - reads == set()
    assert NSX_REFUSED <= set(FIXTURE_REFUSED_ENVIRONMENT)


@pytest.mark.parametrize(
    "name",
    sorted({name for _, name in REFUSED} | NSX_REFUSED | set(FIXTURE_REFUSED_ENVIRONMENT)),
)
def test_refused_variables_stop_a_fixture_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    model = tmp_path / "model.tflite"
    model.write_bytes(b"model")
    config = load_config(
        None,
        {
            "model": {"path": str(model), "arena_size": 65536},
            "engine": {"type": "helia-aot"},
            "target": {"toolchain": "atfe", "board": "apollo510_evb", "clock": {"cpu": "lp"}},
            "work_dir": str(tmp_path / "work"),
        },
    )
    monkeypatch.setenv(name, str(tmp_path))
    with pytest.raises(ConfigError, match="pinned inputs only"):
        _refuse_overrides(config)
