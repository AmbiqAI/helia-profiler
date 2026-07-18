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
