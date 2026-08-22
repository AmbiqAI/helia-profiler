"""Tests for firmware/fingerprint.py — the measured-binary code hash (#138)."""

from __future__ import annotations

import sys
from pathlib import Path

from helia_profiler.firmware.fingerprint import (
    canonical_code,
    firmware_code_fingerprint,
    measured_power_fingerprint,
)

sys.path.insert(0, str(Path(__file__).parent))
from test_template_render import _render_tflm  # noqa: E402


class TestCanonicalCode:
    def test_line_and_block_comments_become_one_space(self):
        src = "int a; // trailing\n/* block\nspanning */int b;\n"
        assert canonical_code(src) == "int a; int b;"

    def test_comment_markers_inside_strings_survive(self):
        src = 'printf("http://x /* not a comment */  spaced\\n");'
        assert canonical_code(src) == src

    def test_escaped_quote_inside_string(self):
        src = 'const char *s = "a\\"b // still string";\n// real comment\n'
        assert canonical_code(src) == 'const char *s = "a\\"b // still string";'

    def test_char_literal_with_comment_chars(self):
        src = "char c = '/';  char d = '*'; // gone\n"
        assert canonical_code(src) == "char c = '/'; char d = '*';"

    def test_unterminated_inputs_do_not_raise(self):
        assert canonical_code("int a; /* never closed") == "int a;"
        assert canonical_code('const char *s = "open') == 'const char *s = "open'

    def test_comments_never_glue_tokens(self):
        # A comment must canonicalize to a space, not to nothing: gluing
        # would make distinct token streams hash equal.
        assert canonical_code("int a/* c */int b") == "int a int b"
        assert canonical_code("int a// c\nint b") == "int a int b"

    def test_directive_line_structure_survives(self):
        """#173 review m1: newline is significant to the preprocessor — a
        directive line joined with the next code line is semantically
        different and must not hash equal."""
        split = canonical_code("#define A 1\nint x;")
        joined = canonical_code("#define A 1 int x;")
        assert split != joined
        assert split == "#define A 1\nint x;"
        # #if/#endif structure likewise:
        assert canonical_code("#if X\nint a;\n#endif\nint b;") == (
            "#if X\nint a;\n#endif\nint b;"
        )
        # ...but a block comment INSIDE a directive is phase-3 whitespace,
        # not a directive break:
        assert canonical_code("#define A 1 /* c\n c */ + 2\nint x;") == (
            "#define A 1 + 2\nint x;"
        )

    def test_stray_apostrophe_cannot_swallow_the_file(self):
        """#173 review m2: a digit separator (1'000) is not a char literal —
        literal scanning stops at the newline, so stripping keeps working."""
        src = "int a = 1'000;\n// gone\nint b = 2; // gone too\nchar c = 'x';"
        out = canonical_code(src)
        assert "gone" not in out
        assert "'x'" in out

    def test_pure_comment_and_whitespace_lines_vanish(self):
        # The property that makes comment-only template churn invisible.
        assert canonical_code("a;\n\n// note\n\nb;") == canonical_code("a;\nb;")


class TestFingerprintStability:
    """The D1 property, proven against a REAL power render: comment-only
    template churn leaves stored baselines untouched; any code change
    shifts the fingerprint."""

    def _render(self) -> str:
        return _render_tflm(
            transport="rtt",
            power_only=True,
            window_mode="fixed",
            clean_iters=100,
            power_window_timer="stimer",
            clean_window_timer="stimer",
        )

    def test_comment_only_edit_is_invisible(self):
        base = self._render()
        commented = base.replace(
            "int main(void) {",
            "// a comment-only template change (the #169 render class)\nint main(void) {",
            1,
        )
        assert base != commented
        assert firmware_code_fingerprint(base) == firmware_code_fingerprint(commented)

    def test_one_line_code_edit_shifts(self):
        base = self._render()
        edited = base.replace("int main(void) {", "int main(void) { int extra = 0;", 1)
        assert firmware_code_fingerprint(base) != firmware_code_fingerprint(edited)

    def test_fingerprint_is_deterministic(self):
        assert firmware_code_fingerprint(self._render()) == firmware_code_fingerprint(
            self._render()
        )


class TestMeasuredPowerFingerprint:
    def _ctx(self, tmp_path, firmware_mode: str):
        from helia_profiler.config import load_config

        tmp_path.mkdir(parents=True, exist_ok=True)
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import PowerRunPlan

        model = tmp_path / "m.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.publish_power_plan(
            PowerRunPlan(
                firmware_mode=firmware_mode,
                inference_count=1,
                count_source="configured",
            )
        )
        src = tmp_path / "fw" / "src"
        src.mkdir(parents=True, exist_ok=True)
        (src / "main.cc").write_text("int main(void) { return 1; }\n")
        (src / "main_power.cc").write_text("int main(void) { return 2; }\n")
        (src / "hpx_pmu_profiler.cc").write_text("void hpx_prof(void) {}\n")
        (src / "hpx_pmu_profiler.h").write_text("void hpx_prof(void);\n")
        ctx.firmware_dir = tmp_path / "fw"
        return ctx

    def test_dedicated_and_shared_hash_their_own_main(self, tmp_path):
        dedicated = measured_power_fingerprint(self._ctx(tmp_path, "dedicated"))
        shared = measured_power_fingerprint(self._ctx(tmp_path, "shared"))
        assert dedicated is not None and shared is not None
        assert dedicated != shared  # routed to different main TUs

    def test_profiler_translation_unit_is_part_of_the_hash(self, tmp_path):
        """#173 review M1: hpx_pmu_profiler.cc is compiled into the measured
        target and its per-op hooks run inside the gated window — an edit
        there must shift the fingerprint like any main-TU edit."""
        ctx = self._ctx(tmp_path, "dedicated")
        before = measured_power_fingerprint(ctx)
        prof = ctx.firmware_dir / "src" / "hpx_pmu_profiler.cc"
        prof.write_text(prof.read_text() + "int hpx_extra;\n")
        assert measured_power_fingerprint(ctx) != before

    def test_missing_profiler_file_degrades_deterministically(self, tmp_path):
        ctx = self._ctx(tmp_path, "dedicated")
        (ctx.firmware_dir / "src" / "hpx_pmu_profiler.h").unlink()
        one = measured_power_fingerprint(ctx)
        two = measured_power_fingerprint(ctx)
        assert one is not None and one == two
        full = measured_power_fingerprint(self._ctx(tmp_path / "again", "dedicated"))
        assert one != full  # absent differs from any present content

    def test_no_plan_or_missing_file_returns_none(self, tmp_path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext

        model = tmp_path / "m.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None, {"model": {"path": str(model)}, "engine": {"type": "helia-rt"}}
        )
        bare = PipelineContext(config=config, work_dir=tmp_path)
        assert measured_power_fingerprint(bare) is None

        ctx = self._ctx(tmp_path, "dedicated")
        (ctx.firmware_dir / "src" / "main_power.cc").unlink()
        assert measured_power_fingerprint(ctx) is None
