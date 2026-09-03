"""Tests for the pipeline primitives and stage sequencing."""

from __future__ import annotations

from tests.pipeline_context_helpers import (
    set_power_deployment,
    set_power_firmware,
    set_power_result,
    set_profile_firmware,
    set_profile_result,
)

import dataclasses
import re
from pathlib import Path
from dataclasses import FrozenInstanceError

import pytest

from helia_profiler.results import (
    DeploymentRecord,
    FirmwareArtifact,
    PowerRunPlan,
)
from helia_profiler.config import load_config
from helia_profiler.errors import CaptureError, HpxError, PipelineError
from helia_profiler.pipeline import (
    PipelineContext,
    PipelineRunner,
    ProgressUpdate,
    Stage,
)

# ---------------------------------------------------------------------------
# Helpers: minimal stage implementations for testing
# ---------------------------------------------------------------------------


class PassStage:
    """A stage that always runs and does nothing."""

    def __init__(self, name: str = "pass_stage"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        pass


class SkipStage:
    """A stage that always skips."""

    @property
    def name(self) -> str:
        return "skip_stage"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return True

    def run(self, ctx: PipelineContext) -> None:
        raise AssertionError("should not be called")


class FailStage:
    """A stage that raises an HpxError."""

    def __init__(self, error: HpxError | None = None):
        self._error = error or CaptureError("boom")

    @property
    def name(self) -> str:
        return "fail_stage"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        raise self._error


class UnexpectedFailStage:
    """A stage that raises a non-HpxError exception."""

    @property
    def name(self) -> str:
        return "unexpected_fail"

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        raise RuntimeError("segfault or something")


class RecordingStage:
    """A stage that records when it ran."""

    def __init__(self, name: str, log: list[str]):
        self._name = name
        self._log = log

    @property
    def name(self) -> str:
        return self._name

    def should_skip(self, ctx: PipelineContext) -> bool:
        return False

    def run(self, ctx: PipelineContext) -> None:
        self._log.append(self._name)


class ProgressStage(PassStage):
    def run(self, ctx: PipelineContext) -> None:
        ctx.report_progress(
            "Running inferences",
            completed=2,
            total=10,
            unit="iterations",
            eta_s=4.5,
        )


class RecordingConsole:
    def __init__(self) -> None:
        self.starts: list[tuple[str, int, int]] = []
        self.updates: list[ProgressUpdate] = []

    def stage_start(self, name: str, index: int, total: int) -> None:
        self.starts.append((name, index, total))

    def progress_update(self, update: ProgressUpdate) -> None:
        self.updates.append(update)

    def stage_done(self, name: str) -> None:
        del name

    def stage_skip(self, name: str) -> None:
        del name

    def pipeline_done(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(tmp_path: Path):
    """Build a minimal ProfileConfig for testing."""
    model_file = tmp_path / "test.tflite"
    model_file.write_bytes(b"\x00")  # dummy
    return load_config(
        None,
        {
            "model": {"path": str(model_file)},
            "engine": {"type": "helia-rt"},
            "work_dir": str(tmp_path / "work"),
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPipelineRunner:
    def test_empty_pipeline(self, tmp_path: Path):
        config = _make_config(tmp_path)
        runner = PipelineRunner([])
        ctx = runner.run(config)
        assert isinstance(ctx, PipelineContext)

    def test_stages_run_in_order(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages: list[Stage] = [
            RecordingStage("first", log),
            RecordingStage("second", log),
            RecordingStage("third", log),
        ]
        runner = PipelineRunner(stages)
        runner.run(config)
        assert log == ["first", "second", "third"]

    def test_runner_reports_true_stage_positions_and_progress(self, tmp_path: Path):
        config = _make_config(tmp_path)
        console = RecordingConsole()
        runner = PipelineRunner([PassStage("first"), ProgressStage("second")], console=console)

        runner.run(config)

        assert console.starts == [("first", 1, 2), ("second", 2, 2)]
        assert console.updates == [
            ProgressUpdate(
                message="Running inferences",
                completed=2,
                total=10,
                unit="iterations",
                eta_s=4.5,
            )
        ]

    def test_skip_stage_not_executed(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages: list[Stage] = [
            RecordingStage("before", log),
            SkipStage(),
            RecordingStage("after", log),
        ]
        runner = PipelineRunner(stages)
        runner.run(config)
        assert log == ["before", "after"]

    def test_hpx_error_propagates(self, tmp_path: Path):
        config = _make_config(tmp_path)
        error = CaptureError("serial timeout", hint="check cable")
        stages: list[Stage] = [PassStage(), FailStage(error)]
        runner = PipelineRunner(stages)
        with pytest.raises(CaptureError, match="serial timeout"):
            runner.run(config)

    def test_unexpected_error_wrapped(self, tmp_path: Path):
        config = _make_config(tmp_path)
        stages: list[Stage] = [PassStage(), UnexpectedFailStage()]
        runner = PipelineRunner(stages)
        with pytest.raises(HpxError, match="Unexpected error.*unexpected_fail"):
            runner.run(config)

    def test_context_has_work_dir(self, tmp_path: Path):
        config = _make_config(tmp_path)
        runner = PipelineRunner([PassStage()])
        ctx = runner.run(config)
        assert ctx.work_dir.exists()

    def test_stages_after_failure_not_run(self, tmp_path: Path):
        config = _make_config(tmp_path)
        log: list[str] = []
        stages: list[Stage] = [
            RecordingStage("before", log),
            FailStage(),
            RecordingStage("after", log),
        ]
        runner = PipelineRunner(stages)
        with pytest.raises(HpxError):
            runner.run(config)
        assert log == ["before"]


class TestPipelineContext:
    def test_initial_state_is_none(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        assert ctx.soc is None
        assert ctx.board is None
        assert ctx.engine_adapter is None
        assert ctx.engine_artifacts is None
        assert ctx.firmware_dir is None
        assert ctx.build_dir is None
        assert ctx.binary_path is None
        assert ctx.pmu_result is None
        assert ctx.power_result is None
        assert ctx.report_paths == []

    def test_explicit_artifacts_start_empty(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        assert ctx.profile_firmware is None
        assert ctx.power_firmware is None
        assert ctx.deployed_power_firmware is None
        assert ctx.power_plan is None
        assert ctx.profile_run is None
        assert ctx.power_run is None

    def test_profile_run_transitions_are_immutable_and_mirrored(self, tmp_path: Path):
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        binary = tmp_path / "hpx_profiler"
        binary.touch()
        firmware = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=binary,
        )
        ctx.publish_profile_firmware(firmware)
        built_run = ctx.profile_run
        assert built_run is not None
        assert ctx.profile_firmware is firmware
        with pytest.raises(FrozenInstanceError):
            # Deliberate invalid assignment: proves the record is frozen.
            built_run.result = PmuResult(meta=FirmwareMeta())  # ty: ignore[invalid-assignment]

        deployment = DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
        ctx.publish_profile_deployment(deployment)
        assert ctx.profile_run is not built_run
        assert ctx.profile_run is not None
        assert ctx.profile_run.deployment is deployment

        result = PmuResult(meta=FirmwareMeta(clean_infer_count=3))
        ctx.publish_profile_result(result)
        assert ctx.profile_run.result is result
        assert ctx.pmu_result is result

    def test_power_run_transitions_clear_stale_deployment_and_mirror(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        plan = PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=5,
            count_source="configured",
        )
        ctx.publish_power_plan(plan)
        assert ctx.power_run is not None
        assert ctx.power_run.plan is plan
        assert ctx.power_plan is plan

        binary = tmp_path / "hpx_profiler_power"
        binary.touch()
        firmware = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=binary,
        )
        ctx.publish_power_firmware(firmware)
        deployment = DeploymentRecord(
            firmware=firmware,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        )
        ctx.publish_power_deployment(deployment)
        assert ctx.deployed_power_firmware is firmware

        ctx.publish_power_firmware(firmware)
        assert ctx.power_run.deployment is None
        assert ctx.deployed_power_firmware is None
        ctx.publish_power_deployment(deployment)

        result = PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10))
        ctx.publish_power_result(result)
        assert ctx.power_run.observation is not None
        assert ctx.power_run.observation.result is result
        assert ctx.power_run.observation.mode == "free_form"
        assert ctx.power_run.observation.integrity == "degraded"
        assert ctx.power_result is result

    def test_deployment_must_reference_current_artifact(self, tmp_path: Path):
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        profile_binary = tmp_path / "profile"
        other_binary = tmp_path / "other"
        profile_binary.touch()
        other_binary.touch()
        current = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=profile_binary,
        )
        other = FirmwareArtifact(
            role="profile",
            target_name="hpx_profiler",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=other_binary,
        )
        ctx.publish_profile_firmware(current)

        with pytest.raises(ValueError, match="current firmware artifact"):
            ctx.publish_profile_deployment(
                DeploymentRecord(
                    firmware=other,
                    target_id="apollo510_evb",
                    deployed_at="2026-07-18T00:00:00+00:00",
                )
            )

    def test_dedicated_result_requires_deployment(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="dedicated"))

        with pytest.raises(ValueError, match="must be deployed"):
            ctx.publish_power_result(
                PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10))
            )

    def test_replanning_clears_legacy_power_state(self, tmp_path: Path):
        from helia_profiler.power.base import PowerResult, PowerSummary

        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        set_power_firmware(ctx, binary_path=tmp_path / "old-power")
        old_power_binary = ctx.power_binary_path
        assert old_power_binary is not None
        set_power_firmware(
            ctx,
            artifact=FirmwareArtifact(
                role="power",
                target_name="hpx_profiler_power",
                app_dir=tmp_path,
                build_dir=tmp_path,
                binary_path=old_power_binary,
            ),
        )
        set_power_deployment(ctx)
        set_power_result(ctx, PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10)))

        ctx.publish_power_plan(PowerRunPlan(firmware_mode="dedicated", inference_count=7))

        assert ctx.power_binary_path is None
        assert ctx.power_firmware is None
        assert ctx.deployed_power_firmware is None
        assert ctx.power_result is None

    def test_context_is_mutable(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        from helia_profiler.results import FirmwareMeta, PmuResult

        set_profile_result(ctx, PmuResult(meta=FirmwareMeta(), layers=[]))
        assert ctx.pmu_result is not None
        assert ctx.pmu_result.layers == []

    def test_progress_is_optional_and_ui_independent(self, tmp_path: Path):
        config = _make_config(tmp_path)
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        updates: list[ProgressUpdate] = []
        ctx.report_progress("ignored without a sink")
        ctx.progress_sink = updates.append

        ctx.report_progress("Profile ready", kind="checkpoint", min_verbosity=1)

        assert updates == [
            ProgressUpdate(
                message="Profile ready",
                kind="checkpoint",
                min_verbosity=1,
            )
        ]


def test_default_pipeline_exposes_profile_then_power_steps():
    from helia_profiler.profiler import build_default_pipeline

    names = [stage.name for stage in build_default_pipeline()._stages]
    assert names.index("capture_pmu") < names.index("plan_power_run")
    assert names.index("plan_power_run") < names.index("build_power_firmware")
    assert names.index("build_power_firmware") < names.index("flash_power_firmware")
    assert names.index("flash_power_firmware") < names.index("capture_power")
    assert names.index("capture_power") < names.index("collect_power_terminal")


def test_pipeline_runner_installs_progress_sink_before_stages(tmp_path: Path):
    config = _make_config(tmp_path)
    updates: list[ProgressUpdate] = []

    class ProgressStage:
        name = "progress_probe"

        def should_skip(self, ctx):
            return False

        def run(self, ctx):
            ctx.report_progress("stage running")

    PipelineRunner([ProgressStage()], progress_sink=updates.append).run(config)

    assert updates == [ProgressUpdate(message="stage running")]


# ---------------------------------------------------------------------------
# Narrowing accessors (#162 Phase 4)
# ---------------------------------------------------------------------------


#: (accessor property, backing field, stage that produces the field).
#: The single source of truth for the read surface of ``PipelineContext``.
NARROWING_ACCESSORS = [
    ("resolved_soc", "soc", "ResolvePlatformStage"),
    ("resolved_board", "board", "ResolvePlatformStage"),
    ("prepared_artifacts", "engine_artifacts", "PrepareEngineStage"),
    ("prepared_adapter", "engine_adapter", "PrepareEngineStage"),
    ("resolved_firmware_dir", "firmware_dir", "GenerateFirmwareStage"),
    ("resolved_workspace", "dependency_workspace", "GenerateFirmwareStage"),
    ("built_binary_path", "binary_path", "BuildFirmwareStage"),
    ("captured_pmu", "pmu_result", "CapturePmuStage"),
    ("planned_arena_region", "arena_region", "PlanMemoryStage"),
]


class TestNarrowingAccessors:
    """Reading a stage product before its stage ran is a named, typed failure.

    Optional fields and derived properties stay the compatible read surface.
    An ``assert`` vanishes under ``-O`` and, when it does fire, says nothing
    about which stage was supposed to run — a ``PipelineError`` naming field
    and producer does.
    """

    @pytest.mark.parametrize("accessor,field,stage", NARROWING_ACCESSORS)
    def test_unset_raises_pipeline_error_naming_field_and_stage(
        self, tmp_path: Path, accessor: str, field: str, stage: str
    ):
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        assert getattr(ctx, field) is None
        with pytest.raises(PipelineError) as excinfo:
            getattr(ctx, accessor)
        message = str(excinfo.value)
        assert f"ctx.{field}" in message
        assert stage in message
        assert "has not run" in message
        # The hint is the actionable half of the contract, not decoration.
        assert excinfo.value.hint is not None
        assert stage in excinfo.value.hint

    @pytest.mark.parametrize("accessor,field,stage", NARROWING_ACCESSORS)
    def test_set_field_passes_straight_through(
        self, tmp_path: Path, accessor: str, field: str, stage: str
    ):
        del stage
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        sentinel = object()
        if field == "binary_path":
            set_profile_firmware(ctx, binary_path=sentinel)
        elif field == "pmu_result":
            set_profile_result(
                ctx, sentinel
            )  # ty: ignore[invalid-argument-type]  # the sentinel passthrough IS the test
        else:
            setattr(ctx, field, sentinel)
        assert getattr(ctx, accessor) is sentinel

    def test_pipeline_error_is_an_hpx_error(self, tmp_path: Path):
        # CLI and API callers catch HpxError; a stage-ordering bug must not
        # escape that net as a bare AssertionError.
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        with pytest.raises(HpxError):
            ctx.captured_pmu

    @pytest.mark.parametrize("accessor,field,stage", NARROWING_ACCESSORS)
    def test_named_producer_is_a_real_stage(self, accessor: str, field: str, stage: str):
        """The stage named in the error must exist AND produce the field.

        Review-hardened: the existence half alone let a wrong-but-real
        producer pass (naming BuildFirmwareStage for dependency_workspace
        stayed green), so the error text could lie. The source check pins
        the attribution: the named stage's module must assign the field
        (or publish it through a ctx.publish_* method — pmu_result's path).
        """
        del accessor
        import inspect

        from helia_profiler import stages

        cls = getattr(stages, stage, None)
        assert cls is not None, f"{stage} is not a stage class in helia_profiler.stages"
        assert isinstance(cls(), Stage)
        module = inspect.getmodule(cls)
        assert module is not None
        module_src = inspect.getsource(module)
        # (?!=) so a comparison (`ctx.field == x`) cannot count as producing
        # the field, and the publish hatch is per-field, not module-wide —
        # both holes let a wrong-but-real producer pass until the second
        # review round mutation-proved them.
        assigns = re.search(rf"ctx\.{field}\s*=(?!=)", module_src) is not None
        field_publisher = {
            "binary_path": "ctx.publish_profile_firmware(",
            "pmu_result": "ctx.publish_profile_result(",
        }.get(field)
        publishes = field_publisher is not None and field_publisher in module_src
        assert assigns or publishes, (
            f"{stage} does not appear to set ctx.{field} — the accessor's "
            "error text would name the wrong producer"
        )

    def test_accessors_cover_every_documented_field(self, tmp_path: Path):
        # Guards a rename on either side of the pair: every listed accessor is
        # a real property and every listed field is a field or derived property.
        ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
        context_fields = {f.name for f in dataclasses.fields(ctx)}
        for accessor, field, _stage in NARROWING_ACCESSORS:
            assert isinstance(getattr(type(ctx), accessor), property)
            assert field in context_fields or isinstance(getattr(type(ctx), field), property)


def test_no_assert_narrowing_of_context_fields_survives_in_src():
    """The acceptance criterion of #162 Phase 4, as a test.

    ``assert ctx.<field> is not None`` is a stage-ordering precondition wearing
    a crash costume: it is compiled out under ``-O`` and names no producer when
    it fires.  New sites must read through the narrowing accessors instead.
    """
    # Two patterns, deliberately scoped (the review round proved both blind
    # spots with mutations):
    #  * ctx-field narrowing anywhere in src/, anchored on `is not None` so a
    #    legitimate absence assertion is not banned with a misleading
    #    use-the-accessor message;
    #  * `assert self.<field> is not None` within pipeline.py itself, where
    #    PipelineContext lives — one such assert falsified this test's claim
    #    until the review round caught it. Other files' self-asserts narrow
    #    their own objects, not pipeline products, and stay legal.
    # Any assert rooted at ctx.<field> — is-not-None, truthiness, or the
    # parenthesised forms — EXCEPT a deliberate absence assertion
    # (`assert ctx.x is None`), which is an invariant check, not narrowing.
    # The guard is SYNTACTIC: narrowing through an intermediate local or a
    # walrus still evades it (one such survivor was found by review inside
    # firmware/context.py and converted to an explicit raise) — reviewers
    # stay the backstop for those spellings.
    ctx_assert = re.compile(r"^\s*assert\s+\(?(self\.)?ctx\.")
    absence_only = re.compile(r"^\s*assert\s+\(?(self\.)?ctx\.[\w.]+\s+is\s+None\b")
    self_pattern = re.compile(r"^\s*assert\s+\(?self\.\w+(\s+is\s+not\s+None\b|\s*\)?\s*(#.*)?$)")
    src = Path(__file__).resolve().parents[1] / "src" / "helia_profiler"
    offenders: list[str] = []
    for path in sorted(src.rglob("*.py")):
        in_pipeline_module = path.name == "pipeline.py"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            hit = ctx_assert.match(line) and not absence_only.match(line)
            if hit or (in_pipeline_module and self_pattern.match(line)):
                offenders.append(f"{path.relative_to(src.parent.parent)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "assert-narrowing of PipelineContext fields found in src/ — read the "
        "field through its PipelineContext accessor instead:\n" + "\n".join(offenders)
    )


def test_docs_accessor_table_matches_the_code():
    """docs/architecture/pipeline.md hand-duplicates the accessor table; the
    second review round showed a mutated producer left the docs silently
    divergent. Parse the table and hold it to NARROWING_ACCESSORS."""
    doc = (Path(__file__).resolve().parents[1] / "docs" / "architecture" / "pipeline.md").read_text(
        encoding="utf-8"
    )
    rows = re.findall(r"^\| `(\w+)` \| `(\w+)` \| `(\w+)` \|$", doc, flags=re.MULTILINE)
    assert set(rows) == set(NARROWING_ACCESSORS), (
        "the accessor table in docs/architecture/pipeline.md no longer "
        "matches pipeline.py's accessors — update both together"
    )


def test_print_results_renders_a_captured_run(tmp_path: Path):
    """The one migrated accessor site with no direct coverage: its
    reachability rests on a three-file invariant (profiler.py's None guard +
    CapturePmuStage always publishing) that nothing else pins. Exercise the
    happy path directly."""
    from helia_profiler.console import HpxConsole
    from helia_profiler.console.results import print_results
    from helia_profiler.results import FirmwareMeta, PmuResult

    ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
    set_profile_result(
        ctx,
        PmuResult(
            meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=1000),
            layers=[],
        ),
    )
    print_results(HpxConsole(verbosity=0), ctx)  # must not raise


def test_render_context_requires_resolved_platform_metadata(tmp_path: Path):
    """firmware/context.py's hand-rolled PipelineError for
    run_metadata.platform (a sub-field no accessor covers) — pinned like the
    nine accessor raises are."""
    from helia_profiler.engines import TFLM_ENGINE_HEADER
    from helia_profiler.engines.base import TflmArtifacts
    from helia_profiler.firmware.context import FirmwareRenderContext
    from helia_profiler.platform import get_board, get_soc

    ctx = PipelineContext(config=_make_config(tmp_path), work_dir=tmp_path)
    ctx.soc = get_soc("apollo510")
    ctx.board = get_board("apollo510_evb")
    ctx.engine_artifacts = TflmArtifacts(engine_header=TFLM_ENGINE_HEADER)
    with pytest.raises(PipelineError, match="run_metadata.platform.*ResolvePlatformStage"):
        FirmwareRenderContext.from_pipeline_context(ctx)
