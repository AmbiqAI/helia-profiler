"""RunIdentity of profile runs: facts from what the run recorded, reasoned unknowns, stable hashes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from helia_profiler.config import load_config
from helia_profiler.deps.compatibility import QualificationState
from helia_profiler.identity import CanonicalEngine, CanonicalKernel, Unknown
from helia_profiler.modelcost.model_analysis import LayerOps, ModelAnalysis
from helia_profiler.pipeline import PipelineContext
from helia_profiler.placement import Placement
from helia_profiler.platform import get_soc_for_board
from helia_profiler.profile_identity import profile_run_identity
from helia_profiler.report import write_report
from helia_profiler.results import (
    BuildImage,
    EngineInfo,
    FirmwareMeta,
    LayerResult,
    ModelInfo,
    PlatformInfo,
    PmuResult,
    RunMetadata,
    TimingInfo,
    ToolchainInfo,
)
from helia_profiler.results.dependencies import (
    ContentDigest,
    DependencyLockMode,
    DependencyLockState,
    DependencyModule,
    DependencyProvenance,
    DependencyWorkspace,
)
from helia_profiler.results.run_summary import load_run_summary
from helia_profiler.runtime_records import runtime
from tests.pipeline_context_helpers import set_profile_result

RT_COMMIT = "1" * 40
CORE_COMMIT = "2" * 40
ARM_COMMIT = "3" * 40
TFLM_COMMIT = "4" * 40
AOT_COMMIT = "5" * 40
LOCK_SHA = "6" * 64
MODEL_SHA = "a" * 64
BASELINE = "hpx-neuralspotx-0.8.1-2026-10-19.2"
NO_PROVENANCE = Unknown("the run recorded no dependency provenance")


def _lock(*, stamp="2026-10-19T11:59:32+00:00", tool="0.2.3") -> bytes:
    return f"""schema_version: 4
targets:
  apollo510_evb:
    generated_at: '{stamp}'
    nsx_tool:
      version: {tool}
    target:
      board: apollo510_evb
      soc: apollo510
      toolchain: arm-none-eabi-gcc
    modules:
      nsx-cmsis-nn:
        project: ns-cmsis-nn
        kind: git
        resolved:
          url: https://github.com/AmbiqAI/ns-cmsis-nn.git
          commit: {CORE_COMMIT}
          content_hash: sha256:{"7" * 64}
          acquired_at: '{stamp}'
      nsx-tooling:
        project: neuralspotx
        kind: packaged
        resolved:
          tool_version: {tool}
          content_hash: sha256:{"8" * 64}
          acquired_at: '{stamp}'
""".encode()


def _module(name, project, *, url=None, tag=None, commit=None, kind="git") -> DependencyModule:
    return DependencyModule(
        name=name,
        project=project,
        kind=kind,
        requested_ref=tag or "main",
        requested_tag=tag,
        peeled_commit=commit,
        content_hash=ContentDigest("sha256", "9" * 64),
        url=url,
        vendored_at=f"modules/{project}",
    )


HELIA_RT = _module(
    "nsx-helia-rt", "helia-rt", url="https://github.com/AmbiqAI/helia-rt.git", commit=RT_COMMIT
)
HELIA_CORE = _module(
    "nsx-cmsis-nn",
    "ns-cmsis-nn",
    url="https://github.com/AmbiqAI/ns-cmsis-nn.git",
    tag="v7.47.0",
    commit=CORE_COMMIT,
)
ARM_CMSIS_NN = _module(
    "arm-cmsis-nn",
    "arm-cmsis-nn",
    url="https://github.com/ARM-software/CMSIS-NN.git",
    tag="v7.0.0",
    commit=ARM_COMMIT,
)
TFLM = _module(
    "nsx-tflite-micro",
    "nsx-tflite-micro",
    url="https://github.com/AmbiqAI/nsx-tflite-micro.git",
    tag="v1.3.0",
    commit=TFLM_COMMIT,
)


def _provenance(modules, *, hpx_version="0.2.3", engine_inputs=None) -> DependencyProvenance:
    digest = ContentDigest("sha256", LOCK_SHA)
    inputs = {"hpx_version": hpx_version, "engine": engine_inputs or {}}
    return DependencyProvenance(
        DependencyWorkspace(1, "fp", BASELINE, "base-fp", digest, inputs, Path("ws")),
        DependencyLockState(DependencyLockMode.REUSED, False, False, True, 4, digest, digest),
        tuple(modules),
        (),
        QualificationState.QUALIFIED,
    )


def _pmu(cycles: float = 10.0) -> PmuResult:
    return PmuResult(
        meta=FirmwareMeta(arena_size=4096, clean_infer_avg_us=120),
        layers=[LayerResult(id=0, op="CONV_2D", counters={"CPU_CYCLES": cycles}, cycles=cycles)],
    )


def _ctx(
    root: Path,
    *,
    engine="helia-rt",
    backend=None,
    modules=(HELIA_RT, HELIA_CORE),
    version="2.2.2",
    flash=b"flat image",
    lock=None,
    hpx_version="0.2.3",
    engine_inputs=None,
    model="model.tflite",
    profiling=None,
    engine_config=None,
) -> PipelineContext:
    root.mkdir(parents=True, exist_ok=True)
    overrides = {
        "model": {"path": model},
        "engine": {
            "type": engine,
            **({"backend": backend} if backend else {}),
            **({"config": engine_config} if engine_config else {}),
        },
        "output": {"dir": str(root / "results")},
    }
    if profiling:
        overrides["profiling"] = profiling
    ctx = PipelineContext(config=load_config(None, overrides), work_dir=root)
    lock_path = root / "nsx.lock"
    lock_path.write_bytes(lock if lock is not None else _lock())
    ctx.dependency_lock_path = lock_path
    ctx.arena_region = Placement.TCM
    ctx.weights_region = Placement.MRAM
    ctx.run_metadata = RunMetadata(
        hpx_version=hpx_version,
        run_id="run-1",
        timestamp="2026-10-19T12:00:00+00:00",
        platform=PlatformInfo(board="apollo510_evb", cpu_clock_name="lp", cpu_clock_mhz=96),
        model=ModelInfo(name="model.tflite", size_bytes=10, sha256=MODEL_SHA),
        toolchain=ToolchainInfo(compiler="arm-none-eabi-gcc", compiler_version="14.2.1"),
        build_images=(
            BuildImage(
                role="profile",
                target_name="hpx_profiler",
                binary_name="hpx_profiler.axf",
                sha256=hashlib.sha256(b"elf with " + str(root).encode()).hexdigest(),
                flash_sha256=hashlib.sha256(flash).hexdigest(),
            ),
        ),
        engine=EngineInfo(type=engine, version=version, backend=backend),
        dependencies=_provenance(modules, hpx_version=hpx_version, engine_inputs=engine_inputs),
    )
    set_profile_result(ctx, _pmu())
    return ctx


def test_a_helia_rt_run_states_what_it_linked(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path))

    assert identity.runtime.engine is CanonicalEngine.HELIA_RT
    assert identity.runtime.repository == "https://github.com/AmbiqAI/helia-rt.git"
    assert (identity.runtime.version, identity.runtime.commit) == ("2.2.2", RT_COMMIT)
    assert identity.kernels.library is CanonicalKernel.HELIA_CORE
    assert identity.kernels.repository == "https://github.com/AmbiqAI/ns-cmsis-nn.git"
    assert (identity.kernels.version, identity.kernels.commit) == ("v7.47.0", CORE_COMMIT)
    assert identity.prepared_archive_sha256 is None
    assert identity.baseline_id == BASELINE
    assert identity.dependency_lock_sha256 == LOCK_SHA
    assert identity.model_sha256 == MODEL_SHA
    assert identity.inputs_sha256 == Unknown(
        "profile firmware feeds zero-filled inputs and pins no input data"
    )
    assert identity.producer == "0.2.3"
    build = identity.build
    assert (build.toolchain, build.toolchain_version) == ("arm-none-eabi-gcc", "14.2.1")
    assert (build.board, build.cpu_hz, build.clock_profile) == ("apollo510_evb", 96_000_000, "lp")
    assert (build.arena_location, build.weights_location) == ("tcm", "mram")
    assert build.arena_bytes == 4096
    run = identity.run
    assert (run.lifecycle, run.iterations, run.warmups) == ("carry", 100, 5)
    assert run.timing_scope == "invoke_only"
    assert isinstance(identity.build_hash, str) and len(identity.build_hash) == 64
    assert isinstance(identity.observation_hash, str) and len(identity.observation_hash) == 64


def test_the_build_hash_ignores_the_hpx_release_and_lock_stamps_but_not_the_image(tmp_path):
    first = profile_run_identity(_ctx(tmp_path / "a"))
    later = profile_run_identity(
        _ctx(
            tmp_path / "b",
            hpx_version="0.3.0",
            lock=_lock(stamp="2026-11-02T08:00:00+00:00", tool="0.2.4"),
        )
    )
    assert first.producer != later.producer
    assert first.build_hash == later.build_hash

    reflashed = profile_run_identity(_ctx(tmp_path / "c", flash=b"another image"))
    assert reflashed.build_hash != first.build_hash


def test_the_build_hash_covers_the_flashed_image_not_the_elf(tmp_path):
    """The ELF digest names the build directory; two hosts' builds still match."""
    here, there = _ctx(tmp_path / "here"), _ctx(tmp_path / "there")
    assert here.run_metadata.build_images[0].sha256 != there.run_metadata.build_images[0].sha256
    assert profile_run_identity(here).build_hash == profile_run_identity(there).build_hash


def test_the_build_hash_changes_with_the_model_and_what_the_lock_resolved(tmp_path):
    base = profile_run_identity(_ctx(tmp_path / "a")).build_hash

    other_model = _ctx(tmp_path / "b")
    assert other_model.run_metadata.model is not None
    other_model.run_metadata.model.sha256 = "b" * 64
    assert profile_run_identity(other_model).build_hash != base

    moved_kernels = _ctx(tmp_path / "c", lock=_lock().replace(CORE_COMMIT.encode(), b"c" * 40))
    assert profile_run_identity(moved_kernels).build_hash != base


def test_the_kernel_library_is_the_linked_module_not_the_backend_label(tmp_path):
    # heliaRT's "helia" backend names heliaCORE, but this lock resolved Arm's CMSIS-NN.
    identity = profile_run_identity(
        _ctx(tmp_path, backend="helia", modules=(HELIA_RT, ARM_CMSIS_NN))
    )

    assert identity.kernels.library is CanonicalKernel.CMSIS_NN
    assert (identity.kernels.version, identity.kernels.commit) == ("v7.0.0", ARM_COMMIT)


def test_a_backend_label_without_a_linked_module_states_no_library(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, engine="litert", backend="cmsis_nn", modules=(TFLM,), version=None)
    )

    reason = Unknown("the dependency lock names 0 CMSIS-NN modules, not one")
    assert identity.kernels.library == reason
    assert identity.kernels.commit == reason


def test_only_litert_reference_links_no_kernel_module(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path, backend=None, modules=(HELIA_RT,)))

    assert identity.kernels.library == Unknown(
        "the dependency lock names 0 CMSIS-NN modules, not one"
    )


def test_litert_reference_links_no_kernel_module_and_states_reference(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path, engine="litert", modules=(TFLM,), version=None))

    assert identity.runtime.engine is CanonicalEngine.LITERT
    assert identity.runtime.version == "v1.3.0"
    assert identity.runtime.commit == TFLM_COMMIT
    assert identity.kernels.library is CanonicalKernel.REFERENCE
    assert identity.kernels.commit == TFLM_COMMIT


def test_a_reference_label_with_a_linked_module_reports_the_module(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, engine="litert", modules=(ARM_CMSIS_NN, TFLM), version=None)
    )

    assert identity.kernels.library is CanonicalKernel.CMSIS_NN


def test_two_kernel_modules_are_not_guessed_between(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path, modules=(HELIA_RT, HELIA_CORE, ARM_CMSIS_NN)))

    assert identity.kernels.library == Unknown(
        "the dependency lock names 2 CMSIS-NN modules, not one"
    )


def test_a_local_kernel_module_states_what_its_lock_entry_lacks(tmp_path):
    local = _module("nsx-cmsis-nn", "ns-cmsis-nn", kind="local")
    identity = profile_run_identity(_ctx(tmp_path, modules=(HELIA_RT, local)))

    assert identity.kernels.library is CanonicalKernel.HELIA_CORE
    assert identity.kernels.repository == Unknown("the lock states no repository for nsx-cmsis-nn")
    assert identity.kernels.version == Unknown("the lock states no release tag for nsx-cmsis-nn")
    assert identity.kernels.commit == Unknown("the lock states no commit for nsx-cmsis-nn")


def test_a_local_helia_rt_module_keeps_the_engine_version_and_states_no_source(tmp_path):
    local = _module("nsx-helia-rt", "helia-rt", kind="local")
    identity = profile_run_identity(_ctx(tmp_path, modules=(local,), version="2.2.0"))

    reason = Unknown("the nsx-helia-rt module is a local copy; the lock records no source")
    assert identity.runtime.version == "2.2.0"
    assert (identity.runtime.repository, identity.runtime.commit) == (reason, reason)


def test_a_runtime_module_missing_from_the_lock_is_stated(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path, modules=(HELIA_CORE,)))

    assert identity.runtime.commit == Unknown("the dependency lock names no nsx-helia-rt module")


def test_a_runtime_version_falls_back_to_the_lock_tag_then_a_reason(tmp_path):
    tagged = replace(HELIA_RT, requested_tag="v2.2.2")
    assert profile_run_identity(
        _ctx(tmp_path / "a", modules=(tagged, HELIA_CORE), version=None)
    ).runtime.version == ("v2.2.2")
    assert profile_run_identity(_ctx(tmp_path / "b", version=None)).runtime.version == Unknown(
        "neither the engine nor the lock states a nsx-helia-rt version"
    )


def _aot_inputs(commit: str | None) -> dict:
    return {
        "type": "helia-aot",
        "source": {"package": "helia-aot", "version": "0.31.0", "commit": commit},
    }


def test_helia_aot_is_the_installed_package_the_workspace_recorded(tmp_path):
    identity = profile_run_identity(
        _ctx(
            tmp_path,
            engine="helia-aot",
            modules=(HELIA_CORE,),
            version="0.30.0",
            engine_inputs=_aot_inputs(AOT_COMMIT),
        )
    )

    record = runtime("helia-aot")
    assert record is not None
    assert identity.runtime.engine is CanonicalEngine.HELIA_AOT
    assert identity.runtime.repository == f"https://github.com/{record.source.repo}"
    assert (identity.runtime.version, identity.runtime.commit) == ("0.31.0", AOT_COMMIT)
    assert identity.kernels.library is CanonicalKernel.HELIA_CORE
    assert identity.run.timing_scope == "restore_and_invoke"


def test_a_non_vcs_helia_aot_install_states_why_it_has_no_commit(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, engine="helia-aot", modules=(HELIA_CORE,), engine_inputs=_aot_inputs(None))
    )

    assert identity.runtime.commit == Unknown(
        "helia-aot 0.31.0 is not a VCS install; it records no commit"
    )


def test_a_workspace_without_the_helia_aot_package_keeps_the_engine_version(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, engine="helia-aot", modules=(HELIA_CORE,), version="0.31.0")
    )

    assert identity.runtime.version == "0.31.0"
    assert identity.runtime.commit == Unknown(
        "the workspace records no installed helia-aot package"
    )


def test_the_busy_loop_probe_times_no_input_restore(tmp_path):
    identity = profile_run_identity(
        _ctx(
            tmp_path,
            engine="helia-aot",
            modules=(HELIA_CORE,),
            engine_inputs=_aot_inputs(AOT_COMMIT),
            profiling={"clean_window_probe": "busy_loop"},
        )
    )

    assert identity.run.timing_scope == "invoke_only"


def test_executorch_is_the_runtime_record_its_adapter_enforces(tmp_path):
    identity = profile_run_identity(
        _ctx(
            tmp_path,
            engine="executorch",
            backend="arm",
            modules=(ARM_CMSIS_NN, _module("nsx-executorch", "nsx-executorch", kind="local")),
            version=None,
            model="model.pte",
        )
    )

    record = runtime("executorch")
    assert record is not None
    assert identity.runtime.engine is CanonicalEngine.EXECUTORCH
    assert identity.runtime.repository == f"https://github.com/{record.source.repo}"
    assert (identity.runtime.version, identity.runtime.commit) == (
        record.version,
        record.source.commit,
    )
    assert identity.kernels.library is CanonicalKernel.CMSIS_NN


def test_a_run_without_provenance_states_why(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.run_metadata.dependencies = None
    identity = profile_run_identity(ctx)

    assert identity.runtime.commit == NO_PROVENANCE
    assert identity.kernels.library == NO_PROVENANCE
    assert identity.baseline_id == NO_PROVENANCE
    assert identity.dependency_lock_sha256 == NO_PROVENANCE
    assert identity.producer == NO_PROVENANCE
    # The lock file itself is still there, so the build hash does not need provenance.
    assert isinstance(identity.build_hash, str)


def test_a_workspace_without_a_release_states_why(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.run_metadata.dependencies is not None
    ctx.run_metadata.dependencies.workspace.inputs.pop("hpx_version")

    assert profile_run_identity(ctx).producer == Unknown("the workspace records no hpx_version")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        pytest.param(
            lambda ctx: setattr(ctx.run_metadata, "build_images", ()),
            "the run recorded no profile image",
            id="no_image",
        ),
        pytest.param(
            lambda ctx: setattr(
                ctx.run_metadata,
                "build_images",
                (replace(ctx.run_metadata.build_images[0], flash_sha256=None),),
            ),
            "the build left no flat image to hash",
            id="no_flat_image",
        ),
        pytest.param(
            lambda ctx: setattr(ctx, "dependency_lock_path", None),
            "the run kept no dependency lock",
            id="no_lock",
        ),
        pytest.param(
            lambda ctx: ctx.dependency_lock_path.unlink(),
            "the run's dependency lock is unreadable",
            id="unreadable_lock",
        ),
        pytest.param(
            lambda ctx: ctx.dependency_lock_path.write_text("not: [an, nsx, lock"),
            "the dependency lock is not an NSX lock",
            id="not_a_lock",
        ),
        pytest.param(
            lambda ctx: setattr(ctx.run_metadata, "model", None),
            "the run recorded no model",
            id="no_model",
        ),
    ],
)
def test_a_build_hash_that_cannot_be_formed_carries_its_reason(tmp_path, change, reason):
    ctx = _ctx(tmp_path)
    change(ctx)

    assert profile_run_identity(ctx).build_hash == Unknown(reason)


def test_only_the_power_image_is_not_the_profile_image(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.run_metadata.build_images = (replace(ctx.run_metadata.build_images[0], role="power"),)

    assert profile_run_identity(ctx).build_hash == Unknown("the run recorded no profile image")


def test_missing_build_facts_carry_their_reason(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.run_metadata.toolchain = None
    ctx.run_metadata.platform = None
    ctx.arena_region = None
    ctx.weights_region = None
    set_profile_result(ctx, replace(_pmu(), meta=FirmwareMeta()))
    build = profile_run_identity(ctx).build

    assert build.toolchain == build.toolchain_version == Unknown("the run recorded no toolchain")
    assert build.cpu_hz == build.clock_profile == Unknown("the run resolved no CPU clock")
    assert build.arena_location == Unknown("the memory plan resolved no arena placement")
    assert build.weights_location == Unknown("the memory plan resolved no weights placement")
    assert build.arena_bytes == Unknown(
        "neither the configuration nor the firmware states an arena size"
    )


def test_a_toolchain_without_a_version_says_so(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.run_metadata.toolchain = ToolchainInfo(compiler="armclang", compiler_version="")

    build = profile_run_identity(ctx).build
    assert build.toolchain == "armclang"
    assert build.toolchain_version == Unknown("the compiler reported no version")


def test_the_configured_arena_size_wins_over_the_firmware_report(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.config = replace(ctx.config, model=replace(ctx.config.model, arena_size=8192))

    assert profile_run_identity(ctx).build.arena_bytes == 8192


def test_the_observation_hash_follows_the_measurement_not_host_timing(tmp_path):
    ctx = _ctx(tmp_path)
    first = profile_run_identity(ctx).observation_hash

    ctx.run_metadata.timing = TimingInfo(capture_duration_s=12.5, phases={"attach": 0.4})
    assert profile_run_identity(ctx).observation_hash == first

    set_profile_result(ctx, _pmu(cycles=11.0))
    assert profile_run_identity(ctx).observation_hash != first

    set_profile_result(
        ctx, replace(_pmu(), meta=FirmwareMeta(arena_size=4096, clean_infer_avg_us=121))
    )
    assert profile_run_identity(ctx).observation_hash != first


def test_a_run_without_a_capture_has_no_observation_hash(tmp_path):
    ctx = _ctx(tmp_path)
    set_profile_result(ctx, None)

    assert profile_run_identity(ctx).observation_hash is None


def test_the_summary_carries_the_identity(tmp_path):
    ctx = _ctx(tmp_path)

    write_report(ctx)

    identity = ctx.run_metadata.identity
    assert identity is not None
    summary = json.loads((tmp_path / "results" / "summary.json").read_text())
    assert summary["identity"] == identity.to_dict()
    assert summary["identity"]["inputs_sha256"] == {
        "unknown": "profile firmware feeds zero-filled inputs and pins no input data"
    }
    loaded = load_run_summary(tmp_path / "results" / "summary.json")
    assert loaded.identity == identity.to_dict()
    assert "identity" not in loaded.extras


HELIA_RT_SOURCE = _module(
    "helia-rt-source", "helia-rt", url="https://github.com/AmbiqAI/helia-rt.git", commit="6" * 40
)


def test_litert_runtime_and_reference_kernels_come_from_helia_rt_source(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, engine="litert", modules=(TFLM, HELIA_RT_SOURCE), version=None)
    )
    assert identity.runtime.commit == "6" * 40
    assert identity.kernels.library is CanonicalKernel.REFERENCE
    assert identity.kernels.commit == "6" * 40


def test_a_prebuilt_heliart_says_its_kernels_are_in_the_archive(tmp_path):
    identity = profile_run_identity(
        _ctx(tmp_path, modules=(HELIA_RT,), engine_config={"dist_path": str(tmp_path)})
    )
    assert identity.kernels.library == Unknown(
        "heliaCORE is built into the prebuilt heliaRT archive"
    )


def test_a_nan_counter_still_gives_an_observation_hash(tmp_path):
    ctx = _ctx(tmp_path)
    set_profile_result(ctx, _pmu(float("nan")))
    assert isinstance(profile_run_identity(ctx).observation_hash, str)


def _analysis(*ops: str, index_space: Literal["source", "execution"] = "source") -> ModelAnalysis:
    layers = [LayerOps(id=index, op=op) for index, op in enumerate(ops)]
    return ModelAnalysis(layers, 0, 0, 0, index_space=index_space)


@pytest.mark.parametrize(
    "engine,analysis,expected",
    [
        ("helia-rt", _analysis("CALL_ONCE", "CONV_2D"), True),
        ("litert", _analysis("CONV_2D"), False),
        ("helia-rt", None, Unknown("the run recorded no analysis of the source model's operators")),
        ("helia-aot", _analysis("CALL_ONCE", index_space="execution"), False),
    ],
)
def test_the_first_invocation_states_whether_it_runs_the_initializer(
    tmp_path, engine, analysis, expected
):
    ctx = _ctx(tmp_path, engine=engine, profiling={"warmup": 0})
    ctx.model_analysis = analysis
    run = profile_run_identity(ctx).run

    assert run.first_invoke_initializer == expected
    assert (run.lifecycle, run.instance_boundary) == ("carry", "boot")
    # The clean window always warms up first, even with no configured warmups.
    assert (run.warmups, run.first_invoke_window) == (0, "warmup")


def test_executorch_reloads_its_instance_for_every_invocation(tmp_path):
    ctx = _ctx(tmp_path, engine="executorch", backend="arm", version=None, model="model.pte")
    ctx.soc = get_soc_for_board("apollo510_evb")
    run = profile_run_identity(ctx).run

    assert (run.lifecycle, run.instance_boundary, run.first_invoke_window) == (
        "single",
        "invocation",
        "measured",
    )
    assert isinstance(run.first_invoke_initializer, Unknown)
    assert (run.timing_clock, run.timing_clock_hz, run.timing_statistic) == (
        "cpu",
        96_000_000,
        "mean",
    )


@pytest.mark.parametrize(
    "board,probe,clock,clock_hz",
    [
        ("apollo510_evb", "infer", "stimer", 32768),
        ("apollo4p_evb", "infer", "cpu", 96_000_000),
        ("apollo4p_evb", "busy_loop", "stimer", 32768),
    ],
)
def test_the_clean_window_states_the_clock_that_timed_it(tmp_path, board, probe, clock, clock_hz):
    ctx = _ctx(tmp_path, profiling={"clean_window_probe": probe})
    ctx.soc = get_soc_for_board(board)
    run = profile_run_identity(ctx).run

    assert (run.timing_clock, run.timing_clock_hz, run.timing_statistic) == (
        clock,
        clock_hz,
        "mean",
    )


def test_a_run_without_a_resolved_soc_states_why_its_clock_is_unknown(tmp_path):
    identity = profile_run_identity(_ctx(tmp_path))

    assert identity.run.timing_clock == Unknown("the run resolved no SoC")
    assert identity.intent_hash is None
