from __future__ import annotations

import inspect

import helia_profiler


def test_every_package_export_has_one_stability_tier() -> None:
    assert set(helia_profiler.__api_stability__) == set(helia_profiler.__all__)
    assert set(helia_profiler.__api_stability__.values()) == {
        "stable",
        "experimental",
        "implementation",
    }


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
        "ConfigError",
        "DependencyError",
        "DependencyLockProvenance",
        "DeterministicCaptureError",
        "DoctorCheck",
        "DoctorResult",
        "DoctorVersionCheck",
        "EngineConfig",
        "EngineError",
        "EngineType",
        "FirmwareError",
        "FirmwareMeta",
        "HeartbeatConfig",
        "HpxError",
        "JLinkProbe",
        "JLinkProbeMatch",
        "LayerResult",
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
        "PresetResult",
        "ProfileConfig",
        "ProfileResult",
        "ProfilingConfig",
        "ProgressUpdate",
        "QualificationState",
        "ReportError",
        "ResetStrategy",
        "ResultArtifact",
        "ResultIssue",
        "ResultManifest",
        "ResultValidity",
        "RunEvaluation",
        "RunMetadata",
        "RunStatus",
        "SerialPortInfo",
        "Session",
        "SocDef",
        "SupportBundleManifest",
        "SupportBundleOptions",
        "SupportBundleSection",
        "TargetConfig",
        "TimeoutsConfig",
        "Toolchain",
        "Transport",
        "VerdictStatus",
        "VersionError",
        "__version__",
        "assess_comparability",
        "build_platform_registry",
        "collect_support_bundle",
        "evaluate_comparison_profile",
        "evaluate_run",
        "examples",
        "get_soc",
        "load_compatibility_baseline",
        "load_result_manifest",
        "profile",
        "read_dependency_lock_provenance",
        "verify_support_bundle",
        "write_support_bundle",
    ]
    for name in helia_profiler.__all__:
        assert hasattr(helia_profiler, name), f"__all__ exports missing attribute {name}"
