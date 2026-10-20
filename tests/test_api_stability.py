from __future__ import annotations

import inspect
from itertools import combinations

import helia_profiler


def test_every_package_export_has_one_stability_tier() -> None:
    assert set(helia_profiler.__api_stability__) == set(helia_profiler.__all__)
    assert set(helia_profiler.__api_stability__.values()) == {
        "stable",
        "experimental",
        "implementation",
    }


def test_stability_tiers_partition_the_public_surface() -> None:
    """The docs pipeline scopes the rendered Python reference to the stable
    and experimental tiers and badges each symbol with its tier (#330), so a
    name in two tiers or in none would silently change what ships. The three
    private sets are the source; ``__api_stability__`` is built from them."""
    tiers = {
        "stable": helia_profiler._STABLE_API,
        "experimental": helia_profiler._EXPERIMENTAL_API,
        "implementation": helia_profiler._IMPLEMENTATION_API,
    }
    sizes = {name: len(members) for name, members in tiers.items()}

    assert sizes == {"stable": 45, "experimental": 91, "implementation": 13}
    assert sum(sizes.values()) == 149 == len(helia_profiler.__all__)

    for left, right in combinations(tiers, 2):
        assert not tiers[left] & tiers[right], (
            f"{sorted(tiers[left] & tiers[right])} is in both {left} and {right}"
        )
    untiered = set(helia_profiler.__all__) - set().union(*tiers.values())
    assert not untiered, f"{sorted(untiered)} is in __all__ but in no tier"

    # __all__ may repeat a name without any set noticing.
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
        "EngineConfig",
        "EngineDTypeCapability",
        "EngineError",
        "EngineType",
        "Expect",
        "FirmwareError",
        "FirmwareMeta",
        "FixtureCapabilities",
        "FixtureCaptureGuard",
        "FixtureFile",
        "FixtureIO",
        "FixtureMethod",
        "FixtureOutput",
        "FixtureRole",
        "FixtureTarget",
        "FixtureTensor",
        "FixtureTimingScope",
        "FrozenModel",
        "FrozenRun",
        "FrozenRuntime",
        "HeartbeatConfig",
        "HpxError",
        "JLinkProbe",
        "JLinkProbeMatch",
        "LayerResult",
        "Lifecycle",
        "LocalInput",
        "LockError",
        "MeasurementScope",
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
        "OutputConfig",
        "OutputFormat",
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
        "ResultArtifact",
        "ResultIssue",
        "ResultManifest",
        "ResultValidity",
        "RunEvaluation",
        "RunMetadata",
        "RunResult",
        "RunStatus",
        "RuntimeQualification",
        "RuntimeRecord",
        "RuntimeSource",
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
        "Transport",
        "TypedFixture",
        "TypedFixtureModelAnalysis",
        "Unknown",
        "VerdictStatus",
        "Verification",
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
        "profile",
        "qualification",
        "read_dependency_lock_provenance",
        "run_build",
        "runtimes",
        "verify_support_bundle",
        "write_support_bundle",
    ]
    for name in helia_profiler.__all__:
        assert hasattr(helia_profiler, name), f"__all__ exports missing attribute {name}"
