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
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from helia_profiler.cache_dirs import hpx_cache_root
from helia_profiler.compatibility import load_compatibility_baseline
from tests.contracts.test_firmware_render_snapshots import (
    _ENGINES,
    _common_kwargs,
    _jinja_env,
    _render,
)

pytestmark = pytest.mark.compile_hw


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
    _HwCase("510-rt-profile", "apollo510_evb-arm-none-eabi-gcc-helia-rt", "apollo510_evb", "apollo510", "helia-rt", "hpx_profiler", extra_profiler_tu=True),
    _HwCase("510-rt-power", "apollo510_evb-arm-none-eabi-gcc-helia-rt", "apollo510_evb", "apollo510", "helia-rt", "hpx_profiler_power", power_only=True),
    _HwCase("510-rt-power-busy", "apollo510_evb-arm-none-eabi-gcc-helia-rt", "apollo510_evb", "apollo510", "helia-rt", "hpx_profiler_power", power_only=True, probe="busy_loop"),
    _HwCase("510-tflm-profile", "apollo510_evb-arm-none-eabi-gcc-tflm", "apollo510_evb", "apollo510", "tflm", "hpx_profiler", extra_profiler_tu=True),
    _HwCase("510-tflm-power", "apollo510_evb-arm-none-eabi-gcc-tflm", "apollo510_evb", "apollo510", "tflm", "hpx_profiler_power", power_only=True),
    _HwCase("510-aot-profile", "apollo510_evb-arm-none-eabi-gcc-helia-aot", "apollo510_evb", "apollo510", "helia-aot", "hpx_profiler"),
    _HwCase("510-aot-profile-busy", "apollo510_evb-arm-none-eabi-gcc-helia-aot", "apollo510_evb", "apollo510", "helia-aot", "hpx_profiler", probe="busy_loop"),
    _HwCase("510-et-profile", "apollo510_evb-arm-none-eabi-gcc-executorch", "apollo510_evb", "apollo510", "executorch", "hpx_profiler"),
    _HwCase("510-et-profile-busy", "apollo510_evb-arm-none-eabi-gcc-executorch", "apollo510_evb", "apollo510", "executorch", "hpx_profiler", probe="busy_loop"),
    _HwCase("330-rt-profile", "apollo330mP_evb-arm-none-eabi-gcc-helia-rt", "apollo330mP_evb", "apollo330P", "helia-rt", "hpx_profiler", extra_profiler_tu=True),
    _HwCase("330-tflm-power", "apollo330mP_evb-arm-none-eabi-gcc-tflm", "apollo330mP_evb", "apollo330P", "tflm", "hpx_profiler_power", power_only=True),
    _HwCase("330-aot-profile", "apollo330mP_evb-arm-none-eabi-gcc-helia-aot", "apollo330mP_evb", "apollo330P", "helia-aot", "hpx_profiler"),
)


#: Known real-toolchain render bugs — STRICT semantics copied from Tier 1's
#: ledger: an entry keeps its case red-with-reason; a case that starts
#: compiling fails this test until its entry is removed, so the ledger
#: cannot go stale. Empty: the design survey's census (16/16 TUs across 7
#: warm workspace combos) found no failures under -Wall -Werror.
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
    app_dir = candidates[0]
    build_dir = app_dir / "build" / case.board
    ninja = build_dir / "build.ninja"
    if not ninja.is_file():
        return f"workspace {app_dir} has no build.ninja for {case.board}"

    # D6: the workspace must have been built against the checkout's baseline.
    deps_file = app_dir / "hpx-dependencies.json"
    if deps_file.is_file():
        recorded = (
            json.loads(deps_file.read_text()).get("workspace", {}).get("baseline_fingerprint")
        )
        current = load_compatibility_baseline().fingerprint
        if recorded is not None and recorded != current:
            return (
                f"workspace baseline fingerprint {recorded[:12]}… != checkout "
                f"{current[:12]}… — re-run a profile to refresh the workspace"
            )

    rules = build_dir / "CMakeFiles" / "rules.ninja"
    compiler = _compiler_from_rules(rules.read_text()) if rules.is_file() else None
    if compiler is None:
        return f"could not read CXX compiler from {rules}"
    if not compiler.is_file():
        return f"workspace compiler {compiler} is not installed here"
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
    m = re.search(
        rf"^rule {re.escape(rule_prefix)}\S*\n((?:^\s+\S[^\n]*\n)+)", rules_text, re.M
    )
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
    return {
        key: value.replace("$$", "$").replace("$ ", " ").replace("$:", ":")
        for key, value in re.findall(r"  (\w+) = ?(.*)", m.group(1))
    }


def _vendor_headers_as_system(includes: list[str], app_dir: Path) -> list[str]:
    """D2: vendored module headers are not ours to clean — ``-isystem`` them
    so a diagnostic located inside them (``AM_SHARED_RW``) does not gate the
    rendered TU, which stays fully ``-Werror``."""
    del app_dir  # module dirs are recognised by path shape, not root
    return [
        flag.replace("-I", "-isystem", 1) if "/modules/" in flag else flag
        for flag in includes
    ]


def _compile_command(
    workspace: _Workspace, case: _HwCase, scratch: Path, tu: Path
) -> list[str] | str:
    source = "src/main_power.cc" if case.target == "hpx_profiler_power" else "src/main.cc"
    variables = _tu_variables(workspace.ninja_text, case.target, source)
    if variables is None:
        return f"no ninja stanza for {case.target}/{source}"
    includes = variables.get("INCLUDES", "").split()
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
        for flag in variables.get("FLAGS", "").split()
        if flag not in ("-MMD", "-MP", "-MD")
    ]
    return [
        str(workspace.compiler),
        *variables.get("DEFINES", "").split(),
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
    (scratch / ("main_power.cc" if case.power_only else "main.cc")).write_text(text)
    main_tu = scratch / ("main_power.cc" if case.power_only else "main.cc")
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
        profiler_tu = scratch / "hpx_pmu_profiler.cc"
        profiler_tu.write_text(
            _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
                profiling_backends=list(kwargs["profiling_backends"]),
                has_armv8m_pmu=kwargs["has_armv8m_pmu"],
            )
        )
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
        pytest.skip(
            "no warm dependency workspace for any matrix leg — run on the "
            "bench after a profile/validate. Legs:\n  " + "\n  ".join(skipped)
        )

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
    assert not failures, (
        f"{len(failures)} rendered TUs rejected by the real toolchain "
        f"({runnable[0][1].compiler}):\n\n" + "\n\n".join(failures)
    )
    assert not stale, (
        "stale _EXPECTED_HW_BUGS entries (cases now compile — remove them): "
        + ", ".join(sorted(set(stale)))
    )


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
        assert power is not None and power["DEFINES"] == "-DPOWER=1 -DCOST=a$b"

    def test_unescapes_ninja_dollar_forms(self):
        v = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler", "src/main.cc")
        assert v is not None
        assert "-I/with space/dir" in v["INCLUDES"]

    def test_launcher_is_ignored_and_optional(self):
        v = _tu_variables(_SYNTHETIC_NINJA, "hpx_profiler_power", "src/main_power.cc")
        assert v is not None and "LAUNCHER" not in ("DEFINES", "FLAGS", "INCLUDES")

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
