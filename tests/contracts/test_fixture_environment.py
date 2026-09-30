"""Every environment read in the package is reviewed for fixed-fixture builds.

A read that can swap a build input must be refused by ``build_fixed_fixture``;
any other read carries the reason it cannot change fixture firmware. A new read
fails here until it is classified.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import helia_profiler
from helia_profiler._fixture_build import _refuse_overrides
from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError

PACKAGE = Path(helia_profiler.__file__).resolve().parent

#: Reads that swap a build input; the fixture build refuses each one.
REFUSED = {
    ("deps/compatibility.py", "CMSIS_NN_PATH"),
    ("deps/compatibility.py", "<variable>"),
    ("deps/dependencies.py", "<variable>"),
    ("engines/cmsis_nn.py", "CMSIS_NN_PATH"),
    ("engines/helia_rt/adapter.py", "HELIART_DIST_PATH"),
    ("engines/helia_rt/artifacts.py", "HELIART_DIST_PATH"),
    ("engines/helia_rt/artifacts.py", "HELIART_SOURCE_PATH"),
    ("firmware/launcher.py", "HPX_COMPILER_LAUNCHER"),
    ("firmware/segger.py", "SEGGER_RTT_PATH"),
}

#: Refused variables named through a loop variable at the ``<variable>`` sites above.
REFUSED_INDIRECT = ("HELIART_DIST_PATH", "HELIART_SOURCE_PATH")

#: Reads that cannot change fixture firmware, with the reason.
ALLOWED = {
    ("_fixture_build.py", "<name>"): "the fixture refusal check itself",
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
}


def _environment_reads() -> set[tuple[str, str]]:
    reads: set[tuple[str, str]] = set()
    for path in sorted(PACKAGE.rglob("*.py")):
        module = path.relative_to(PACKAGE).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                func = node.func
                environ_get = (
                    func.attr in {"get", "pop", "setdefault"}
                    and isinstance(func.value, ast.Attribute)
                    and func.value.attr == "environ"
                )
                if (environ_get or func.attr == "getenv") and node.args:
                    key = node.args[0]
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "environ"
                and isinstance(node.ctx, ast.Load)
            ):
                key = node.slice
            if key is not None:
                name = key.value if isinstance(key, ast.Constant) else f"<{ast.unparse(key)}>"
                reads.add((module, str(name)))
    return reads


def test_every_environment_read_is_classified() -> None:
    reads = _environment_reads()
    unclassified = reads - REFUSED - set(ALLOWED)
    assert not unclassified, (
        f"Classify these environment reads for fixture builds: {sorted(unclassified)}"
    )
    assert not REFUSED & set(ALLOWED)


def test_classification_has_no_stale_entries() -> None:
    reads = _environment_reads()
    assert (REFUSED | set(ALLOWED)) - reads == set()


@pytest.mark.parametrize(
    "name",
    sorted({name for _, name in REFUSED if not name.startswith("<")} | set(REFUSED_INDIRECT)),
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
