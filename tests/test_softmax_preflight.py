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

import math
from pathlib import Path

import pytest

from helia_profiler.engines import EngineType
from helia_profiler.errors import ConfigError
from helia_profiler.modelcost.softmax_preflight import (
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
        from helia_profiler.modelcost.softmax_preflight import SoftmaxScaling

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

    def test_the_aot_error_band_is_bounded_at_BOTH_ends(self):
        """helia-aot raises in [2**-32, 0.5) -- and only there.

        The first version errored on everything below 0.5. Review found the
        lower edge: `quantize_multiplier` FLUSHES to (0, 0) once the frexp
        exponent would fall below -31, so a smaller multiplier gets shift 0
        and compiles again. The gate blocked that sub-flush band -- the same
        over-blocking as gating heliaAOT at all, one dimension over, and
        found only because nobody had swept below 0.5.

        Measured against the pinned helia-aot 0.18 by running its real path.
        """
        from helia_profiler.modelcost.softmax_preflight import aot_softmax_verdict

        # Below the flush point: compiles, so it must not error.
        assert aot_softmax_verdict(2.0**-40) == "warn"
        assert aot_softmax_verdict(2.0**-33) == "warn"
        # The raise band, at both edges.
        assert aot_softmax_verdict(2.0**-32) == "error"
        assert aot_softmax_verdict(0.2889418303966522) == "error"  # issue model
        assert aot_softmax_verdict(0.49) == "error"
        # Exactly 0.5 has exponent 0 and compiles, so the bound is strict.
        assert aot_softmax_verdict(0.5) == "warn"
        assert aot_softmax_verdict(1.0) == "warn"
        assert aot_softmax_verdict(1.0000001) == "ok"
        assert aot_softmax_verdict(9710150.0) == "ok"  # KWS reference

    def test_a_nan_multiplier_does_not_fall_through_to_ok(self):
        """Every ordered comparison against NaN is False.

        A corrupt-but-parseable file can produce one (the fuzz corpus does),
        and helia-aot raises on it -- so falling through the band checks to
        'ok' would pass a model that crashes. TFLM's `supported` already
        blocks it for the same reason.
        """
        from helia_profiler.modelcost.softmax_preflight import aot_softmax_verdict

        assert aot_softmax_verdict(float("nan")) == "error"

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

    def test_has_usable_beta_rejects_zero(self):
        """Pure, so it pins the predicate in the BARE environment.

        The end-to-end no-beta test below needs the generator (litert), so it
        skips in CI's unit matrix -- where a mutation making has_usable_beta
        always True went unnoticed. A guardrail that only runs in the
        environment least likely to hit the bug is the shape this whole PR
        keeps rediscovering.
        """
        from helia_profiler.modelcost.softmax_preflight import SoftmaxScaling

        def scaling(beta: float) -> SoftmaxScaling:
            return SoftmaxScaling(
                subgraph_index=0,
                op_index=0,
                input_tensor="t",
                input_type="int8",
                beta=beta,
                input_scale=1.0,
                multiplier=beta * 1.0 * (1 << 26),
            )

        assert not scaling(0.0).has_usable_beta
        assert not scaling(-1.0).has_usable_beta
        assert scaling(1e-9).has_usable_beta
        assert scaling(1.0).has_usable_beta

    def test_the_gate_routes_a_no_beta_finding_to_its_own_error(
        self, tmp_path, monkeypatch
    ):
        """Also bare-env: the BRANCH, not just the predicate.

        Deleting the no_beta branch entirely survived the bare suite, because
        the only test reaching it was litert-gated. Substituting the scanner
        keeps this dependency-free while still driving the real gate.
        """
        from helia_profiler.modelcost.softmax_preflight import SoftmaxScaling

        no_beta = SoftmaxScaling(
            subgraph_index=0,
            op_index=3,
            input_tensor="orphaned_softmax",
            input_type="int8",
            beta=0.0,
            input_scale=1.49011612e-08,
            multiplier=0.0,
        )
        monkeypatch.setattr(
            "helia_profiler.stages.preflight.scan_softmax_scaling",
            lambda _path: [no_beta],
        )

        for engine in (EngineType.HELIA_RT, EngineType.TFLM, EngineType.HELIA_AOT):
            with pytest.raises(ConfigError, match="no usable SoftmaxOptions") as exc:
                _check_softmax_scaling(KWS_MODEL, engine)
            message = str(exc.value)
            assert "orphaned_softmax" in message
            assert "inf" not in message
            assert "calculate_input_radius" not in message

    def test_an_op_with_no_usable_beta_gets_its_own_message(self, tmp_path):
        """beta <= 0 is not a scale problem and must not be reported as one.

        TFLM value-initialises beta to 0.0 when SoftmaxOptions is absent;
        helia-aot's field default is 1.0. The engines disagree about what the
        model even says, and neither runs it. Reporting it through the scale
        path printed "needs input_scale > inf" (no scale can rescue a zero
        beta) and, for helia-aot, named a crash in a function that model
        never reaches -- both found by review.
        """
        import importlib.util

        pytest.importorskip("ai_edge_litert.schema_py_generated")
        tool = (
            Path(__file__).parent.parent / "tools" / "gen_softmax_preflight_fixture.py"
        )
        spec = importlib.util.spec_from_file_location("gen_fixture_nobeta", tool)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model = tmp_path / "no_beta.tflite"
        model.write_bytes(module.generate(betas=(0.0, 0.0, 0.0, 0.0)))

        for engine in (EngineType.HELIA_RT, EngineType.TFLM, EngineType.HELIA_AOT):
            with pytest.raises(ConfigError, match="no usable SoftmaxOptions") as exc:
                _check_softmax_scaling(model, engine)
            message = str(exc.value)
            assert "inf" not in message, "an infinite bound is not advice"
            assert "calculate_input_radius" not in message, (
                "beta=0 never reaches the AOT shift path"
            )

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
    assert spec is not None and spec.loader is not None
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

    from helia_profiler.modelcost._tflite_reader import read_quantized_softmax_ops

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


# ---------------------------------------------------------------------------
# #147: tripwire the hand-mirrored helia-aot boundary constants
# ---------------------------------------------------------------------------

#: Every observable edge of the pinned helia-aot 0.18 raise band, measured by
#: running its real code (the six points from the module comment plus the
#: one-rounding-step neighbours of each boundary). ``quantize_multiplier``
#: rounds the frexp fraction to Q31 half-up and promotes the exponent when it
#: rounds to 1.0 -- BEFORE the flush check -- so both raw edges sit one
#: rounding step below the nominal constants: [0.5 - 2**-33, 0.5) rounds up
#: and compiles, [2**-32 * (1 - 2**-32), 2**-32) rounds up and raises.
_AOT_SWEEP = [
    # (label, multiplier, what the pinned helia-aot 0.18 measurably does)
    # Negatives are swept too (#172 round-2): the Q31 promotion fires only
    # at +2**31, so the negative boundaries do NOT mirror the positive ones
    # — a sign-blind guard disagreed with the compiler on 218/689 points.
    ("negative in the raise band", -0.25, "raises"),
    ("negative half-up edge keeps its shift", -0.49999999999999994, "raises"),
    ("negative 0.5 has exponent 0", -0.5, "compiles"),
    ("negative above the band", -0.75, "compiles"),
    ("negative at 2**-32", -(2.0**-32), "raises"),
    ("negative flush rounds toward zero", -(2.0**-32) * (1.0 - 2.0**-32), "compiles"),
    ("negative large", -2.0, "compiles"),
    ("deep sub-flush", 2.0**-40, "compiles"),
    ("measured 2**-33", 2.0**-33, "compiles"),
    (
        "one ulp below the flush round-up band",
        math.nextafter(2.0**-32 * (1.0 - 2.0**-32), 0.0),
        "compiles",
    ),
    ("flush round-up band lower edge", 2.0**-32 * (1.0 - 2.0**-32), "raises"),
    ("one ulp below 2**-32", math.nextafter(2.0**-32, 0.0), "raises"),
    ("measured 2**-32", 2.0**-32, "raises"),
    ("one ulp above 2**-32", math.nextafter(2.0**-32, 1.0), "raises"),
    ("measured 2**-31", 2.0**-31, "raises"),
    ("issue #57 model", 0.2889418303966522, "raises"),
    ("measured 0.49", 0.49, "raises"),
    (
        "one ulp below the 0.5 round-up band",
        math.nextafter(0.5 - 2.0**-33, 0.0),
        "raises",
    ),
    ("0.5 round-up band lower edge", 0.5 - 2.0**-33, "compiles"),
    ("one ulp below 0.5", math.nextafter(0.5, 0.0), "compiles"),
    ("measured 0.5", 0.5, "compiles"),
    ("one ulp above 0.5", math.nextafter(0.5, 1.0), "compiles"),
    ("KWS reference", 9710150.0, "compiles"),
]


class TestAotCompilerBoundaryTripwire:
    """#147: drive the REAL helia-aot softmax path across every band edge.

    ``aot_softmax_verdict`` predicts the AOT compiler from three constants
    mirrored by hand out of helia-aot 0.18's internals; until this sweep,
    only comments guarded them, so a helia-aot version bump could move a
    boundary and the gate would keep enforcing the old one -- blocking
    models that now compile, or waving through models that now crash. This
    runs helia-aot's own ``preprocess_softmax_scaling`` ->
    ``calculate_input_radius`` (the exact int8 chain in its Softmax
    operator's ``compute_values``) at each swept multiplier and fails if the
    measured outcome no longer matches either the recorded ground truth or
    the gate's verdict. Runs in CI's analysis-tests job, which installs the
    aot extra and fails loudly if helia-aot is absent; skips cleanly in the
    bare unit matrix, same as the litert-gated tests above.
    """

    @staticmethod
    def _real_compiler_outcome(multiplier: float) -> str:
        """'raises' or 'compiles', from helia-aot's actual code."""
        from helia_aot.aot.operators.softmax import preprocess_softmax_scaling
        from helia_aot.aot.operators.utils import calculate_input_radius

        # preprocess_softmax_scaling computes beta * scale * 2**26; feed it
        # a scale that reproduces the target multiplier exactly (dividing by
        # a power of two is exact in binary floating point).
        scale = multiplier / (1 << 26)
        assert scale * (1 << 26) == multiplier, "sweep point not exactly representable"
        fp = preprocess_softmax_scaling(
            beta=1.0, input_scale=scale, scaled_diff_integer_bits=5
        )
        try:
            calculate_input_radius(
                input_integer_bits=5, input_left_shift=fp.shift, total_signed_bits=31
            )
        except ValueError:
            return "raises"
        return "compiles"

    @pytest.mark.parametrize(
        ("label", "multiplier", "measured"),
        _AOT_SWEEP,
        ids=[label.replace(" ", "-") for label, _, _ in _AOT_SWEEP],
    )
    def test_verdict_agrees_with_the_real_compiler(
        self, label: str, multiplier: float, measured: str
    ):
        pytest.importorskip("helia_aot")
        from helia_profiler.modelcost.softmax_preflight import aot_softmax_verdict

        real = self._real_compiler_outcome(multiplier)
        assert real == measured, (
            f"{label} (multiplier {multiplier!r}): the installed helia-aot "
            f"{real} where the pinned 0.18 measurably {measured} -- a version "
            "bump moved this boundary; re-measure the band and update "
            "softmax_preflight's constants"
        )

        verdict = aot_softmax_verdict(multiplier)
        assert (verdict == "error") == (real == "raises"), (
            f"{label} (multiplier {multiplier!r}): aot_softmax_verdict says "
            f"{verdict!r} but the real compiler {real} -- the gate and the "
            "compiler disagree"
        )


def test_aot_absent_beta_matches_the_installed_helia_aot():
    """#147: the beta-when-options-absent constant, against the live default.

    helia-aot's ``AirSoftmaxOptions`` is a pydantic dataclass whose ``beta``
    field defaults to 1.0 -- opposite of TFLM's value-initialised 0.0. The
    module now reads it live when the extra is installed; this pins that the
    read works and that a bump changing the default fails here rather than
    silently re-diverging the two engines' absent-options semantics.
    """
    pytest.importorskip("helia_aot")
    from helia_aot.air.options import AirSoftmaxOptions

    from helia_profiler.modelcost.softmax_preflight import AOT_ABSENT_BETA

    assert AOT_ABSENT_BETA == AirSoftmaxOptions().beta


def test_aot_absent_beta_is_one_in_every_environment():
    """Dependency-free pin of the value itself.

    With the extra installed this checks the live read; without it, the
    fallback. Both must be 1.0 -- the fallback exists so a bare install keeps
    the documented 0.18 semantics, not so it can drift from them.
    """
    from helia_profiler.modelcost.softmax_preflight import AOT_ABSENT_BETA

    assert AOT_ABSENT_BETA == 1.0


def test_negative_multipliers_mirror_the_real_chain_not_a_blanket_error():
    """#172 round-2: the first fix blanket-errored negatives; the real chain
    compiles most of that domain. The asymmetry is the Q31 promotion (fires
    only at +2**31), so the verdict mirrors the SHIFT — a sign-blind band
    disagreed on 218 of 689 negative sweep points."""
    from helia_profiler.modelcost.softmax_preflight import aot_softmax_verdict

    # In the negative raise band (shift in [-31, -1]):
    assert aot_softmax_verdict(-0.25) == "error"
    assert aot_softmax_verdict(-1e-09) == "error"
    assert aot_softmax_verdict(-0.49999999999999994) == "error"  # rounds to -2**31, NO promotion
    assert aot_softmax_verdict(-(2.0**-32)) == "error"
    # Outside it (the real compiler compiles these):
    assert aot_softmax_verdict(-0.5) == "warn"  # exponent 0
    assert aot_softmax_verdict(-0.75) == "warn"
    assert aot_softmax_verdict(-2.0) == "warn"
    assert aot_softmax_verdict(-(2.0**-32) * (1 - 2.0**-32)) == "warn"  # negative flush
    # -inf overflows the Q31 floor inside preprocess_softmax_scaling:
    assert aot_softmax_verdict(float("-inf")) == "error"


def test_top_binade_multiplier_does_not_crash_the_verdict():
    """#172 review: the Q31 rounding promotes the top float64 binade past
    ldexp's range — the mirror must degrade to the raw value (far above the
    raise band) instead of raising OverflowError from a preflight."""
    import sys

    from helia_profiler.modelcost.softmax_preflight import aot_softmax_verdict

    assert aot_softmax_verdict(sys.float_info.max) == "ok"


def test_reader_constants_re_derive_from_the_installed_litert():
    """#229 D7: the reader's frozen constants carry provenance — re-derive
    every one from the installed generated schema by introspection, so a
    litert upgrade that moved anything fails loudly here rather than
    silently misparsing models."""
    import inspect
    import re

    g = pytest.importorskip("ai_edge_litert.schema_py_generated")

    from helia_profiler.modelcost import _tflite_reader as r

    def slot(accessor) -> int:
        match = re.search(r"Offset\((\d+)\)", inspect.getsource(accessor))
        assert match, f"no Offset() in {accessor.__qualname__}"
        return int(match.group(1))

    assert r.BUILTIN_SOFTMAX == g.BuiltinOperator.SOFTMAX
    assert r.TENSOR_TYPE_UINT8 == g.TensorType.UINT8
    assert r.TENSOR_TYPE_INT8 == g.TensorType.INT8
    assert r.BUILTIN_OPTIONS_SOFTMAX == g.BuiltinOptions.SoftmaxOptions

    # Tuples, NOT a dict: slot values collide across tables (four distinct
    # fields sit at slot 10 alone), and a value-keyed dict silently dropped
    # 9 of these 14 checks (#235 lens).
    expected_slots = [
        (r._MODEL_OPERATOR_CODES, g.Model.OperatorCodes),
        (r._MODEL_SUBGRAPHS, g.Model.Subgraphs),
        (r._OPCODE_DEPRECATED_BUILTIN, g.OperatorCode.DeprecatedBuiltinCode),
        (r._OPCODE_BUILTIN, g.OperatorCode.BuiltinCode),
        (r._SUBGRAPH_TENSORS, g.SubGraph.Tensors),
        (r._SUBGRAPH_OPERATORS, g.SubGraph.Operators),
        (r._OPERATOR_OPCODE_INDEX, g.Operator.OpcodeIndex),
        (r._OPERATOR_INPUTS, g.Operator.Inputs),
        (r._OPERATOR_OPTIONS_TYPE, g.Operator.BuiltinOptionsType),
        (r._OPERATOR_OPTIONS, g.Operator.BuiltinOptions),
        (r._TENSOR_TYPE, g.Tensor.Type),
        (r._TENSOR_NAME, g.Tensor.Name),
        (r._TENSOR_QUANTIZATION, g.Tensor.Quantization),
        (r._QUANT_SCALE, g.QuantizationParameters.Scale),
    ]
    assert len(expected_slots) == 14  # one row per frozen slot constant
    for constant, accessor in expected_slots:
        assert constant == slot(accessor), accessor.__qualname__
