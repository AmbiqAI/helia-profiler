"""Compatibility baseline and qualification provenance contracts."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from helia_profiler.compatibility import (
    BASELINE_SCHEMA_VERSION,
    QualificationState,
    load_compatibility_baseline,
)
from helia_profiler.config import load_config
from helia_profiler.errors import ConfigError
from helia_profiler.report.metadata import _metadata_to_dict
from helia_profiler.results import RunMetadata


def _config(tmp_path: Path, **overrides: object):
    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    data: dict[str, object] = {"model": {"path": str(model)}}
    data.update(overrides)
    return load_config(None, data)


def test_default_baseline_has_exact_qualified_refs(tmp_path: Path) -> None:
    config = _config(tmp_path)
    compatibility = config.compatibility

    assert compatibility is not None
    assert compatibility.qualification is QualificationState.QUALIFIED
    baseline = compatibility.baseline
    assert baseline.schema_version == BASELINE_SCHEMA_VERSION
    assert baseline.neuralspotx_version == "0.7.17"
    assert (
        baseline.neuralspotx_sha256
        == "1289cd67eb27475159a4f9083338ee81648fcc115783db4f467ec96c9ca0fbdb"
    )
    assert baseline.project("neuralspotx").ref == "8b5a7fa99f044cfd4ba3c0668fb2419eceabb44f"
    assert baseline.project("nsx-ambiq-sdk").ref == "a9f4ec25a162f6f3700623feb691423bb5a51132"
    assert baseline.project("nsx-pmu-armv8m").ref == "5725c065a0c3603132f1064ee2684d1fa8587c88"
    assert baseline.project("nsx-tflite-micro").ref == "7afcf2b4170e039caf4c49f91e2c45d5869be333"
    assert baseline.project("arm-cmsis-nn").ref == "6d21a6f821fb72541173a6c4d05d83329fa74f7c"
    assert baseline.module("arm-cmsis-nn").ref == "6d21a6f821fb72541173a6c4d05d83329fa74f7c"
    assert baseline.project("ns-cmsis-nn").ref == "631726420b04860a5c4236956a3741ff5a96bd7f"
    assert baseline.project("nsx-executorch").ref == "0a0d5a1633f595b86dfd156f3c2859bebdf2a470"
    assert baseline.engine("executorch").ref == "0a0d5a1633f595b86dfd156f3c2859bebdf2a470"
    assert baseline.engine("helia-rt").ref == "c1b97f4a49ab608d226029d1bf1c9c2dac10ef62"
    assert baseline.engine("helia-aot").min_version == "0.18.0"
    assert baseline.engine("helia-aot").max_version_exclusive == "0.19.0"
    assert len(baseline.fingerprint) == 64


def test_baseline_has_no_unrelated_ref_drift() -> None:
    baseline = load_compatibility_baseline()

    assert {project.name: project.ref for project in baseline.projects} == {
        "neuralspotx": "8b5a7fa99f044cfd4ba3c0668fb2419eceabb44f",
        "nsx-ambiq-sdk": "a9f4ec25a162f6f3700623feb691423bb5a51132",
        "nsx-pmu-armv8m": "5725c065a0c3603132f1064ee2684d1fa8587c88",
        "nsx-tflite-micro": "7afcf2b4170e039caf4c49f91e2c45d5869be333",
        "arm-cmsis-nn": "6d21a6f821fb72541173a6c4d05d83329fa74f7c",
        "ns-cmsis-nn": "631726420b04860a5c4236956a3741ff5a96bd7f",
        "nsx-executorch": "0a0d5a1633f595b86dfd156f3c2859bebdf2a470",
        "helia-rt": "c1b97f4a49ab608d226029d1bf1c9c2dac10ef62",
        # nsx-sensors v0.3.0 — full datasheet audit of the INA228 driver.
        # Cumulative fixes that matter here: SHUNT_CAL scaling (v0.2.0),
        # ADCRANGE moved to its real register (CONFIG bit 4 — earlier code
        # wrote a VTCT bit, making range-1 calibrations 4x wrong), DEVICE_ID
        # rev-nibble masking, corrected DIAG_ALRT alert bit positions, and
        # the SHUNT_CAL write that silently left the register at zero on
        # Apollo510B (found here — see the hardware bring-up commit). Adds
        # the raw 40-bit accumulator reads this firmware uses. Pinned for
        # power.driver: ina228 (issue #95).
        "nsx-sensors": "c219a2bc98c62f96819fae20ab6c8911fcea3e25",
    }
    assert {module.name: module.ref for module in baseline.modules} == {
        "nsx-ambiq-bsp": "a9f4ec25a162f6f3700623feb691423bb5a51132",
        "nsx-pmu-armv8m": "5725c065a0c3603132f1064ee2684d1fa8587c88",
        "nsx-tflite-micro": "7afcf2b4170e039caf4c49f91e2c45d5869be333",
        "arm-cmsis-nn": "6d21a6f821fb72541173a6c4d05d83329fa74f7c",
        "nsx-cmsis-nn": "631726420b04860a5c4236956a3741ff5a96bd7f",
        "nsx-executorch": "0a0d5a1633f595b86dfd156f3c2859bebdf2a470",
        "nsx-helia-rt": "c1b97f4a49ab608d226029d1bf1c9c2dac10ef62",
        "nsx-sensors": "c219a2bc98c62f96819fae20ab6c8911fcea3e25",
    }
    assert baseline.engine("helia-rt").version == "1.16.0"
    assert baseline.engine("helia-aot").min_version == "0.18.0"
    assert baseline.engine("helia-aot").max_version_exclusive == "0.19.0"
    assert baseline.engine("tflm").governed_by_modules
    assert baseline.engine("executorch").version == "0.1.0"


def test_only_full_commit_refs_are_accepted(tmp_path: Path) -> None:
    baseline = load_compatibility_baseline().to_dict()
    baseline["projects"]["nsx-ambiq-sdk"]["ref"] = "a" * 40
    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(baseline))
    assert load_compatibility_baseline(valid).project("nsx-ambiq-sdk").ref == "a" * 40

    baseline["projects"]["nsx-ambiq-sdk"]["ref"] = "vendor-v1.2.3"
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="full 40-character commit SHA"):
        load_compatibility_baseline(invalid)


def test_package_dependency_matches_qualified_baseline() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    baseline = load_compatibility_baseline()
    expected_dependency = f"neuralspotx=={baseline.neuralspotx_version}"
    with (repo_root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    dependency = next(
        dependency for dependency in project["dependencies"] if dependency.startswith("neuralspotx")
    )
    assert dependency == expected_dependency

    with (repo_root / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)
    packages = lock["package"]
    project_package = next(
        package for package in packages if package["name"] == "helia-profiler"
    )
    locked_dependency = next(
        dependency
        for dependency in project_package["metadata"]["requires-dist"]
        if dependency["name"] == "neuralspotx"
    )
    assert locked_dependency["specifier"] == f"=={baseline.neuralspotx_version}"

    neuralspotx_package = next(
        package for package in packages if package["name"] == "neuralspotx"
    )
    assert neuralspotx_package["version"] == baseline.neuralspotx_version


def test_provenance_fingerprint_is_serializable_and_stable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.compatibility is not None
    provenance = config.compatibility.to_dict()
    assert provenance["baseline_fingerprint"] == config.compatibility.fingerprint
    assert provenance["baseline_fingerprint"] == load_compatibility_baseline().fingerprint
    assert json.loads(json.dumps(provenance)) == provenance


def test_engine_override_is_qualified_with_override(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        engine={"config": {"source": {"repo": "local/helia-rt", "ref": "feature/test"}}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED_WITH_ENGINE_OVERRIDE
    assert config.compatibility.engine_overrides == ("engine.config.source",)
    assert not config.compatibility.module_overrides


def test_engine_backend_selection_does_not_affect_qualification(tmp_path: Path) -> None:
    # Selecting the cmsis_nn TFLM backend still resolves exclusively to
    # baseline-qualified refs (arm-cmsis-nn is a required qualified
    # project), so it must not be classified as an override.
    config = _config(tmp_path, engine={"backend": "cmsis_nn"})

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED
    assert not config.compatibility.engine_overrides


def test_unrelated_engine_config_keys_do_not_affect_qualification(tmp_path: Path) -> None:
    # Ordinary build knobs (variant, linker_profile, ...) don't redirect an
    # engine's source/version, so they must not trigger an override state.
    config = _config(
        tmp_path,
        engine={"config": {"variant": "release-with-logs", "linker_profile": "sram"}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED
    assert not config.compatibility.engine_overrides


def test_module_override_is_development_override(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        build={"nsx_modules": {"nsx-core": {"ref": "feature/test"}}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.DEVELOPMENT_OVERRIDES
    assert config.compatibility.module_overrides == ("nsx-core",)


def test_engine_owned_module_override_is_not_a_development_override(
    tmp_path: Path,
) -> None:
    # nsx-helia-rt / nsx-cmsis-nn are resolved by their engine adapters (via
    # engine.config), not build.nsx_modules — firmware/__init__.py silently
    # ignores a build.nsx_modules entry targeting either name (and warns
    # pointing at engine.config instead). Qualification must reflect what
    # was actually applied, so this must not report development-overrides.
    config = _config(
        tmp_path,
        build={"nsx_modules": {"nsx-helia-rt": {"ref": "feature/test"}}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED
    assert not config.compatibility.module_overrides


def test_malformed_or_unsupported_baseline_fails_clearly(tmp_path: Path) -> None:
    malformed = tmp_path / "baseline.json"
    malformed.write_text(json.dumps({"schema": "wrong", "schema_version": 1}))
    with pytest.raises(ConfigError, match="Unsupported compatibility baseline schema"):
        load_compatibility_baseline(malformed)

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(
        json.dumps(
            {
                "schema": "hpx.compatibility-baseline",
                "schema_version": 99,
                "baseline_id": "test",
            }
        )
    )
    with pytest.raises(ConfigError, match="Unsupported compatibility baseline version"):
        load_compatibility_baseline(unsupported)

    scalar_entry = tmp_path / "scalar-entry.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["projects"]["nsx-ambiq-sdk"] = "v5.2.23"
    scalar_entry.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="project 'nsx-ambiq-sdk' must be an object"):
        load_compatibility_baseline(scalar_entry)

    branch_ref = tmp_path / "branch-ref.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["projects"]["nsx-ambiq-sdk"]["ref"] = "feature/customer"
    branch_ref.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="full 40-character commit SHA"):
        load_compatibility_baseline(branch_ref)

    unrecognized_branch_ref = tmp_path / "unrecognized-branch-ref.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["projects"]["nsx-ambiq-sdk"]["ref"] = "develop"
    unrecognized_branch_ref.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="full 40-character commit SHA"):
        load_compatibility_baseline(unrecognized_branch_ref)

    trailing_newline_ref = tmp_path / "trailing-newline-ref.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["projects"]["nsx-ambiq-sdk"]["ref"] = "v5.2.23\n"
    trailing_newline_ref.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="full 40-character commit SHA"):
        load_compatibility_baseline(trailing_newline_ref)

    inverted_range = tmp_path / "inverted-range.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-aot"] = {
        "min_version": "0.19.0",
        "max_version_exclusive": "0.18.0",
    }
    inverted_range.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="inverted version range"):
        load_compatibility_baseline(inverted_range)

    malformed_min_version = tmp_path / "malformed-min-version.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-aot"] = {"min_version": "not-semver"}
    malformed_min_version.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="major.minor.patch"):
        load_compatibility_baseline(malformed_min_version)

    malformed_max_version = tmp_path / "malformed-max-version.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-aot"] = {"max_version_exclusive": "not-semver"}
    malformed_max_version.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="major.minor.patch"):
        load_compatibility_baseline(malformed_max_version)

    no_policy_mode = tmp_path / "no-policy-mode.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-aot"] = {}
    no_policy_mode.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="needs a version"):
        load_compatibility_baseline(no_policy_mode)

    ambiguous_policy_mode = tmp_path / "ambiguous-policy-mode.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-aot"] = {"version": "0.18.0", "min_version": "0.18.0"}
    ambiguous_policy_mode.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="more than one policy mode"):
        load_compatibility_baseline(ambiguous_policy_mode)

    version_without_ref = tmp_path / "version-without-ref.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-rt"] = {"version": "1.16.0"}
    version_without_ref.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="sets a pinned version but no ref"):
        load_compatibility_baseline(version_without_ref)

    malformed_pinned_version = tmp_path / "malformed-pinned-version.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["helia-rt"] = {"version": "not-semver", "ref": "a" * 40}
    malformed_pinned_version.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="major.minor.patch"):
        load_compatibility_baseline(malformed_pinned_version)

    ambiguous_governed_and_range = tmp_path / "ambiguous-governed-and-range.json"
    baseline = load_compatibility_baseline().to_dict()
    baseline["engines"]["tflm"] = {
        "governed_by_modules": True,
        "min_version": "0.1.0",
    }
    ambiguous_governed_and_range.write_text(json.dumps(baseline))
    with pytest.raises(ConfigError, match="more than one policy mode"):
        load_compatibility_baseline(ambiguous_governed_and_range)


def test_result_metadata_serializes_qualification_provenance(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.compatibility is not None
    metadata = _metadata_to_dict(RunMetadata(compatibility=config.compatibility))

    compatibility = metadata["compatibility"]
    assert compatibility["qualification"] == "qualified"
    assert (
        compatibility["baseline"]["projects"]["nsx-tflite-micro"]["ref"]
        == "7afcf2b4170e039caf4c49f91e2c45d5869be333"
    )
    json.dumps(metadata)


def test_helia_aot_version_check_uses_baseline_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.errors import EngineError

    config = _config(tmp_path)

    def _fake_version(name: str) -> str:
        assert name == "helia-aot"
        return "0.18.4"

    monkeypatch.setattr("importlib.metadata.version", _fake_version)
    # Within the baseline-qualified range [0.18.0, 0.19.0) -> no error.
    assert aot_compile._check_helia_aot_version(config) == "0.18.4"

    def _fake_version_too_old(name: str) -> str:
        return "0.17.9"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_old)
    with pytest.raises(EngineError, match=r"below the minimum supported version \(v0\.18\.0\)"):
        aot_compile._check_helia_aot_version(config)

    def _fake_version_too_new(name: str) -> str:
        return "0.19.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_new)
    with pytest.raises(EngineError, match=r"outside the qualified policy"):
        aot_compile._check_helia_aot_version(config)

    # Without a config (or without a resolved compatibility), the local
    # HELIAAOT_MIN_VERSION / HELIAAOT_MAX_VERSION_EXCLUSIVE constants remain
    # the fallback policy.
    monkeypatch.setattr("importlib.metadata.version", _fake_version)
    assert aot_compile._check_helia_aot_version(None) == "0.18.4"


def test_helia_aot_unparseable_version_warns_full_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # An unparseable installed version skips the *entire* qualified-range
    # check (both min and max), not just the floor — the warning must say
    # so and mention both bounds, not just the minimum.
    import logging

    from helia_profiler.engines.helia_aot import compile as aot_compile

    config = _config(tmp_path)

    def _fake_version(name: str) -> str:
        return "not-a-version"

    monkeypatch.setattr("importlib.metadata.version", _fake_version)
    with caplog.at_level(logging.WARNING):
        result = aot_compile._check_helia_aot_version(config)

    assert result == "not-a-version"
    messages = [rec.message for rec in caplog.records]
    assert any("0.18.0" in message and "0.19.0" in message for message in messages)
    assert not any("floor" in message for message in messages)


def test_helia_aot_success_debug_log_only_after_max_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # The "Using helia-aot vX" debug log must only fire once the version has
    # cleared *both* the min and max bound — logging success before the max
    # check would be misleading if that check then raises.
    import logging

    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.errors import EngineError

    config = _config(tmp_path)

    def _fake_version_too_new(name: str) -> str:
        return "0.19.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_new)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(EngineError, match=r"outside the qualified policy"):
            aot_compile._check_helia_aot_version(config)

    assert not any("Using helia-aot" in rec.message for rec in caplog.records)

    def _fake_version_ok(name: str) -> str:
        return "0.18.4"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_ok)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        aot_compile._check_helia_aot_version(config)

    assert any("Using helia-aot" in rec.message for rec in caplog.records)


def test_helia_aot_single_sided_baseline_range_is_not_backfilled_from_constants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # A baseline may legally set only one side of the helia-aot range (see
    # _parse_baseline()'s per-bound validation). The unset side must be
    # treated as unbounded, not silently backfilled from the unrelated
    # HELIAAOT_MIN_VERSION / HELIAAOT_MAX_VERSION_EXCLUSIVE constants — a
    # baseline min_version above the local max constant (or vice versa)
    # would otherwise reject every installed version.
    import logging
    from dataclasses import replace
    from types import SimpleNamespace

    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.errors import EngineError

    config = _config(tmp_path)
    assert config.compatibility is not None
    baseline = config.compatibility.baseline
    aot_engine = baseline.engine("helia-aot")

    # _check_helia_aot_version() only reads config.compatibility.baseline, so
    # a lightweight stand-in avoids ProfileConfig's init=False `compatibility`
    # field (which dataclasses.replace() cannot target directly).
    def _config_with_baseline(new_baseline: object) -> object:
        return SimpleNamespace(compatibility=SimpleNamespace(baseline=new_baseline))

    # min_version only, well above HELIAAOT_MAX_VERSION_EXCLUSIVE (0.19.0) —
    # an installed version above that local constant must still pass, since
    # the baseline leaves the ceiling unbounded.
    min_only_engine = replace(aot_engine, min_version="0.20.0", max_version_exclusive=None)
    min_only_engines = tuple(
        min_only_engine if engine.name == "helia-aot" else engine for engine in baseline.engines
    )
    min_only_baseline = replace(baseline, engines=min_only_engines)
    min_only_config = _config_with_baseline(min_only_baseline)

    def _fake_version_high(name: str) -> str:
        return "5.0.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_high)
    with caplog.at_level(logging.DEBUG):
        assert aot_compile._check_helia_aot_version(min_only_config) == "5.0.0"
    # The unbounded ceiling must render as "unbounded", never as the
    # malformed "<vunbounded" a hard-coded "v" prefix would produce.
    messages = [rec.message for rec in caplog.records]
    assert any("<unbounded" in message for message in messages)
    assert not any("vunbounded" in message for message in messages)

    def _fake_version_below_baseline_min(name: str) -> str:
        return "0.19.5"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_below_baseline_min)
    with pytest.raises(EngineError, match=r"below the minimum supported version \(v0\.20\.0\)"):
        aot_compile._check_helia_aot_version(min_only_config)

    # max_version_exclusive only, well below HELIAAOT_MIN_VERSION (0.18.0) —
    # an installed version below that local constant must still pass, since
    # the baseline leaves the floor unbounded.
    max_only_engine = replace(aot_engine, min_version=None, max_version_exclusive="0.5.0")
    max_only_engines = tuple(
        max_only_engine if engine.name == "helia-aot" else engine for engine in baseline.engines
    )
    max_only_baseline = replace(baseline, engines=max_only_engines)
    max_only_config = _config_with_baseline(max_only_baseline)

    def _fake_version_low(name: str) -> str:
        return "0.1.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_low)
    assert aot_compile._check_helia_aot_version(max_only_config) == "0.1.0"

    def _fake_version_above_baseline_max(name: str) -> str:
        return "0.5.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_above_baseline_max)
    with pytest.raises(EngineError, match=r"outside the qualified policy"):
        aot_compile._check_helia_aot_version(max_only_config)

    # An unparseable installed version with a single-sided baseline must also
    # render the unbounded side cleanly in the skip-check warning.
    def _fake_version_unparseable(name: str) -> str:
        return "not-a-version"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_unparseable)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        aot_compile._check_helia_aot_version(min_only_config)
    messages = [rec.message for rec in caplog.records]
    assert any("<unbounded" in message for message in messages)
    assert not any("vunbounded" in message for message in messages)


def test_baseline_helia_rt_entry_matches_canonical_artifacts_constants() -> None:
    # engines/helia_rt/artifacts.py is the single canonical source for the
    # default heliaRT version/ref (see AGENTS.md: "bump HELIART_VERSION when
    # adopting a new release"). The baseline only mirrors these values for
    # reporting/classification; it must never drift from them, and runtime
    # resolution must keep consulting the constants directly, not the
    # baseline (see engines/helia_rt/adapter.py and artifacts.py).
    from helia_profiler.engines.helia_rt.artifacts import (
        HELIART_SOURCE_COMMIT,
        HELIART_VERSION,
    )

    engine = load_compatibility_baseline().engine("helia-rt")
    assert engine.version == HELIART_VERSION
    assert engine.ref == HELIART_SOURCE_COMMIT


def test_engine_owned_module_names_match_canonical_constants() -> None:
    # ENGINE_OWNED_MODULE_NAMES is a literal mirror (see compatibility.py's
    # comment) of HELIART_MODULE / CMSIS_NN_MODULE, shared by
    # resolve_compatibility() (qualification classification) and
    # firmware/__init__.py (the "use engine.config instead" warning). Guard
    # against the literals drifting from the canonical constants.
    from helia_profiler.compatibility import ENGINE_OWNED_MODULE_NAMES
    from helia_profiler.engines.helia_aot.cmsis_nn import CMSIS_NN_MODULE
    from helia_profiler.engines.executorch import EXECUTORCH_MODULE
    from helia_profiler.engines.helia_rt.artifacts import HELIART_MODULE

    assert ENGINE_OWNED_MODULE_NAMES == {
        HELIART_MODULE,
        CMSIS_NN_MODULE,
        EXECUTORCH_MODULE,
    }
