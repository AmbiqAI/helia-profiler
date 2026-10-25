from __future__ import annotations

import ast
import importlib
import inspect
import json
from pathlib import Path

import pytest

import helia_profiler


def test_every_package_export_has_one_stability_tier() -> None:
    assert set(helia_profiler.__api_stability__) == set(helia_profiler.__all__)
    assert set(helia_profiler.__api_stability__.values()) == {
        "stable",
        "experimental",
        "implementation",
    }


def test_stability_tiers_match_the_published_contract() -> None:
    expected = json.loads(
        (Path(__file__).parent / "fixtures/api_stability.json").read_text(encoding="utf-8")
    )
    assert helia_profiler.__api_stability__ == expected
    assert set(helia_profiler._EXPORTS) == set(helia_profiler.__all__)
    assert len(helia_profiler.__all__) == len(set(helia_profiler.__all__))


def test_profile_signature_keeps_config_and_keyword_progress_sink() -> None:
    parameters = list(inspect.signature(helia_profiler.profile).parameters.values())

    assert [(parameter.name, parameter.kind) for parameter in parameters] == [
        ("config", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        ("progress_sink", inspect.Parameter.KEYWORD_ONLY),
    ]


def test_session_core_method_signatures_are_explicit() -> None:
    profile_parameters = inspect.signature(helia_profiler.Session.profile).parameters
    compare_parameters = inspect.signature(helia_profiler.Session.compare).parameters

    assert list(profile_parameters) == ["self", "model", "progress_sink"]
    assert profile_parameters["progress_sink"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(compare_parameters) == [
        "self",
        "baseline",
        "candidate",
        "output_dir",
        "profile",
    ]


def test_public_surface_membership_snapshot() -> None:
    """#229 D8: the tier-consistency check alone lets the surface drift —
    and a size pin would still admit a simultaneous add+remove. This exact
    membership list makes every surface change a deliberate, reviewed edit
    here, naming the change in the PR."""
    assert sorted(helia_profiler.__all__) == [
        "BoardDef",
        "BuildConfig",
        "BuildError",
        "BuildFacts",
        "CanonicalEngine",
        "CanonicalKernel",
        "CaptureError",
        "ClockSelection",
        "ComparabilityAssessment",
        "ComparabilityIssue",
        "ComparabilitySeverity",
        "CompareResult",
        "ComparisonProfile",
        "ComparisonVerdict",
        "CompatibilityBaseline",
        "CompatibilityResolution",
        "CompiledBuild",
        "ConfigError",
        "DependencyError",
        "DependencyLockProvenance",
        "DeterministicCaptureError",
        "DoctorCheck",
        "DoctorResult",
        "DoctorVersionCheck",
        "ElfSection",
        "EnergyFacts",
        "EngineConfig",
        "EngineDTypeCapability",
        "EngineError",
        "EngineSource",
        "EngineType",
        "Expect",
        "FirmwareError",
        "FirmwareMeta",
        "FixtureBatchCompletion",
        "FixtureBoardPowerRequest",
        "FixtureBuild",
        "FixtureCapabilities",
        "FixtureCapability",
        "FixtureCaptureGuard",
        "FixtureCaptureResult",
        "FixtureDType",
        "FixtureFile",
        "FixtureIO",
        "FixtureKernelSource",
        "FixtureMemory",
        "FixtureMethod",
        "FixtureOutput",
        "FixturePowerGuard",
        "FixturePowerSync",
        "FixturePsramObservation",
        "FixturePsramPayload",
        "FixturePsramWeights",
        "FixtureRailEnergy",
        "FixtureRailPowerRequest",
        "FixtureRole",
        "FixtureRuntimeSource",
        "FixtureStatePolicy",
        "FixtureTarget",
        "FixtureTensor",
        "FixtureTiming",
        "FixtureTimingScope",
        "FrozenModel",
        "FrozenRun",
        "FrozenRuntime",
        "HeartbeatConfig",
        "HpxError",
        "JLinkProbe",
        "JLinkProbeMatch",
        "KernelIdentity",
        "LayerResult",
        "Lifecycle",
        "LoadSegment",
        "LocalInput",
        "LockError",
        "MeasurementScope",
        "MemoryPlan",
        "MetricDirection",
        "MetricPolicy",
        "MetricVerdict",
        "MissingMetricPolicy",
        "ModelAnalysis",
        "ModelConfig",
        "NetworkError",
        "NsxModuleOverride",
        "NsxModuleRef",
        "ObservationMode",
        "OnDevicePowerSummary",
        "OperatorGuard",
        "OutputConfig",
        "OutputFormat",
        "PerAxisQuantization",
        "PerTensorQuantization",
        "Placement",
        "PlatformError",
        "PmuCounter",
        "PmuResult",
        "PowerConfig",
        "PowerError",
        "PowerIntegrity",
        "PowerMetadata",
        "PowerMode",
        "PowerObservation",
        "PowerResult",
        "PowerTerminalRecord",
        "PreparedRuntimeManifest",
        "PreparedUpstreamRuntime",
        "PresetResult",
        "ProfileConfig",
        "ProfileResult",
        "ProfilingConfig",
        "ProgressUpdate",
        "Qualification",
        "QualificationState",
        "QualifiedFixtureTarget",
        "QualifiedTarget",
        "ReferenceOrigin",
        "ReferenceOutputs",
        "ReportError",
        "ResetStrategy",
        "ResolverPlan",
        "ResultArtifact",
        "ResultIssue",
        "ResultManifest",
        "ResultValidity",
        "RunEvaluation",
        "RunFacts",
        "RunIdentity",
        "RunMetadata",
        "RunResult",
        "RunResultError",
        "RunResultRecord",
        "RunStatus",
        "RuntimeABI",
        "RuntimeHeader",
        "RuntimeIdentity",
        "RuntimeProvider",
        "RuntimeQualification",
        "RuntimeRecord",
        "RuntimeSource",
        "SectionInventory",
        "SemanticHash",
        "SerialPortInfo",
        "Session",
        "SocDef",
        "SupportBundleManifest",
        "SupportBundleOptions",
        "SupportBundleSection",
        "TargetConfig",
        "TensorComparison",
        "TimeoutsConfig",
        "Tolerance",
        "Toolchain",
        "ToolchainIdentity",
        "ToolchainInfo",
        "Transport",
        "TypedFixture",
        "TypedFixtureModelAnalysis",
        "Unknown",
        "VerdictStatus",
        "Verification",
        "VerifiedPreparedRuntime",
        "VersionError",
        "__version__",
        "analyze_run_model",
        "analyze_run_model_for_engine",
        "assess_comparability",
        "build",
        "build_platform_registry",
        "canonical_engine",
        "canonical_kernel",
        "capabilities",
        "collect_support_bundle",
        "compare_fixture_outputs",
        "compare_outputs",
        "evaluate_comparison_profile",
        "evaluate_run",
        "examples",
        "get_soc",
        "host_reference",
        "hpx_cache_root",
        "load_build",
        "load_compatibility_baseline",
        "load_result_manifest",
        "load_run",
        "profile",
        "qualification",
        "read_dependency_lock_provenance",
        "run_build",
        "runtimes",
        "section_inventory",
        "verify_support_bundle",
        "write_support_bundle",
    ]
    for name in helia_profiler.__all__:
        assert hasattr(helia_profiler, name), f"__all__ exports missing attribute {name}"


def test_the_fixture_module_is_gone() -> None:
    """The run API replaced the fixture API; the package root is the public contract."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("helia_profiler.fixture")
    assert not hasattr(helia_profiler, "fixture")


def test_the_section_inventory_is_exported_from_the_package_root() -> None:
    from helia_profiler.hostenv import elf_inventory

    for name in ("section_inventory", "SectionInventory", "ElfSection", "LoadSegment"):
        assert getattr(helia_profiler, name) is getattr(elf_inventory, name)
        assert helia_profiler.__api_stability__[name] == "experimental"


def test_export_catalog_keys_are_literal_and_unique() -> None:
    tree = ast.parse(Path(helia_profiler.__file__).read_text(encoding="utf-8"))
    catalog = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_EXPORTS" for target in node.targets)
    )
    assert isinstance(catalog, ast.Dict)
    keys = [ast.literal_eval(key) for key in catalog.keys if key is not None]
    assert len(keys) == len(catalog.keys) == len(set(keys))
    assert set(keys) == set(helia_profiler.__all__)
