"""Contract: every rendered firmware TU compiles under host GNU g++ (#187 Tier 1).

The snapshot suite (``test_firmware_render_snapshots.py``) pins renders by
sha256, which cannot see whether a render *compiles* — an undeclared
identifier in one render arm, a printf format/arg mismatch, or an orphaned
variable all render fine and stayed invisible until a bench build (#171
round 2 is the canonical case).  This gate closes that hole: it renders the
snapshot module's full scenario matrix (every SoC x transport x engine,
including the power_only and busy_loop variants), the wire census matrix
(``test_wire_protocol._MATRIX`` — every condition-variant override set),
the ``hpx_pmu_profiler.cc`` second TU per SoC, and a full-resolver-plan TU,
then syntax-checks each unique TU with host
``g++ -fsyntax-only -std=gnu++17 -Wall -Werror -Wformat`` against an
hpx-owned stub include tree (``tests/fixtures/compile_stubs/``): one
minimal header per vendor include, declaring exactly the symbols the
templates use.

Stub maintenance rule (#187): a template that starts using a new vendor
symbol fails this gate until the stub declares it — loud by construction,
and the stub diff rides the template PR (same discipline as the wire
census).  See maintainers/compile-gate.md.

``hpx_printf`` has no format attribute in the templates (the vendor printf
path is variadic), so the harness force-includes a per-case prelude that
declares it with ``__attribute__((format(printf, 1, 2)))`` before the TU's
own definition — that is what arms ``-Wformat`` for the profiler's actual
output path.

The three ``test_gate_fails_on_*`` self-tests are the acceptance criteria
from #187: each doctors a rendered TU at string level (no template edits)
and asserts the SAME harness invocation goes red, proving the gate can see
each observed bug class.

Scope / CI wiring: the gate runs only where a REAL GNU g++ exists (probed —
never trusted by name: on macOS ``g++`` is clang, whose ``-Wall`` implies
extra warnings (-Wunused-const-variable and friends) this flag set is not
tuned for, and a MinGW g++ on Windows brings the ms_printf format archetype).
Linux/GNU-on-ELF hosts run it as part of the normal suite; macOS and Windows
skip.  A clang lane with its own flag set is possible future work; Tier 2
(real arm-none-eabi-g++ against a dependency workspace) is a separate
bench-marked concern.

Runs in ~1 s wall: 150 enumerated scenarios dedup to 90 unique TUs
(~50-100 ms each), compiled in parallel.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pytest


def _find_gnu_gxx() -> str | None:
    """First candidate that is a REAL GNU g++ — probed, not trusted by name.

    macOS installs clang as ``g++``; MSYS/MinGW puts a Windows-targeting g++
    on PATH.  Both advertise the right name and fail this flag set for
    reasons that have nothing to do with the templates, so the probe rejects
    them: ``--version`` must not mention clang, ``-dumpmachine`` must not
    mention mingw.
    """
    for candidate in ("g++", "g++-14", "g++-13"):
        path = shutil.which(candidate)
        if path is None:
            continue
        try:
            version = subprocess.run([path, "--version"], capture_output=True, text=True)
            machine = subprocess.run([path, "-dumpmachine"], capture_output=True, text=True)
        except OSError:
            continue
        if version.returncode != 0 or machine.returncode != 0:
            continue
        if "clang" in version.stdout.lower():
            continue
        if "mingw" in machine.stdout.lower():
            continue
        return path
    return None


_GXX = _find_gnu_gxx()
if _GXX is None:
    pytest.skip(
        "no GNU g++ found (g++/g++-14/g++-13 probed; clang and MinGW rejected)",
        allow_module_level=True,
    )

# Imports live BELOW the module-level skip on purpose (noqa: E402): the
# census import renders its whole 79-TU matrix at import time, and a host
# with no GNU g++ should skip before paying for that.
from helia_profiler.firmware import _jinja_env  # noqa: E402
from helia_profiler.firmware.op_resolver import _ALL_REGISTRATIONS  # noqa: E402

# Reuse the render machinery and both scenario matrices wholesale: the
# snapshot module's matrix is the canonical "every arm renders" enumeration
# (#187 D2) and the wire census matrix carries every condition-variant
# override set.  Plain package imports (tests/contracts is a package), the
# same way test_wire_protocol imports the snapshot module — an importlib
# re-execution would re-run the snapshot module's _maybe_regenerate() and,
# under HPX_UPDATE_SNAPSHOTS=1, rewrite the snapshot JSON as a side effect.
from .test_firmware_render_snapshots import (  # noqa: E402
    _POWER_BUSY_LOOP_PROBE,
    _SNAPSHOTS,
    _all_combos,
    _common_kwargs,
    _key,
    _power_busy_loop_combos,
    _power_combos,
    _profile_busy_loop_combos,
    _render,
)
from .test_wire_protocol import _MATRIX as _CENSUS_MATRIX  # noqa: E402

_STUB_DIR = Path(__file__).parent.parent / "fixtures" / "compile_stubs"


# ---------------------------------------------------------------------------
# Case model: every compiled TU is (id, text, render vars), where the vars
# carry the SoC facts the generated per-case headers need
# (cmsis_device_header, profiling_backends, has_armv8m_pmu, pmu_max_ops).
# ---------------------------------------------------------------------------


@dataclass
class _CompileCase:
    case_id: str
    text: str
    vars: dict
    is_main_tu: bool = True
    aliases: list[str] = field(default_factory=list)


def _render_pmu_profiler_header(vars: dict) -> str:
    """Render hpx_pmu_profiler.h with the vars production hands it.

    Mirrors firmware/__init__.py (the hpx_pmu_profiler.h render site): the
    header's variables are sourced from the SoC exactly as
    FirmwareRenderContext sources them.
    """
    return _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
        cmsis_device_header=vars["cmsis_device_header"],
        profiling_backends=list(vars["profiling_backends"]),
        has_armv8m_pmu=vars["has_armv8m_pmu"],
        pmu_max_ops=vars["pmu_max_ops"],
    )


def _render_pmu_profiler_cc(vars: dict) -> str:
    """Render hpx_pmu_profiler.cc exactly as firmware/__init__.py does."""
    return _jinja_env.get_template("hpx_pmu_profiler.cc.j2").render(
        profiling_backends=list(vars["profiling_backends"]),
        has_armv8m_pmu=vars["has_armv8m_pmu"],
    )


def _snapshot_cases() -> list[tuple[str, tuple]]:
    """(case id, _render args) per snapshot-matrix scenario.

    Mirrors ``_build_all()`` in the snapshot module: the transport matrix,
    the power_only matrix, and both busy_loop matrices.
    """
    cases: list[tuple[str, tuple]] = []
    for soc, transport, engine in _all_combos():
        cases.append((_key(soc, transport, engine), (soc, transport, engine, False, "infer")))
    for soc, transport, engine in _power_combos():
        cases.append(
            (_key(soc, transport, engine, power_only=True), (soc, transport, engine, True, "infer"))
        )
    for soc, transport, engine in _power_busy_loop_combos():
        cases.append(
            (
                _key(
                    soc,
                    transport,
                    engine,
                    power_only=True,
                    clean_window_probe=_POWER_BUSY_LOOP_PROBE,
                ),
                (soc, transport, engine, True, _POWER_BUSY_LOOP_PROBE),
            )
        )
    for soc, transport, engine in _profile_busy_loop_combos():
        cases.append(
            (
                _key(soc, transport, engine, clean_window_probe=_POWER_BUSY_LOOP_PROBE),
                (soc, transport, engine, False, _POWER_BUSY_LOOP_PROBE),
            )
        )
    return cases


def test_compile_matrix_covers_every_snapshot_case():
    """The snapshot half of the compile matrix is the snapshot set, exactly.

    Set equality against the committed snapshot keys — not a count floor —
    so a case silently dropped from (or invented in) this harness's
    enumeration is named, not absorbed.
    """
    assert _SNAPSHOTS, "no firmware render snapshot committed"
    assert {case_id for case_id, _ in _snapshot_cases()} == set(_SNAPSHOTS)


#: One extra TU with the FULL mode="all" resolver plan: every
#: firmware/op_resolver.py _ALL_REGISTRATIONS line rendered into
#: get_resolver().  The stub resolver declares each Add* explicitly, so a
#: renamed production entry fails the gate (no catch-all).
_FULL_RESOLVER_ID = "apollo510|rtt|tflm|full-resolver"


def _build_cases() -> list[_CompileCase]:
    cases: list[_CompileCase] = []

    for case_id, (soc, transport, engine, power_only, probe) in _snapshot_cases():
        cases.append(
            _CompileCase(
                case_id=case_id,
                text=_render(soc, transport, engine, power_only=power_only, clean_window_probe=probe),
                vars=_common_kwargs(soc, transport),
            )
        )

    # Wire census matrix: 79 renders including every condition-variant
    # override set (PSRAM placements, AOT external arenas + const blobs,
    # Apollo3 burst, clean-window trace, auto window, power sync, hb-ms,
    # INA228).  _Render appends hpx_pmu_profiler.cc to the TFLM/heliaRT text
    # for its token census; strip that back off — the .cc is compiled as its
    # own TU below, as production builds it.
    for render in _CENSUS_MATRIX:
        text = render.text
        if render.engine.value in ("tflm", "helia-rt"):
            cc = _render_pmu_profiler_cc(render.vars)
            assert text.endswith(cc), (
                f"[{render.label}] census text does not end with the "
                "hpx_pmu_profiler.cc render — the census assembly changed; "
                "update this harness's strip logic"
            )
            text = text[: -len(cc)]
        cases.append(_CompileCase(case_id=f"census:{render.label}", text=text, vars=render.vars))

    # Full resolver plan (mode="all"): all 92 registrations, max_ops sized to
    # match, so every Add* call the production plan can emit is compiled.
    registrations = [code for _, code in _ALL_REGISTRATIONS]
    cases.append(
        _CompileCase(
            case_id=_FULL_RESOLVER_ID,
            text=_render(
                "apollo510",
                "rtt",
                "tflm",
                overrides={
                    "resolver_registrations": registrations,
                    "resolver_max_ops": len(registrations),
                },
            ),
            vars=_common_kwargs("apollo510", "rtt"),
        )
    )

    # hpx_pmu_profiler.cc: the second TU of every TFLM/heliaRT app
    # (CMakeLists.txt.j2 compiles it into both binaries), rendered per SoC
    # exactly as firmware/__init__.py renders it.
    for soc in ("apollo3p", "apollo4p", "apollo510"):
        vars = _common_kwargs(soc, "rtt")
        cases.append(
            _CompileCase(
                case_id=f"{soc}|hpx_pmu_profiler.cc",
                text=_render_pmu_profiler_cc(vars),
                vars=vars,
                is_main_tu=False,
            )
        )

    return cases


def _dedup(cases: list[_CompileCase]) -> list[_CompileCase]:
    """One compile per unique TU text (the census base matrix re-renders the
    snapshot matrix byte-identically); duplicate ids ride as aliases so a
    failure names every scenario it covers."""
    by_digest: dict[str, _CompileCase] = {}
    for case in cases:
        digest = hashlib.sha256(case.text.encode("utf-8")).hexdigest()
        if digest in by_digest:
            by_digest[digest].aliases.append(case.case_id)
        else:
            by_digest[digest] = case
    return list(by_digest.values())


# ---------------------------------------------------------------------------
# Per-case generated files + compile invocation
# ---------------------------------------------------------------------------

# The snapshot matrix pins weights_region="mram" (see _common_kwargs), and
# firmware/__init__.py's _model_to_header emits `static const` for mram —
# so the generated stand-in matches the const-ness the renders expect.
_MODEL_DATA_STUB = """\
// hpx compile-check generated model_data.h stand-in (#187)
#pragma once
alignas(16) static const unsigned char model_data[] = {0};
static const unsigned int model_data_len = 1;
"""

# heliaAOT engine header ({{engine_header}}): the matrix renders with
# aot_prefix="fake" (see the snapshot module's _render), so the generated
# model API is fake_model.h/fake_common.h.  Declares exactly the model API
# symbols main_aot.cc.j2 references, including the external-arena binding
# the census arena variants call.
_AOT_MODEL_STUB = """\
// hpx compile-check generated AOT engine-header stand-in (#187)
#pragma once
#include <stddef.h>
#include <stdint.h>

typedef enum {
    fake_op_state_run_started = 0,
    fake_op_state_run_finished,
} fake_operator_state_t;

typedef void (*fake_operator_callback_t)(
    int32_t op, fake_operator_state_t state, int32_t status, void *user_data);

typedef struct {
    void *data;
    size_t size;
} fake_io_buffer_t;

typedef struct {
    fake_operator_callback_t callback;
    void *user_data;
    fake_io_buffer_t inputs[4];
    fake_io_buffer_t outputs[4];
} fake_model_context_t;

typedef int32_t fake_arena_region_t;

static const int fake_num_inputs = 1;
static const int fake_num_outputs = 1;

static inline int32_t fake_model_init(fake_model_context_t *ctx) { (void)ctx; return 0; }
static inline int32_t fake_model_run(fake_model_context_t *ctx) { (void)ctx; return 0; }
static inline int32_t fake_bind_arena(fake_arena_region_t region, uint8_t *buf, size_t size) {
    (void)region;
    (void)buf;
    (void)size;
    return 0;
}
"""

_AOT_COMMON_STUB = """\
// hpx compile-check generated AOT engine-header stand-in (#187)
#pragma once
"""

# Constant-arena sidecar blobs: firmware/__init__.py generates one
# hpx_const_blob_<region_id>.h per blob-carrying arena region (via
# _blob_to_header); the stand-in mirrors its shape.
_CONST_BLOB_INCLUDE = re.compile(r'#include\s+"hpx_const_blob_(\d+)\.h"')


def _const_blob_stub(region_id: str) -> str:
    return (
        "// hpx compile-check generated const-blob stand-in (#187)\n"
        "#pragma once\n"
        "#include <stddef.h>\n"
        f"alignas(16) static const unsigned char hpx_const_blob_{region_id}[] = {{0}};\n"
        f"static const size_t hpx_const_blob_{region_id}_len = "
        f"sizeof(hpx_const_blob_{region_id});\n"
    )


def _part_define(vars: dict) -> str:
    """AM_PART_* define for this render's SoC, from its CMSIS device header.

    Production gets the part macro from the toolchain command line (CMake),
    not from an include, so the stub tree's part gates (burst, debug domain,
    SRAM config, cache capabilities) key on the same mechanism here.
    """
    stem = Path(str(vars["cmsis_device_header"])).stem
    return f"AM_PART_{stem.upper()}"


def _prepare_case_dir(case: _CompileCase, base: Path) -> Path:
    """Write the TU plus its generated per-case headers into a dir."""
    case_dir = base / re.sub(r"[^A-Za-z0-9_.-]+", "_", case.case_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "main.cc").write_text(case.text)
    if '#include "hpx_pmu_profiler.h"' in case.text or not case.is_main_tu:
        (case_dir / "hpx_pmu_profiler.h").write_text(_render_pmu_profiler_header(case.vars))
    if '#include "model_data.h"' in case.text:
        (case_dir / "model_data.h").write_text(_MODEL_DATA_STUB)
    if '#include "fake_model.h"' in case.text:
        (case_dir / "fake_model.h").write_text(_AOT_MODEL_STUB)
        (case_dir / "fake_common.h").write_text(_AOT_COMMON_STUB)
    for region_id in set(_CONST_BLOB_INCLUDE.findall(case.text)):
        (case_dir / f"hpx_const_blob_{region_id}.h").write_text(_const_blob_stub(region_id))
    # Arm -Wformat for the profiler's own printf: a prior declaration with the
    # format attribute merges into the TU's later definition.  Linkage must
    # match the render's printf_linkage ("static " on the heliaAOT template);
    # `unused` keeps a static declaration warning-free on any TU that never
    # defines it (hpx_pmu_profiler.cc only declares it extern).
    linkage = "static " if "static void hpx_printf" in case.text else ""
    (case_dir / "hpx_prelude.h").write_text(
        "#pragma once\n"
        f"{linkage}void hpx_printf(const char *fmt, ...) "
        "__attribute__((format(printf, 1, 2), unused));\n"
    )
    return case_dir


def _compile(case_dir: Path, part_define: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            _GXX,
            "-fsyntax-only",
            "-std=gnu++17",
            "-Wall",
            "-Werror",
            "-Wformat",
            f"-D{part_define}",
            "-include",
            str(case_dir / "hpx_prelude.h"),
            "-I",
            str(_STUB_DIR),
            "-I",
            str(case_dir),
            str(case_dir / "main.cc"),
        ],
        capture_output=True,
        text=True,
    )


#: Known render bugs the gate found on its first census sweep (#171 class) —
#: pre-existing template defects, recorded here as STRICT expected failures
#: rather than fixed, because the templates are out of this contract's
#: write scope.  Each entry keeps its case red-with-reason; fixing the
#: template makes the case compile and this test then FAILS until the entry
#: is removed (strict-xfail semantics), so the ledger cannot go stale.
_EXPECTED_RENDER_BUGS: dict[str, str] = {
    "census:ap510|rtt|tflm|psram-weights": (
        "main.cc.j2:183-189 — kArenaPsramOffset is computed under "
        "weights_region=='psram' but its only consumer is inside the "
        "arena_region=='psram' guard; weights-only-in-PSRAM renders trip "
        "-Wunused-variable"
    ),
    "census:ap510|swo|tflm|psram-weights": (
        "same kArenaPsramOffset defect as ap510|rtt|tflm|psram-weights "
        "(main.cc.j2:183-189), reached via the SWO transport arm"
    ),
    "census:ap510|rtt|executorch|psram-weights": (
        "_main_base.cc.j2:331-334 includes _psram_metadata.j2 for every "
        "psram_needed render, but main_executorch.cc.j2 renders no PSRAM "
        "init block, so psram_info is undeclared — an uncompilable render "
        "(production-unreachable: preflight rejects psram placement for "
        "executorch, the exact #171 shape)"
    ),
    "census:ap510|rtt|helia-aot|arenas-psram-noblob": (
        "main_aot.cc.j2:329/369 — hpx_arena_psram_offset_N is set for every "
        "PSRAM arena region but read only under region.blob_filename "
        "(main_aot.cc.j2:408); a PSRAM region without a sidecar blob trips "
        "-Wunused-but-set-variable"
    ),
}


def test_every_rendered_firmware_tu_compiles(tmp_path):
    """Every unique rendered TU syntax-checks clean under -Wall -Werror.

    One aggregated test rather than a parametrize: every failing case is
    reported together (first 30 stderr lines each), so a stub-tree gap that
    breaks a whole SoC family reads as one diff-shaped report instead of
    dozens of identical first-fail truncations.
    """
    cases = _dedup(_build_cases())
    for case in cases:
        # An empty or truncated render must not pass vacuously: every main
        # TU has an entry point, and the profiler TU defines the class.
        marker = "int main(" if case.is_main_tu else "HpxPmuProfiler::"
        assert marker in case.text, f"[{case.case_id}] render has no {marker!r} — vacuous TU"

    prepared = [(case, _prepare_case_dir(case, tmp_path)) for case in cases]

    workers = min(16, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda item: _compile(item[1], _part_define(item[0].vars)), prepared)
        )

    failures = []
    stale_expectations = []
    for (case, _), result in zip(prepared, results):
        ids = [case.case_id, *case.aliases]
        expected = [i for i in ids if i in _EXPECTED_RENDER_BUGS]
        if result.returncode != 0 and not expected:
            head = "\n".join(result.stderr.splitlines()[:30])
            failures.append(f"[{', '.join(ids)}]\n{head}")
        elif result.returncode == 0 and expected:
            stale_expectations.append(", ".join(expected))
    assert not failures, (
        f"{len(failures)}/{len(cases)} rendered TUs do not compile under "
        "g++ -fsyntax-only -Wall -Werror -Wformat.  If the template legitimately "
        "started using a new vendor symbol, declare it in "
        "tests/fixtures/compile_stubs/ in this same PR (#187 stub maintenance "
        "rule).\n\n" + "\n\n".join(failures)
    )
    assert not stale_expectations, (
        "these cases now compile clean — the template defect was fixed, so "
        "remove their _EXPECTED_RENDER_BUGS entries: " + "; ".join(stale_expectations)
    )


# ---------------------------------------------------------------------------
# Self-tests (#187 acceptance): the gate must go red on each bug class the
# reviews kept finding.  Each doctors ONE rendered TU at string level and
# asserts the identical harness invocation fails with the expected
# diagnostic.  apollo510|rtt|tflm is an arbitrary representative case.
# ---------------------------------------------------------------------------

_SELFTEST_SOC = "apollo510"
# The boot call sequence in every render — a stable, code-context anchor for
# injecting statements into main() (the definition line spells
# "hpx_sync_init(void)", so the indented call is unambiguous).
_INJECT_ANCHOR = "\n    hpx_sync_init();"


def _compile_doctored(tmp_path: Path, doctor) -> subprocess.CompletedProcess[str]:
    rendered = _render(_SELFTEST_SOC, "rtt", "tflm")
    doctored = doctor(rendered)
    assert doctored != rendered, "self-test injection did not change the TU"
    case = _CompileCase(
        case_id="selftest", text=doctored, vars=_common_kwargs(_SELFTEST_SOC, "rtt")
    )
    case_dir = _prepare_case_dir(case, tmp_path)
    return _compile(case_dir, _part_define(case.vars))


def test_gate_fails_on_an_undeclared_identifier(tmp_path):
    """#171 round 2's bug class: a render arm referencing a name nothing declares."""
    anchor = _INJECT_ANCHOR

    def doctor(rendered: str) -> str:
        assert anchor in rendered
        return rendered.replace(anchor, anchor + "\n    clean_warm_min_cyc += 1U;", 1)

    result = _compile_doctored(tmp_path, doctor)
    assert result.returncode != 0, "gate passed a TU with an undeclared identifier"
    assert "clean_warm_min_cyc" in result.stderr
    assert "not declared" in result.stderr or "was not declared" in result.stderr


def test_gate_fails_on_a_printf_format_mismatch(tmp_path):
    """The #110-era hazard: a conversion that does not match its argument."""
    original = 'hpx_printf("HPX_VERSION=1\\n");'
    doctored_call = 'hpx_printf("HPX_VERSION=%u\\n", "one");'

    def doctor(rendered: str) -> str:
        assert original in rendered
        return rendered.replace(original, doctored_call, 1)

    result = _compile_doctored(tmp_path, doctor)
    assert result.returncode != 0, "gate passed a printf format/argument mismatch"
    assert "-Wformat" in result.stderr or "format" in result.stderr


def test_gate_fails_on_an_unused_local_variable(tmp_path):
    """The (void)x bug class: dropping a printf arg orphans its variable."""
    anchor = _INJECT_ANCHOR

    def doctor(rendered: str) -> str:
        assert anchor in rendered
        return rendered.replace(anchor, anchor + "\n    int hpx_orphaned_by_injection = 0;", 1)

    result = _compile_doctored(tmp_path, doctor)
    assert result.returncode != 0, "gate passed a TU with an unused local variable"
    assert "hpx_orphaned_by_injection" in result.stderr
    assert "-Wunused-variable" in result.stderr or "unused" in result.stderr
