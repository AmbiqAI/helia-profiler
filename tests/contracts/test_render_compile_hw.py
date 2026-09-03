"""#187 Tier 2 — ground-truth compile of rendered firmware with the REAL toolchain.

Tier 1 (``test_render_compile.py``) syntax-checks every render with a host
compiler and declaration stubs on every PR. This tier compiles the most
template-diverse renders with the real ``arm-none-eabi`` toolchain against a
REAL cached dependency workspace's include set — real vendor HAL headers,
real ``-mcpu``, NSX's own ``-Wall`` upgraded to ``-Werror``.

Runs where a warm workspace exists (the bench, after any profile/validate
run), marked ``compile_hw`` and deselected by default: GitHub runners have
no workspaces and every leg skips with its reason. A cold workspace build
is minutes of network+compile — the wrong cost profile for a compile gate —
so this test NEVER builds one (D3).

Design contract (#187 Tier 2, D1–D6):

* D1 — the compile command is extracted from the workspace's own
  ``build.ninja`` per-TU stanza (DEFINES/INCLUDES/FLAGS) and its compiler
  from ``rules.ninja``. No configure step, no workspace mutation.
* D2 — vendored ``modules/`` include dirs are passed as ``-isystem`` so
  warnings located inside vendor headers (the ``AM_SHARED_RW`` redefinition
  in every production build) do not gate OUR code; the rendered TU itself
  compiles at full ``-Werror``.
* D3 — the matrix below; legs whose workspace is absent skip with reason.
* D4 — ``-fsyntax-only``: codegen-only diagnostics are not this gate's bug
  class, and no objects means no launcher/cache interplay.
* D6 — TUs are rendered from the CURRENT CHECKOUT (never the workspace's
  ``src/``); only the command comes from the workspace, and the workspace's
  recorded baseline fingerprint must match the checkout's compatibility
  baseline or the leg skips with the drift named. The toolchain version is
  recorded in failure output, not enforced.

CACHE HYGIENE: this test shares the bench's precious warm cache. It must
never write under the workspace or ``HPX_CACHE_DIR`` — all scratch TUs go
to ``tmp_path``. Reads only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from helia_profiler.hostenv.cache_dirs import hpx_cache_root
from helia_profiler.deps.compatibility import load_compatibility_baseline
from tests.contracts.test_firmware_render_snapshots import (
    _ENGINES,
    _common_kwargs,
    _jinja_env,
    _render,
)


# ---------------------------------------------------------------------------
# The matrix (D3) — one row per (workspace leg, target, render arm).
# rtt-only: power/busy arms are rtt-only and transport variation is Tier 1's
# job. apollo510 covers every engine family; apollo330P is the divergent-HAL,
# no-ITCM leg. AP3/AP4 and armclang/ATfE legs are growth points: they join
# this table when the bench warms their workspaces.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _HwCase:
    case_id: str
    workspace: str  # <board>-<toolchain>-<engine> cache key
    board: str
    soc: str
    engine: str
    target: str  # hpx_profiler | hpx_profiler_power
    power_only: bool = False
    probe: str = "infer"
    #: engines whose build also compiles the standalone profiler TU
    extra_profiler_tu: bool = False


_MATRIX: tuple[_HwCase, ...] = (
    _HwCase(
        "510-rt-profile",
        "apollo510_evb-arm-none-eabi-gcc-helia-rt",
        "apollo510_evb",
        "apollo510",
        "helia-rt",
        "hpx_profiler",
        extra_profiler_tu=True,
    ),
    _HwCase(
        "510-rt-power",
        "apollo510_evb-arm-none-eabi-gcc-helia-rt",
        "apollo510_evb",
        "apollo510",
        "helia-rt",
        "hpx_profiler_power",
        power_only=True,
    ),
    _HwCase(
        "510-rt-power-busy",
        "apollo510_evb-arm-none-eabi-gcc-helia-rt",
        "apollo510_evb",
        "apollo510",
        "helia-rt",
        "hpx_profiler_power",
        power_only=True,
        probe="busy_loop",
    ),
    _HwCase(
        "510-tflm-profile",
        "apollo510_evb-arm-none-eabi-gcc-tflm",
        "apollo510_evb",
        "apollo510",
        "tflm",
        "hpx_profiler",
        extra_profiler_tu=True,
    ),
    _HwCase(
        "510-tflm-power",
        "apollo510_evb-arm-none-eabi-gcc-tflm",
        "apollo510_evb",
        "apollo510",
        "tflm",
        "hpx_profiler_power",
        power_only=True,
    ),
    _HwCase(
        "510-aot-profile",
        "apollo510_evb-arm-none-eabi-gcc-helia-aot",
        "apollo510_evb",
        "apollo510",
        "helia-aot",
        "hpx_profiler",
    ),
    _HwCase(
        "510-aot-profile-busy",
        "apollo510_evb-arm-none-eabi-gcc-helia-aot",
        "apollo510_evb",
        "apollo510",
        "helia-aot",
        "hpx_profiler",
        probe="busy_loop",
    ),
    _HwCase(
        "510-et-profile",
        "apollo510_evb-arm-none-eabi-gcc-executorch",
        "apollo510_evb",
        "apollo510",
        "executorch",
        "hpx_profiler",
    ),
    _HwCase(
        "510-et-profile-busy",
        "apollo510_evb-arm-none-eabi-gcc-executorch",
        "apollo510_evb",
        "apollo510",
        "executorch",
        "hpx_profiler",
        probe="busy_loop",
    ),
    _HwCase(
        "330-rt-profile",
        "apollo330mP_evb-arm-none-eabi-gcc-helia-rt",
        "apollo330mP_evb",
        "apollo330P",
        "helia-rt",
        "hpx_profiler",
        extra_profiler_tu=True,
    ),
    _HwCase(
        "330-tflm-power",
        "apollo330mP_evb-arm-none-eabi-gcc-tflm",
        "apollo330mP_evb",
        "apollo330P",
        "tflm",
        "hpx_profiler_power",
        power_only=True,
    ),
    _HwCase(
        "330-aot-profile",
        "apollo330mP_evb-arm-none-eabi-gcc-helia-aot",
        "apollo330mP_evb",
        "apollo330P",
        "helia-aot",
        "hpx_profiler",
    ),
)


#: Known real-toolchain render bugs — same strict semantics as Tier 1's
#: ledger: an entry keeps its case red-with-reason, and a case that starts
#: compiling fails this test until its entry is removed.
_EXPECTED_HW_BUGS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Workspace resolution (D3/D6) — read-only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Workspace:
    app_dir: Path
    build_dir: Path
    compiler: Path
    ninja_text: str = field(repr=False)


def _resolve_workspace(case: _HwCase) -> _Workspace | str:
    """The newest fingerprinted workspace for this leg, or a skip reason.

    Resolves strictly through the post-#212 ``dependency-workspaces/<fp>/``
    layout — the legacy ``profiler_app`` root that may coexist beside it is
    a stale pre-#212 tree whose renders/flags predate current templates
    (the survey's phantom-failure trap).
    """
    root = hpx_cache_root() / "workspaces" / case.workspace / "dependency-workspaces"
    candidates = sorted(
        root.glob("*/profiler_app"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return f"no warm workspace under {root}"
    # Newest-first, FIRST FULLY-RESOLVING candidate wins (#225): the
    # cache legitimately holds fingerprint dirs from several branches/
    # baselines, mtime-ordered by whichever branch last profiled — a stale
    # branch's newer workspace must not disable the leg while a matching
    # one sits beside it.
    reasons: list[str] = []
    for app_dir in candidates:
        resolved = _resolve_candidate(app_dir, case)
        if isinstance(resolved, _Workspace):
            return resolved
        name = app_dir.parent.name
        reasons.append(f"{name[:12] + '…' if len(name) > 12 else name}: {resolved}")
    return "; ".join(reasons)


def _resolve_candidate(app_dir: Path, case: _HwCase) -> _Workspace | str:
    build_dir = app_dir / "build" / case.board
    ninja = build_dir / "build.ninja"
    if not ninja.is_file():
        return f"no build.ninja for {case.board}"

    # D6: the workspace must have been built against the checkout's baseline
    # — and unknown is not verified (#225): a workspace that records
    # no fingerprint predates provenance and is exactly the stale kind.
    deps_file = app_dir / "hpx-dependencies.json"
    recorded = None
    if deps_file.is_file():
        recorded = (
            json.loads(deps_file.read_text()).get("workspace", {}).get("baseline_fingerprint")
        )
    if recorded is None:
        return "records no baseline fingerprint"
    current = load_compatibility_baseline().fingerprint
    if recorded != current:
        return (
            f"baseline fingerprint {recorded[:12]}… != checkout {current[:12]}… "
            "— re-run a profile to refresh"
        )

    rules = build_dir / "CMakeFiles" / "rules.ninja"
    compiler = _compiler_from_rules(rules.read_text()) if rules.is_file() else None
    if compiler is None:
        return f"could not read CXX compiler from {rules}"
    if not compiler.is_file():
        return f"compiler {compiler} is not installed here"
    return _Workspace(
        app_dir=app_dir,
        build_dir=build_dir,
        compiler=compiler,
        ninja_text=ninja.read_text(),
    )


def _compiler_from_rules(rules_text: str) -> Path | None:
    """The C++ compiler the workspace was configured with (D1: inherit, never
    guess from PATH).

    The command token may be prefixed by ninja variable references
    (``${LAUNCHER}${CODE_CHECK}/usr/.../arm-none-eabi-g++`` when sccache or
    a checker is configured) — strip them; only the compiler path is ours.
    """
    m = re.search(r"^\s*command = (\S+)", _rule_block(rules_text, "CXX_COMPILER__"), re.M)
    if m is None:
        return None
    token = re.sub(r"^(?:\$\{\w+\})+", "", m.group(1))
    return Path(token) if token else None


def _rule_block(rules_text: str, rule_prefix: str) -> str:
    m = re.search(rf"^rule {re.escape(rule_prefix)}\S*\n((?:^\s+\S[^\n]*\n)+)", rules_text, re.M)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# build.ninja per-TU stanza parsing (D1).
# ---------------------------------------------------------------------------


def _tu_variables(ninja_text: str, target: str, source: str) -> dict[str, str] | None:
    """DEFINES/FLAGS/INCLUDES for one build statement, ninja-unescaped.

    Stanza-anchored, not offset-anchored: matches the ``build
    CMakeFiles/<target>.dir/<source>.obj:`` statement and reads its indented
    ``key = value`` block. Tolerates ``$``-escapes (ninja escapes ``$``,
    space and ``:``; none occur in cache paths today, but ``$$`` handling is
    cheap insurance) and absent LAUNCHER lines (no sccache).
    """
    pattern = (
        rf"^build CMakeFiles/{re.escape(target)}\.dir/{re.escape(source)}\.obj:"
        rf"[^\n]*\n((?:  \w+ = ?[^\n]*\n)+)"
    )
    m = re.search(pattern, ninja_text, re.M)
    if m is None:
        return None
    return dict(re.findall(r"  (\w+) = ?(.*)", m.group(1)))


def _split_ninja(value: str) -> list[str]:
    """Tokenize a ninja variable value, honouring its escapes.

    ``$$`` is a literal ``$``, ``$ `` a literal space, ``$:`` a colon —
    tokens split on UNescaped spaces only, then each token unescapes, so
    ``-I/opt/Arm$ GNU/include`` stays one argv entry (#225: the
    old unescape-then-split round trip corrupted exactly the case the
    unescaping existed for).
    """
    protected = value.replace("$$", "\x00").replace("$ ", "\x01").replace("$:", ":")
    return [token.replace("\x01", " ").replace("\x00", "$") for token in protected.split()]


def _vendor_headers_as_system(includes: list[str], app_dir: Path) -> list[str]:
    """D2: vendored module headers are not ours to clean — ``-isystem`` them
    so a diagnostic located inside them (``AM_SHARED_RW``) does not gate the
    rendered TU, which stays fully ``-Werror``."""
    del app_dir  # module dirs are recognised by path shape, not root
    return [flag.replace("-I", "-isystem", 1) if "/modules/" in flag else flag for flag in includes]


def _compile_command(
    workspace: _Workspace, case: _HwCase, scratch: Path, tu: Path
) -> list[str] | str:
    source = "src/main_power.cc" if case.target == "hpx_profiler_power" else "src/main.cc"
    variables = _tu_variables(workspace.ninja_text, case.target, source)
    if variables is None:
        return f"no ninja stanza for {case.target}/{source}"
    includes = _split_ninja(variables.get("INCLUDES", ""))
    workspace_src = workspace.app_dir / "src"
    # The scratch dir replaces the workspace src include so the CURRENT
    # CHECKOUT's rendered headers win (D6); keep everything else verbatim.
    includes = [f"-I{scratch}" if inc == f"-I{workspace_src}" else inc for inc in includes]
    includes = _vendor_headers_as_system(includes, workspace.app_dir)
    flags = [
        # -MMD/-MP/-MD write .d depfiles into the CWD even under
        # -fsyntax-only — a filesystem side effect this read-only gate must
        # not have. Dependency output is meaningless for a syntax check.
        flag
        for flag in _split_ninja(variables.get("FLAGS", ""))
        if flag not in ("-MMD", "-MP", "-MD")
    ]
    return [
        str(workspace.compiler),
        *_split_ninja(variables.get("DEFINES", "")),
        *includes,
        *flags,
        "-Werror",
        "-fsyntax-only",
        str(tu),
    ]


# ---------------------------------------------------------------------------
# Rendering from the current checkout (D6).
# ---------------------------------------------------------------------------


def _prepare_case(case: _HwCase, workspace: _Workspace, tmp_path: Path) -> tuple[Path, list[Path]]:
    scratch = tmp_path / case.case_id
    scratch.mkdir()
    overrides = {}
    if case.engine == "helia-aot":
        # The workspace's generated model module carries its real prefix;
        # render against it so the include resolves to the real header.
        prefix = _aot_prefix_in(workspace.app_dir)
        if prefix is not None:
            overrides = {"aot_prefix": prefix}
    text = _render(
        case.soc,
        "rtt",
        case.engine,
        power_only=case.power_only,
        clean_window_probe=case.probe,
        overrides=overrides or None,
    )
    # Tier 1's vacuity rule: an empty or truncated render must not pass
    # -fsyntax-only trivially (#225).
    assert "int main(" in text, f"[{case.case_id}] render has no 'int main(' — vacuous TU"
    main_tu = scratch / ("main_power.cc" if case.power_only else "main.cc")
    main_tu.write_text(text)
    tus = [main_tu]

    kwargs = _common_kwargs(case.soc, "rtt")
    (scratch / "hpx_pmu_profiler.h").write_text(
        _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
            cmsis_device_header=kwargs["cmsis_device_header"],
            profiling_backends=list(kwargs["profiling_backends"]),
            has_armv8m_pmu=kwargs["has_armv8m_pmu"],
            pmu_max_ops=kwargs["pmu_max_ops"],
        )
    )
    if case.extra_profiler_tu:
        profiler_text = _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
            profiling_backends=list(kwargs["profiling_backends"]),
            has_armv8m_pmu=kwargs["has_armv8m_pmu"],
        )
        assert "HpxPmuProfiler::" in profiler_text, f"[{case.case_id}] vacuous profiler TU"
        profiler_tu = scratch / "hpx_pmu_profiler.cc"
        profiler_tu.write_text(profiler_text)
        tus.append(profiler_tu)
    # Generated inputs production writes next to the sources, not templates:
    # carry them from the workspace so includes resolve (read-only copy).
    model_data = workspace.app_dir / "src" / "model_data.h"
    if model_data.is_file():
        shutil.copy(model_data, scratch / "model_data.h")
    return scratch, tus


def _aot_prefix_in(app_dir: Path) -> str | None:
    for header in (app_dir / "src").glob("*_model.h"):
        return header.name.removesuffix("_model.h")
    for module in app_dir.glob("modules/*_model"):
        return module.name.removesuffix("_model")
    return None


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


def _require_all_legs() -> bool:
    """HPX_COMPILE_HW_REQUIRE_ALL: any value except off-like ones arms it —
    a workflow author writing "true" must not silently lose enforcement
    (#225 2)."""
    return os.environ.get("HPX_COMPILE_HW_REQUIRE_ALL", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


@pytest.mark.compile_hw
def test_rendered_firmware_compiles_with_the_real_toolchain(tmp_path: Path):
    """Aggregated like Tier 1: every failure reported together, strict
    expected-bug semantics, per-leg skip reasons listed when nothing ran."""
    runnable: list[tuple[_HwCase, _Workspace]] = []
    skipped: list[str] = []
    for case in _MATRIX:
        resolved = _resolve_workspace(case)
        if isinstance(resolved, str):
            skipped.append(f"{case.case_id}: {resolved}")
        else:
            runnable.append((case, resolved))
    if not runnable:
        status = (
            "no warm dependency workspace for any matrix leg — run on the "
            "bench after a profile/validate. Legs:\n  " + "\n  ".join(skipped)
        )
        # The MOST degraded run must not be the one that stays green: with
        # REQUIRE_ALL armed, a wiped cache or mistyped HPX_CACHE_DIR in the
        # workflow is a failure, not a skip (#225 2).
        if _require_all_legs():
            pytest.fail(status)
        pytest.skip(status)
    # A partial run must be DISTINGUISHABLE from a full one (#225):
    # a green nightly where nine legs silently never compiled is the gate
    # lying. Skipped legs surface as a warning every run, and
    # HPX_COMPILE_HW_REQUIRE_ALL=1 turns any partial run into a failure
    # (the bench workflow's setting, once every leg's workspace is warm).
    if skipped:
        status = f"compile_hw ran {len(runnable)}/{len(_MATRIX)} legs; skipped:\n  " + "\n  ".join(
            skipped
        )
        if _require_all_legs():
            pytest.fail(status)
        warnings.warn(status, stacklevel=1)

    jobs: list[tuple[_HwCase, Path, list[str] | str]] = []
    for case, workspace in runnable:
        scratch, tus = _prepare_case(case, workspace, tmp_path)
        for tu in tus:
            command = _compile_command(workspace, case, scratch, tu)
            jobs.append((case, tu, command))

    def run(job: tuple[_HwCase, Path, list[str] | str]):
        case, tu, command = job
        if isinstance(command, str):
            return case, tu, 1, f"[harness] {command}", ""
        result = subprocess.run(command, capture_output=True, text=True)
        return case, tu, result.returncode, result.stderr, " ".join(command)

    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        results = list(pool.map(run, jobs))

    failures = []
    stale = []
    compiled = set()
    for case, tu, returncode, stderr, command in results:
        job_id = f"{case.case_id}/{tu.name}"
        compiled.add(case.case_id)
        expected = case.case_id in _EXPECTED_HW_BUGS or job_id in _EXPECTED_HW_BUGS
        if returncode != 0 and not expected:
            head = "\n".join(stderr.splitlines()[:30])
            failures.append(f"[{job_id}]\n$ {command}\n{head}")
        elif returncode == 0 and job_id in _EXPECTED_HW_BUGS:
            stale.append(job_id)
    for case_id in list(_EXPECTED_HW_BUGS):
        if case_id in compiled and all(
            rc == 0 for c, _t, rc, *_ in results if c.case_id == case_id
        ):
            stale.append(case_id)
    if failures:
        # Lazy and deduped on purpose: green runs spawn no subprocesses,
        # and N legs sharing one compiler probe it once (#225 2).
        compilers = {ws.compiler for _case, ws in runnable}
        versions = "; ".join(
            f"{compiler}: {_compiler_version(compiler)}" for compiler in sorted(compilers)
        )
        pytest.fail(
            f"{len(failures)} rendered TUs rejected by the real toolchain "
            f"({versions}):\n\n" + "\n\n".join(failures)
        )
    assert not stale, (
        "stale _EXPECTED_HW_BUGS entries (cases now compile — remove them): "
        + ", ".join(sorted(set(stale)))
    )


def _compiler_version(compiler: Path) -> str:
    """Recorded, never enforced (D6): failure output names the exact
    toolchain so a compiler-upgrade warning change reads as what it is."""
    try:
        out = subprocess.run(
            [str(compiler), "--version"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.splitlines()[0] if out.stdout else "version unknown"
    except (OSError, subprocess.SubprocessError):
        # TimeoutExpired is a SubprocessError, NOT an OSError — a hanging
        # compiler wrapper must degrade to "unknown", never crash the gate.
        return "version unknown"


def test_matrix_covers_every_engine_family():
    """Matrix-drift guard: a new engine (e.g. an atomiq110 NPU backend)
    must grow a Tier-2 leg, loudly."""
    assert {case.engine for case in _MATRIX} == set(_ENGINES), (
        "the Tier-2 matrix no longer spans the render engine set — add a "
        "leg per new engine (and a warm-workspace note) or record why not"
    )
    assert {case.probe for case in _MATRIX} == {"infer", "busy_loop"}
    assert any(case.power_only for case in _MATRIX)
    assert {case.soc for case in _MATRIX} == {"apollo510", "apollo330P"}
    # The exact leg set (#225): every individual row was deletable
    # without tripping the coverage sets above — a removed leg must be as
    # loud as a missing engine.
    assert {case.case_id for case in _MATRIX} == {
        "510-rt-profile",
        "510-rt-power",
        "510-rt-power-busy",
        "510-tflm-profile",
        "510-tflm-power",
        "510-aot-profile",
        "510-aot-profile-busy",
        "510-et-profile",
        "510-et-profile-busy",
        "330-rt-profile",
        "330-tflm-power",
        "330-aot-profile",
    }, "the Tier-2 leg set changed — deliberate? update this pin with the reason"


class TestWorkspaceResolution:
    """#225 F3/F4: candidate fallback and provenance, on synthetic
    cache trees (portable: the 'compiler' is any existing file)."""

    def _make_tree(self, root: Path, case: _HwCase, name: str, *, fingerprint, ninja=True):
        import sys

        app = root / "workspaces" / case.workspace / "dependency-workspaces" / name / "profiler_app"
        build = app / "build" / case.board
        (build / "CMakeFiles").mkdir(parents=True)
        if ninja:
            (build / "build.ninja").write_text(
                f"build CMakeFiles/{case.target}.dir/src/main.cc.obj: C ../src/main.cc\n"
                "  DEFINES = -DX\n  INCLUDES = -I/ws/src\n  FLAGS = -O2\n"
            )
        (build / "CMakeFiles" / "rules.ninja").write_text(
            "rule CXX_COMPILER__x\n  command = " + sys.executable + " $FLAGS -c $in\n"
        )
        if fingerprint is not None:
            (app / "hpx-dependencies.json").write_text(
                json.dumps({"workspace": {"baseline_fingerprint": fingerprint}})
            )
        return app

    def test_newer_broken_workspace_falls_back_to_a_matching_older_one(
        self, tmp_path: Path, monkeypatch
    ):
        import time

        case = _MATRIX[0]
        current = load_compatibility_baseline().fingerprint
        good = self._make_tree(tmp_path, case, "old-good", fingerprint=current)
        bad = self._make_tree(tmp_path, case, "new-drifted", fingerprint="deadbeef")
        past = time.time() - 1000
        for path in good.rglob("*"):
            os.utime(path, (past, past))
        os.utime(good, (past, past))
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))

        resolved = _resolve_workspace(case)

        assert isinstance(resolved, _Workspace), resolved
        assert resolved.app_dir == good
        del bad

    def test_a_workspace_without_provenance_is_skipped_not_trusted(
        self, tmp_path: Path, monkeypatch
    ):
        case = _MATRIX[0]
        self._make_tree(tmp_path, case, "no-provenance", fingerprint=None)
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))

        resolved = _resolve_workspace(case)

        assert isinstance(resolved, str)
        assert "records no baseline fingerprint" in resolved

    def test_all_candidates_failing_names_each_reason(self, tmp_path: Path, monkeypatch):
        case = _MATRIX[0]
        self._make_tree(tmp_path, case, "aaaa-no-ninja", fingerprint="x", ninja=False)
        self._make_tree(tmp_path, case, "bbbb-drifted", fingerprint="deadbeef")
        monkeypatch.setenv("HPX_CACHE_DIR", str(tmp_path))

        resolved = _resolve_workspace(case)

        assert isinstance(resolved, str)
        assert "no build.ninja" in resolved and "deadbeef" in resolved


# ---------------------------------------------------------------------------
# Parser robustness (lens plan): synthetic stanzas, not just the live file.
# ---------------------------------------------------------------------------


_SYNTHETIC_NINJA = """\
# CMake generated file
build CMakeFiles/hpx_profiler.dir/src/main.cc.obj: CXX_COMPILER__x ../src/main.cc || order
  DEFINES = -DAM_PART_APOLLO510 -DBUFFER_SIZE_UP=32768
  INCLUDES = -I/ws/src -isystem /ws/modules/nsx-core/includes-api -I/with$ space/dir
  FLAGS = -O3 -std=gnu++17 -Wall
  LAUNCHER = /usr/local/bin/sccache

build CMakeFiles/hpx_profiler_power.dir/src/main_power.cc.obj: CXX_COMPILER__x ../src/main_power.cc
  DEFINES = -DPOWER=1 -DCOST=a$$b
  INCLUDES = -I/ws/src
  FLAGS = -Os
"""


class TestNinjaStanzaParser:
    def test_reads_the_right_targets_stanza(self):
        v = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler", "src/main.cc")
        assert v is not None
        assert v["DEFINES"] == "-DAM_PART_APOLLO510 -DBUFFER_SIZE_UP=32768"
        assert v["FLAGS"] == "-O3 -std=gnu++17 -Wall"
        power = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler_power", "src/main_power.cc")
        assert power is not None
        assert _split_ninja(power["DEFINES"]) == ["-DPOWER=1", "-DCOST=a$b"]

    def test_unescapes_ninja_dollar_forms(self):
        """Split BEFORE unescape (#225): an escaped space must stay
        inside ONE argv token, not be unescaped and then split apart."""
        v = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler", "src/main.cc")
        assert v is not None
        tokens = _split_ninja(v["INCLUDES"])
        assert "-I/with space/dir" in tokens
        assert "-I/with" not in tokens

    def test_launcher_is_ignored_and_optional(self):
        with_launcher = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler", "src/main.cc")
        assert with_launcher is not None
        assert set(with_launcher) == {"DEFINES", "INCLUDES", "FLAGS", "LAUNCHER"}
        without = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler_power", "src/main_power.cc")
        assert without is not None and "LAUNCHER" not in without
        # The launcher must never reach the compile argv.
        ws = _Workspace(
            app_dir=Path("/ws"),
            build_dir=Path("/ws/build/b"),
            compiler=Path("/usr/bin/g++"),
            ninja_text=_SYNTHETIC_NINJA,
        )
        command = _compile_command(ws, _MATRIX[0], Path("/scratch"), Path("/scratch/main.cc"))
        assert isinstance(command, list)
        assert not any("sccache" in token for token in command)

    def test_depfile_flags_are_stripped_from_the_command(self):
        """-MMD writes .d files into the CWD even under -fsyntax-only — the
        gate is read-only and must have no filesystem side effects."""
        ninja = (
            "build CMakeFiles/hpx_profiler.dir/src/main.cc.obj: C ../src/main.cc\n"
            "  DEFINES = -DX\n"
            "  INCLUDES = -I/ws/src\n"
            "  FLAGS = -O3 -MMD -MP -Wall\n"
        )
        ws = _Workspace(
            app_dir=Path("/ws"),
            build_dir=Path("/ws/build/b"),
            compiler=Path("/usr/bin/g++"),
            ninja_text=ninja,
        )
        case = _MATRIX[0]
        command = _compile_command(ws, case, Path("/scratch"), Path("/scratch/main.cc"))
        assert isinstance(command, list)
        assert "-MMD" not in command and "-MP" not in command
        assert "-Wall" in command and "-Werror" in command

    def test_missing_stanza_is_none_not_a_guess(self):
        assert _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler", "src/other.cc") is None

    def test_vendor_modules_become_isystem_and_nothing_else_does(self):
        flags = ["-I/ws/src", "-isystem", "/x", "-I/ws/modules/nsx-core/includes-api"]
        out = _vendor_headers_as_system(flags, Path("/ws"))
        assert out[0] == "-I/ws/src"
        assert out[3] == "-isystem/ws/modules/nsx-core/includes-api"

    def test_compiler_comes_from_the_rules_file(self):
        rules = (
            "rule CXX_COMPILER__hpx_profiler_unscanned_Release\n"
            "  depfile = $DEP_FILE\n"
            "  command = /usr/local/arm-gnu-toolchain/bin/arm-none-eabi-g++ "
            "$DEFINES $INCLUDES $FLAGS -c $in -o $out\n"
        )
        assert _compiler_from_rules(rules) == Path(
            "/usr/local/arm-gnu-toolchain/bin/arm-none-eabi-g++"
        )

    def test_launcher_variable_prefixes_are_stripped_from_the_compiler(self):
        rules = (
            "rule CXX_COMPILER__hpx_profiler_unscanned_Release\n"
            "  command = ${LAUNCHER}${CODE_CHECK}/usr/local/arm-gnu-toolchain"
            "/bin/arm-none-eabi-g++ $FLAGS -c $in -o $out\n"
        )
        assert _compiler_from_rules(rules) == Path(
            "/usr/local/arm-gnu-toolchain/bin/arm-none-eabi-g++"
        )
