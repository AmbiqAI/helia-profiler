"""Contract: every rendered firmware TU compiles under host g++ (#187 Tier 1).

The snapshot suite (``test_firmware_render_snapshots.py``) pins renders by
sha256, which cannot see whether a render *compiles* — an undeclared
identifier in one render arm, a printf format/arg mismatch, or an orphaned
variable all render fine and stayed invisible until a bench build (#171
round 2 is the canonical case).  This gate closes that hole: it renders the
snapshot module's full scenario matrix (every SoC x transport x engine,
including the power_only and busy_loop variants) and syntax-checks each TU
with host ``g++ -fsyntax-only -std=gnu++17 -Wall -Werror -Wformat`` against
an hpx-owned stub include tree (``tests/fixtures/compile_stubs/``): one
minimal header per vendor include, declaring exactly the symbols the
templates use.

Stub maintenance rule (#187): a template that starts using a new vendor
symbol fails this gate until the stub declares it — loud by construction,
and the stub diff rides the template PR, same discipline as the wire census.

``hpx_printf`` has no format attribute in the templates (the vendor printf
path is variadic), so the harness force-includes a per-case prelude that
declares it with ``__attribute__((format(printf, 1, 2)))`` before the TU's
own definition — that is what arms ``-Wformat`` for the profiler's actual
output path.

The three ``test_gate_fails_on_*`` self-tests are the acceptance criteria
from #187: each doctors a rendered TU at string level (no template edits)
and asserts the SAME harness invocation goes red, proving the gate can see
each observed bug class.

CI wiring: no marker needed — the module skips itself when host ``g++`` is
absent, so it runs wherever the normal suite runs (Tier 2, the real
``arm-none-eabi-g++`` compile, is a separate bench-marked concern).

Runs in well under a minute: ~70 TUs at ~50-100 ms each, compiled in
parallel.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

if shutil.which("g++") is None:
    pytest.skip("host g++ not available", allow_module_level=True)

from helia_profiler.firmware import _jinja_env
from helia_profiler.platform import get_soc

_STUB_DIR = Path(__file__).parent.parent / "fixtures" / "compile_stubs"

# Reuse the snapshot module's render machinery and scenario matrix wholesale:
# it is the canonical "every arm renders" enumeration (#187 D2), and loading
# it via importlib keeps this module free of a tests-package import path.
_SNAPSHOT_MODULE_PATH = Path(__file__).parent / "test_firmware_render_snapshots.py"
_spec = importlib.util.spec_from_file_location("_hpx_render_snapshots", _SNAPSHOT_MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_snap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_snap)


def _all_cases() -> list[tuple[str, str, str, bool, str]]:
    """(soc, transport, engine, power_only, clean_window_probe) per scenario.

    Mirrors ``_build_all()`` in the snapshot module: the transport matrix,
    the power_only matrix, and both busy_loop matrices.
    """
    cases: list[tuple[str, str, str, bool, str]] = []
    for soc, transport, engine in _snap._all_combos():
        cases.append((soc, transport, engine, False, "infer"))
    for soc, transport, engine in _snap._power_combos():
        cases.append((soc, transport, engine, True, "infer"))
    for soc, transport, engine in _snap._power_busy_loop_combos():
        cases.append((soc, transport, engine, True, _snap._POWER_BUSY_LOOP_PROBE))
    for soc, transport, engine in _snap._profile_busy_loop_combos():
        cases.append((soc, transport, engine, False, _snap._POWER_BUSY_LOOP_PROBE))
    return cases


def _case_id(case: tuple[str, str, str, bool, str]) -> str:
    soc, transport, engine, power_only, probe = case
    return _snap._key(soc, transport, engine, power_only=power_only, clean_window_probe=probe)


def _render_pmu_profiler_header(soc_name: str) -> str:
    """Render hpx_pmu_profiler.h with the vars production hands it.

    Mirrors firmware/__init__.py (the hpx_pmu_profiler.h render site): the
    header's variables are sourced from the SoC exactly as
    FirmwareRenderContext sources them.
    """
    soc = get_soc(soc_name)
    backends = list(soc.profiling_backends)
    return _jinja_env.get_template("hpx_pmu_profiler.h.j2").render(
        cmsis_device_header=soc.cmsis_header,
        profiling_backends=backends,
        has_armv8m_pmu="armv8m-pmu" in backends,
        pmu_max_ops=soc.pmu_max_ops,
    )


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
# symbols main_aot.cc.j2 references.
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

static const int fake_num_inputs = 1;
static const int fake_num_outputs = 1;

static inline int32_t fake_model_init(fake_model_context_t *ctx) { (void)ctx; return 0; }
static inline int32_t fake_model_run(fake_model_context_t *ctx) { (void)ctx; return 0; }
"""

_AOT_COMMON_STUB = """\
// hpx compile-check generated AOT engine-header stand-in (#187)
#pragma once
"""


def _prepare_case_dir(case: tuple[str, str, str, bool, str], base: Path, rendered: str) -> Path:
    """Write the rendered TU plus its generated per-case headers into a dir."""
    soc, transport, engine, power_only, probe = case
    case_dir = base / _case_id(case).replace("|", "__")
    case_dir.mkdir(parents=True, exist_ok=True)
    (case_dir / "main.cc").write_text(rendered)
    if '#include "hpx_pmu_profiler.h"' in rendered:
        (case_dir / "hpx_pmu_profiler.h").write_text(_render_pmu_profiler_header(soc))
    if '#include "model_data.h"' in rendered:
        (case_dir / "model_data.h").write_text(_MODEL_DATA_STUB)
    if engine == "helia-aot":
        (case_dir / "fake_model.h").write_text(_AOT_MODEL_STUB)
        (case_dir / "fake_common.h").write_text(_AOT_COMMON_STUB)
    # Arm -Wformat for the profiler's own printf: a prior declaration with the
    # format attribute merges into the TU's later definition.  Linkage must
    # match the render's printf_linkage ("static " on the heliaAOT template);
    # `unused` keeps a static declaration warning-free on any render that
    # never defines it.
    linkage = "static " if "static void hpx_printf" in rendered else ""
    (case_dir / "hpx_prelude.h").write_text(
        "#pragma once\n"
        f"{linkage}void hpx_printf(const char *fmt, ...) "
        "__attribute__((format(printf, 1, 2), unused));\n"
    )
    return case_dir


def _compile(case_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "g++",
            "-fsyntax-only",
            "-std=gnu++17",
            "-Wall",
            "-Werror",
            "-Wformat",
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


def _render_case(case: tuple[str, str, str, bool, str]) -> str:
    soc, transport, engine, power_only, probe = case
    return _snap._render(
        soc, transport, engine, power_only=power_only, clean_window_probe=probe
    )


def test_every_rendered_firmware_tu_compiles(tmp_path):
    """The whole scenario matrix syntax-checks clean under -Wall -Werror.

    One aggregated test rather than a parametrize: every failing case is
    reported together (first 30 stderr lines each), so a stub-tree gap that
    breaks a whole SoC family reads as one diff-shaped report instead of
    dozens of identical first-fail truncations.
    """
    cases = _all_cases()
    assert len(cases) >= 60, "scenario matrix shrank — the gate lost most of its subject"
    dirs = [_prepare_case_dir(case, tmp_path, _render_case(case)) for case in cases]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_compile, dirs))

    failures = []
    for case, result in zip(cases, results):
        if result.returncode != 0:
            head = "\n".join(result.stderr.splitlines()[:30])
            failures.append(f"[{_case_id(case)}]\n{head}")
    assert not failures, (
        f"{len(failures)}/{len(cases)} rendered TUs do not compile under "
        "g++ -fsyntax-only -Wall -Werror -Wformat.  If the template legitimately "
        "started using a new vendor symbol, declare it in "
        "tests/fixtures/compile_stubs/ in this same PR (#187 stub maintenance "
        "rule).\n\n" + "\n\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Self-tests (#187 acceptance): the gate must go red on each bug class the
# reviews kept finding.  Each doctors ONE rendered TU at string level and
# asserts the identical harness invocation fails with the expected
# diagnostic.  apollo510|rtt|tflm is an arbitrary representative case.
# ---------------------------------------------------------------------------

_SELFTEST_CASE = ("apollo510", "rtt", "tflm", False, "infer")
# The boot call sequence in every render — a stable, code-context anchor for
# injecting statements into main() (the definition line spells
# "hpx_sync_init(void)", so the indented call is unambiguous).
_INJECT_ANCHOR = "\n    hpx_sync_init();"


def _compile_doctored(tmp_path: Path, doctor) -> subprocess.CompletedProcess[str]:
    rendered = _render_case(_SELFTEST_CASE)
    doctored = doctor(rendered)
    assert doctored != rendered, "self-test injection did not change the TU"
    case_dir = _prepare_case_dir(_SELFTEST_CASE, tmp_path, doctored)
    return _compile(case_dir)


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
