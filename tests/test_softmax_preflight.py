"""#57: host-side preflight for TFLM's quantized-Softmax scaling requirement.

The fixture `softmax_scale_unsupported.tflite` (936 bytes, committed) carries
the issue's exact failing value: one int8 Softmax with input scale
4.305568790385905e-09. TFLM computes `beta * input_scale * 2**26` and aborts
on target unless it exceeds 1.0; that scale yields 0.2889418303966522 -- the
multiplier the issue quotes.

The parser under test is the package's own minimal flatbuffer reader, not
ai-edge-litert: litert is an optional extra, absent from a plain helia-rt
install and from the unit-test CI matrix, and a preflight that skips exactly
where the bug bites is not a preflight. Everything here except the
litert-marked tests runs dependency-free; those (the cross-parser sweep and
the fixture-generator byte-identity pin) run in CI's analysis-tests job,
which installs the analysis extra for exactly this purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.evaluation.softmax_preflight import (
    scan_softmax_scaling,
    softmax_input_multiplier,
)
from helia_profiler.stages.preflight import _check_softmax_scaling

FIXTURES = Path(__file__).parent / "fixtures"
BAD_MODEL = FIXTURES / "softmax_scale_unsupported.tflite"
# The root-level copy, NOT tests/fixtures/mlperf_tiny/kws/: everything under
# mlperf_tiny/** is Git-LFS-tracked and the unit-test CI checks out without
# LFS, so those paths hold 130-byte pointer files there -- the reader then
# "parses" ASCII pointer text as offsets. Found by CI on the first push.
KWS_MODEL = FIXTURES / "kws_ref_model.tflite"

#: Ground truth from issue #57, verbatim.
FAILING_SCALE = 4.305568790385905e-09
FAILING_MULTIPLIER = 0.2889418303966522
PASSING_SCALE = 0.14469251036643982


class TestMultiplier:
    def test_reproduces_the_issues_failing_multiplier_exactly(self):
        """`beta * input_scale * 2**26` IS the number the issue quotes."""
        assert softmax_input_multiplier(1.0, FAILING_SCALE) == FAILING_MULTIPLIER

    def test_the_reference_models_scale_passes(self):
        assert softmax_input_multiplier(1.0, PASSING_SCALE) > 1.0

    def test_the_exact_boundary_is_rejected(self):
        """multiplier == 1.0 must fail: TFLM's check is CHECK_GT, strict.

        Reachable in a real file -- 2^-26 is float32-exact, so a quantizer
        emitting a power-of-two scale lands the product exactly on 1.0.
        Review found the `>` unpinned: flipping it to `>=` survived the whole
        suite (the PR's own mutation battery had reported it CAUGHT, off
        stale bytecode a second time).
        """
        from helia_profiler.evaluation.softmax_preflight import SoftmaxScaling

        boundary_scale = 2.0**-26
        assert softmax_input_multiplier(1.0, boundary_scale) == 1.0
        at_boundary = SoftmaxScaling(
            subgraph_index=0,
            op_index=0,
            input_tensor="t",
            input_type="int8",
            beta=1.0,
            input_scale=boundary_scale,
            multiplier=1.0,
        )
        assert not at_boundary.supported, "TFLITE_CHECK_GT(1.0, 1.) aborts"

    def test_beta_participates(self):
        """A large beta can rescue a small scale, and a small beta can sink a
        healthy one -- TFLM multiplies them before checking."""
        assert softmax_input_multiplier(1e9, FAILING_SCALE) > 1.0
        assert softmax_input_multiplier(1e-9, PASSING_SCALE) < 1.0


class TestScan:
    """The fixture carries three ops so every scanner branch is load-bearing
    without litert: the failing op; one whose SUPPORT depends on beta being
    read (tiny scale rescued by beta=1e9 -- a scanner that hardcoded beta=1
    flags it and fails here); and a uint8 op. Its OperatorCode stores the
    builtin only in the deprecated field, exercising the pre-v3a fallback."""

    def test_finds_the_unsupported_softmax_with_the_issues_numbers(self):
        findings = scan_softmax_scaling(BAD_MODEL)

        assert len(findings) == 3
        f = findings[0]
        assert not f.supported
        assert f.input_scale == FAILING_SCALE
        assert f.multiplier == FAILING_MULTIPLIER
        assert f.beta == 1.0
        assert f.input_type == "int8"
        assert f.input_tensor == "softmax_input"

    def test_only_the_degenerate_op_is_flagged(self):
        findings = scan_softmax_scaling(BAD_MODEL)

        assert [f.supported for f in findings] == [False, True, True]

    def test_beta_is_read_from_the_op_not_assumed(self):
        """Same tiny scale as the failing op; only its beta saves it."""
        rescued = scan_softmax_scaling(BAD_MODEL)[1]

        assert rescued.input_scale == FAILING_SCALE
        assert rescued.beta == pytest.approx(1e9)
        assert rescued.supported

    def test_uint8_softmax_is_scanned_too(self):
        """TFLM's helper covers both quantized types."""
        u8 = scan_softmax_scaling(BAD_MODEL)[2]

        assert u8.input_type == "uint8"
        assert u8.supported

    def test_the_reference_model_scans_clean_but_not_empty(self):
        """Non-empty matters: an all-clear from a scanner that saw nothing is
        the vacuous-check shape, so the fixture must prove the walker reached
        a real Softmax."""
        findings = scan_softmax_scaling(KWS_MODEL)

        assert len(findings) == 1
        f = findings[0]
        assert f.supported
        assert f.input_scale == pytest.approx(PASSING_SCALE, rel=1e-9)
        assert f.input_tensor == "functional_1/dense/BiasAdd"

    def test_minimum_scale_names_the_boundary(self):
        finding = scan_softmax_scaling(BAD_MODEL)[0]

        assert finding.minimum_scale == pytest.approx(1 / (1 << 26))
        assert finding.input_scale < finding.minimum_scale

    def test_minimum_scale_scales_with_beta(self):
        """The printed 'needs input_scale > X' must use the op's OWN beta.

        The beta-rescued op (beta=1e9) needs a scale 1e9 smaller than a
        beta=1 op needs. Review found the beta term unpinned: dropping it
        from minimum_scale survived the whole suite while the user-facing
        error printed a bound a billion times too large.
        """
        rescued = scan_softmax_scaling(BAD_MODEL)[1]

        assert rescued.beta == pytest.approx(1e9)
        assert rescued.minimum_scale == pytest.approx(1 / (1e9 * (1 << 26)))


class TestPreflightGate:
    def test_the_failing_model_is_rejected_before_anything_runs(self):
        with pytest.raises(ConfigError, match="Softmax") as excinfo:
            _check_softmax_scaling(BAD_MODEL, EngineType.HELIA_RT)

        message = str(excinfo.value)
        # The error must carry what a user needs to act: which op, both
        # numbers, and what would have happened on target.
        assert "softmax_input" in message
        assert "4.30556879e-09" in message
        assert "AllocateTensors" in message
        # And it must name only the degenerate op -- the fixture's other two
        # Softmax ops are healthy, and flagging them would send the user
        # chasing layers that are fine.
        assert "1 quantized Softmax" in message
        assert "beta_rescued_input" not in message

    def test_each_engine_gets_its_own_verdict_and_message(self):
        """Three engine behaviours, each established by running real code.

        This test has been wrong twice. v1 gated heliaAOT with TFLM's message
        after misreading the issue. v2 exempted heliaAOT entirely after
        verifying its preprocess_softmax_scaling handles the failing scale --
        one call short: calculate_input_radius does `1 << shift` on the
        result and raises `negative shift count` for multipliers below 0.5,
        so the issue's model (0.289) crashes the AOT compiler at stage 2 with
        a message naming nothing. The gate now catches it with the cause.
        """
        for engine in (EngineType.HELIA_RT, EngineType.TFLM):
            with pytest.raises(ConfigError, match="AllocateTensors"):
                _check_softmax_scaling(BAD_MODEL, engine)

        with pytest.raises(ConfigError, match="calculate_input_radius") as excinfo:
            _check_softmax_scaling(BAD_MODEL, EngineType.HELIA_AOT)
        assert "AllocateTensors" not in str(excinfo.value), (
            "the AOT message must not describe a call AOT firmware never makes"
        )

        _check_softmax_scaling(BAD_MODEL, EngineType.EXECUTORCH)

    def test_the_aot_verdict_bands_match_the_pinned_compiler(self):
        """The three bands, swept at their boundaries.

        Verified against helia-aot 0.18 by running its softmax path: below
        0.5 raises `negative shift count`; [0.5, 1.0] compiles with diff_min
        seven orders of magnitude from healthy (warn); above 1.0 is fine.
        Exactly 0.5 has frexp exponent 0 and compiles, so the error bound is
        strict.
        """
        from helia_profiler.evaluation.softmax_preflight import aot_softmax_verdict

        assert aot_softmax_verdict(0.2889418303966522) == "error"  # issue model
        assert aot_softmax_verdict(0.49) == "error"
        assert aot_softmax_verdict(0.5) == "warn"
        assert aot_softmax_verdict(0.99) == "warn"
        assert aot_softmax_verdict(1.0) == "warn"
        assert aot_softmax_verdict(1.0000001) == "ok"
        assert aot_softmax_verdict(9710150.0) == "ok"  # KWS reference

    def test_the_aot_error_path_warns_on_the_degenerate_band_too(self, caplog):
        """A model can carry ops in BOTH bands; the sub-0.5 op raises and the
        0.5..1.0 op must still be logged, not eaten by the error."""
        import logging

        with caplog.at_level(logging.WARNING):
            with pytest.raises(ConfigError):
                _check_softmax_scaling(BAD_MODEL, EngineType.HELIA_AOT)
        # BAD_MODEL has no 0.5..1.0 op, so nothing may be logged here either:
        # the warning must track the band exactly, in both directions.
        assert "degenerate input scale" not in caplog.text

    def test_a_clean_model_passes(self):
        _check_softmax_scaling(KWS_MODEL, EngineType.HELIA_RT)

    def test_the_stage_itself_runs_the_gate(self, tmp_path):
        """Pin the CALL, not just the function.

        Every gate test above drives `_check_softmax_scaling` directly, so
        deleting the one line in `PreflightStage.run` that invokes it left all
        of them green while the pipeline stopped checking anything -- the
        untested-write-site gap #137's review found, in its preflight shape.
        (First caught here by a mutation run whose "3 failed" turned out to be
        stale bytecode; in a clean run it was 15 passed.)
        """
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.preflight import PreflightStage

        config = load_config(
            None,
            {
                "model": {"path": str(BAD_MODEL)},
                "engine": {"type": "helia-rt"},
                "output": {"dir": str(tmp_path)},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)

        with pytest.raises(ConfigError, match="Softmax"):
            PreflightStage().run(ctx)

    @pytest.mark.parametrize(
        "engine", [EngineType.HELIA_RT, EngineType.TFLM, EngineType.HELIA_AOT]
    )
    def test_a_damaged_flatbuffer_is_an_error_not_a_stack_trace(
        self, tmp_path, engine
    ):
        """Stage 0 must catch a corrupt file for EVERY TFLite engine.

        The round-1 whitelist returned before the parse for helia-aot, so a
        malformed model sailed through preflight and surfaced at stage 5 --
        after the board was powered and the probe resolved, or on a laptop
        with no board as a misleading "J-Link probe not found" (found by
        review). The parse now runs for all TFLite engines; only the
        VERDICTS are per-engine.
        """
        mangled = tmp_path / "mangled.tflite"
        mangled.write_bytes(b"TFL3" + b"\xff" * 64)

        with pytest.raises(ConfigError, match="could not be parsed"):
            _check_softmax_scaling(mangled, engine)


def test_fixture_matches_its_committed_generator():
    """The committed fixture must be reproducible from tools/, byte for byte.

    Same convention as tools/gen_example_model.py: the generator is the
    fixture's documentation, and this pins the two together so a drifted
    regeneration (or a hand-edited fixture) fails CI's analysis-tests job
    instead of silently changing what these tests test. The generator needs
    litert's builder, so this skips in the bare unit environment.
    """
    pytest.importorskip("ai_edge_litert.schema_py_generated")
    import importlib.util

    tool = Path(__file__).parent.parent / "tools" / "gen_softmax_preflight_fixture.py"
    spec = importlib.util.spec_from_file_location("gen_softmax_preflight_fixture", tool)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.generate() == BAD_MODEL.read_bytes()


def test_reader_agrees_with_litert_on_every_fixture():
    """Cross-validation: the minimal reader vs the reference parser.

    Runs wherever ai-edge-litert is installed -- in CI, the analysis-tests
    job, which exists to run this; skips in the bare unit matrix. A drift
    between the two parsers fails here rather than silently diverging.
    """
    schema = pytest.importorskip("ai_edge_litert.schema_py_generated")

    from helia_profiler.evaluation._tflite_reader import read_quantized_softmax_ops

    checked = 0
    for path in sorted(FIXTURES.rglob("*.tflite")):
        buf = path.read_bytes()
        if b"TFL3" not in buf[:16]:
            continue  # a Git LFS pointer in a checkout without LFS content
        mine = [
            (op.subgraph_index, op.op_index, op.input_tensor, op.input_scale, op.beta)
            for op in read_quantized_softmax_ops(buf)
        ]

        model = schema.Model.GetRootAs(buf, 0)
        reference = []
        for sg_index in range(model.SubgraphsLength()):
            sg = model.Subgraphs(sg_index)
            for op_index in range(sg.OperatorsLength()):
                op = sg.Operators(op_index)
                opcode = model.OperatorCodes(op.OpcodeIndex())
                builtin = opcode.BuiltinCode() or opcode.DeprecatedBuiltinCode()
                if builtin != schema.BuiltinOperator.SOFTMAX:
                    continue
                if op.InputsLength() == 0:
                    # litert's generated accessor returns a hardcoded 0 for
                    # Inputs(j) when the field is absent, which would read
                    # tensor 0 -- the reference must skip, as the reader does.
                    continue
                if op.Inputs(0) < 0:
                    continue  # -1 marks an absent optional input (TFLite convention)
                tensor = sg.Tensors(op.Inputs(0))
                if tensor.Type() not in (
                    schema.TensorType.INT8,
                    schema.TensorType.UINT8,
                ):
                    continue
                quant = tensor.Quantization()
                scale = (
                    float(quant.Scale(0)) if quant and quant.ScaleLength() else None
                )
                beta = 0.0
                if op.BuiltinOptionsType() == schema.BuiltinOptions.SoftmaxOptions:
                    options = schema.SoftmaxOptions()
                    options.Init(op.BuiltinOptions().Bytes, op.BuiltinOptions().Pos)
                    beta = float(options.Beta())
                raw_name = tensor.Name()
                reference.append(
                    (
                        sg_index,
                        op_index,
                        (
                            raw_name.decode("utf-8", "replace")
                            if raw_name is not None
                            else f"tensor_{op.Inputs(0)}"
                        ),
                        scale,
                        beta,
                    )
                )

        assert mine == reference, f"parsers disagree on {path.name}"
        checked += 1

    assert checked >= 2, (
        "the sweep must at least cover the committed repro fixture and the "
        "root KWS model; mlperf_tiny/** joins it only in LFS checkouts"
    )
