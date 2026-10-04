"""Compatibility baseline and qualification provenance contracts."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from helia_profiler.config import load_config
from helia_profiler.deps.compatibility import (
    BASELINE_SCHEMA_VERSION,
    QualificationState,
    load_compatibility_baseline,
)
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
    assert baseline.neuralspotx_version == "0.8.1"
    assert (
        baseline.neuralspotx_sha256
        == "7aac6f1b2e89ebf41dfe087c7588e0db11d65e8a11cac2cda03f6d7c510a9094"
    )
    assert baseline.project("neuralspotx").ref == "2dbe12a2799fd8c3df85f1a103b0adca340c901f"
    assert baseline.project("nsx-ambiq-sdk").ref == "aefce2ca858795e783c76726ebe7d14d9d4bde7c"
    assert baseline.project("nsx-pmu-armv8m").ref == "5725c065a0c3603132f1064ee2684d1fa8587c88"
    assert baseline.project("nsx-tflite-micro").ref == "7afcf2b4170e039caf4c49f91e2c45d5869be333"
    assert baseline.project("arm-cmsis-nn").ref == "6d21a6f821fb72541173a6c4d05d83329fa74f7c"
    assert baseline.module("arm-cmsis-nn").ref == "6d21a6f821fb72541173a6c4d05d83329fa74f7c"
    assert baseline.project("ns-cmsis-nn").ref == "5f3fed9f21a57390cc7f00f77a37db8f5f110cb8"
    assert baseline.project("nsx-executorch").ref == "5514ac1ea8439b3fe615d180bf68c75a9dabb48e"
    assert len(baseline.fingerprint) == 64


def test_baseline_has_no_unrelated_ref_drift() -> None:
    baseline = load_compatibility_baseline()

    assert {project.name: project.ref for project in baseline.projects} == {
        "neuralspotx": "2dbe12a2799fd8c3df85f1a103b0adca340c901f",
        "nsx-ambiq-sdk": "aefce2ca858795e783c76726ebe7d14d9d4bde7c",
        # Vendored Arm Ethos-U core driver pulled transitively by nsx-npu;
        # the baseline records the resolved commit, not the registry's
        # mutable tag, so _verify_baseline_resolution covers the NPU
        # sampling path.
        "nsx-ethos-u-driver": "f0f99bb124b22486ef55694c76567008680cb5a8",
        "nsx-pmu-armv8m": "5725c065a0c3603132f1064ee2684d1fa8587c88",
        "nsx-tflite-micro": "7afcf2b4170e039caf4c49f91e2c45d5869be333",
        "arm-cmsis-nn": "6d21a6f821fb72541173a6c4d05d83329fa74f7c",
        "ns-cmsis-nn": "5f3fed9f21a57390cc7f00f77a37db8f5f110cb8",
        "nsx-executorch": "5514ac1ea8439b3fe615d180bf68c75a9dabb48e",
        "helia-rt": "dc8533abe0ec7e01c251a067ce54c60f54237f5f",
        # nsx-sensors: INA228 driver pinned for the shunt-cal register
        # fixes and raw 40-bit accumulator reads power.driver: ina228
        # needs (issue #95).
        "nsx-sensors": "c219a2bc98c62f96819fae20ab6c8911fcea3e25",
    }
    assert {module.name: module.ref for module in baseline.modules} == {
        "nsx-ambiq-bsp": "aefce2ca858795e783c76726ebe7d14d9d4bde7c",
        "nsx-npu": "aefce2ca858795e783c76726ebe7d14d9d4bde7c",
        "nsx-pmu-armv8m": "5725c065a0c3603132f1064ee2684d1fa8587c88",
        "nsx-tflite-micro": "7afcf2b4170e039caf4c49f91e2c45d5869be333",
        "arm-cmsis-nn": "6d21a6f821fb72541173a6c4d05d83329fa74f7c",
        "nsx-cmsis-nn": "5f3fed9f21a57390cc7f00f77a37db8f5f110cb8",
        "nsx-executorch": "5514ac1ea8439b3fe615d180bf68c75a9dabb48e",
        "nsx-helia-rt": "dc8533abe0ec7e01c251a067ce54c60f54237f5f",
        "nsx-sensors": "c219a2bc98c62f96819fae20ab6c8911fcea3e25",
    }


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
    project_package = next(package for package in packages if package["name"] == "helia-profiler")
    locked_dependency = next(
        dependency
        for dependency in project_package["metadata"]["requires-dist"]
        if dependency["name"] == "neuralspotx"
    )
    assert locked_dependency["specifier"] == f"=={baseline.neuralspotx_version}"

    neuralspotx_package = next(package for package in packages if package["name"] == "neuralspotx")
    assert neuralspotx_package["version"] == baseline.neuralspotx_version


def test_aot_extra_and_lock_match_the_qualified_helia_aot_range() -> None:
    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.engines.semver import parse_semver

    repo_root = Path(__file__).resolve().parent.parent
    # The range runs from the oldest helia-aot runtime record to the minor after the newest.
    minimum = aot_compile.HELIAAOT_MIN_VERSION
    maximum = aot_compile.HELIAAOT_MAX_VERSION_EXCLUSIVE
    assert (minimum, maximum) == ("0.23.0", "0.26.0")
    specifier = f">={minimum},<{maximum}"

    with (repo_root / "pyproject.toml").open("rb") as stream:
        extra = tomllib.load(stream)["project"]["optional-dependencies"]["aot"]
    assert f"helia-aot{specifier}" in extra

    with (repo_root / "uv.lock").open("rb") as stream:
        packages = tomllib.load(stream)["package"]
    project_package = next(package for package in packages if package["name"] == "helia-profiler")
    locked = next(
        dependency
        for dependency in project_package["metadata"]["requires-dist"]
        if dependency["name"] == "helia-aot"
    )
    assert locked["specifier"] == specifier
    version = next(package for package in packages if package["name"] == "helia-aot")["version"]
    assert parse_semver(minimum) <= parse_semver(version) < parse_semver(maximum)


def test_helia_rt_and_core_pins_agree_across_the_baseline() -> None:
    # heliaRT, heliaAOT and the shared ns-cmsis-nn core are qualified as one
    # set: every place the baseline names heliaRT or the core carries one ref,
    # and each engine-owned project carries its default runtime record's commit.
    from helia_profiler.engines.helia_rt.artifacts import HELIART_SOURCE_COMMIT, HELIART_VERSION
    from helia_profiler.runtime_records import runtime

    baseline = load_compatibility_baseline()
    helia_rt = runtime("helia-rt")
    assert helia_rt is not None
    assert (helia_rt.version, helia_rt.source.commit) == (HELIART_VERSION, HELIART_SOURCE_COMMIT)
    assert baseline.project("helia-rt").ref == HELIART_SOURCE_COMMIT
    assert helia_rt.kernels is not None
    assert helia_rt.kernels.commit == baseline.project("ns-cmsis-nn").ref
    executorch = runtime("executorch")
    assert executorch is not None
    assert baseline.project("nsx-executorch").ref == executorch.source.commit
    assert baseline.module("nsx-executorch").ref == executorch.source.commit
    assert baseline.module("nsx-helia-rt").ref == HELIART_SOURCE_COMMIT
    core = baseline.project("ns-cmsis-nn").ref
    assert baseline.module("nsx-cmsis-nn").ref == core


def test_provenance_fingerprint_is_serializable_and_stable(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.compatibility is not None
    provenance = config.compatibility.to_dict()
    assert provenance["baseline_fingerprint"] == config.compatibility.fingerprint
    assert provenance["baseline_fingerprint"] == load_compatibility_baseline().fingerprint
    assert json.loads(json.dumps(provenance)) == provenance


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("source", {"repo": "local/helia-rt", "ref": "feature/test"}),
        ("source_path", "local/helia-rt"),
        ("dist_path", "local/helia-rt-dist"),
    ],
)
def test_engine_override_is_qualified_with_override(
    tmp_path: Path, key: str, value: object
) -> None:
    config = _config(
        tmp_path,
        engine={"config": {key: value}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED_WITH_ENGINE_OVERRIDE
    assert config.compatibility.engine_overrides == (f"engine.config.{key}",)
    assert not config.compatibility.module_overrides


@pytest.mark.parametrize("selector", ["cmsis_nn_ref", "cmsis_nn_path", "CMSIS_NN_PATH"])
@pytest.mark.parametrize("with_engine_override", [False, True])
def test_cmsis_nn_override_has_same_stamp_as_applied_nsx_module_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector: str,
    with_engine_override: bool,
) -> None:
    module_config = _config(
        tmp_path,
        build={"nsx_modules": {"nsx-tflite-micro": {"ref": "feature/test"}}},
    )
    engine_config = {"source_path": "local/helia-rt"} if with_engine_override else {}
    if selector == "CMSIS_NN_PATH":
        monkeypatch.setenv(selector, str(tmp_path / "ns-cmsis-nn"))
    else:
        engine_config[selector] = (
            "feature/test" if selector == "cmsis_nn_ref" else str(tmp_path / "ns-cmsis-nn")
        )
    config = _config(tmp_path, engine={"config": engine_config})

    assert config.compatibility is not None
    assert module_config.compatibility is not None
    assert config.compatibility.qualification is module_config.compatibility.qualification
    assert config.compatibility.qualification is QualificationState.DEVELOPMENT_OVERRIDES
    assert config.compatibility.module_overrides == ("nsx-cmsis-nn",)
    assert config.compatibility.engine_overrides == (
        ("engine.config.source_path",) if with_engine_override else ()
    )
    provenance = config.compatibility.to_dict()
    assert provenance["qualification"] == "development-overrides"
    assert provenance["module_overrides"] == ["nsx-cmsis-nn"]


@pytest.mark.parametrize(
    ("engine_type", "backend", "module"),
    [
        ("helia-rt", None, "nsx-cmsis-nn"),
        ("helia-aot", None, "nsx-cmsis-nn"),
        ("executorch", "ns", "nsx-cmsis-nn"),
        ("executorch", "arm", "arm-cmsis-nn"),
        # ExecuTorch defaults to the arm provider when no backend is set.
        ("executorch", None, "arm-cmsis-nn"),
    ],
)
def test_cmsis_nn_selector_names_the_provider_module_it_replaces(
    tmp_path: Path, engine_type: str, backend: str | None, module: str
) -> None:
    model = tmp_path / ("model.pte" if engine_type == "executorch" else "model.tflite")
    model.write_bytes(b"\x00")
    config = _config(
        tmp_path,
        model={"path": str(model)},
        engine={"type": engine_type, "backend": backend, "config": {"cmsis_nn_ref": "v9.9.9"}},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.DEVELOPMENT_OVERRIDES
    assert config.compatibility.module_overrides == (module,)


def test_cmsis_nn_env_fallback_does_not_reach_executorch_arm_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # engines/executorch._provider_module_ref only reads engine.config for the
    # arm provider, so the environment variable changes nothing there.
    monkeypatch.setenv("CMSIS_NN_PATH", str(tmp_path / "ns-cmsis-nn"))
    model = tmp_path / "model.pte"
    model.write_bytes(b"\x00")
    config = _config(
        tmp_path,
        model={"path": str(model)},
        engine={"type": "executorch", "backend": "arm"},
    )

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED
    assert not config.compatibility.module_overrides


@pytest.mark.parametrize(
    "engine_config",
    [
        {"cmsis_nn_path": ""},
        {"cmsis_nn_ref": ""},
        {"cmsis_nn_path": None},
        {"dist_path": ""},
        {"source_path": ""},
        {"source": {}},
    ],
)
def test_empty_source_override_values_do_not_change_qualification(
    tmp_path: Path, engine_config: dict[str, object]
) -> None:
    # Every adapter gates on truthiness and falls back to the baseline for an
    # empty value, so the stamp must not claim an override that never applied.
    config = _config(tmp_path, engine={"config": engine_config})

    assert config.compatibility is not None
    assert config.compatibility.qualification is QualificationState.QUALIFIED
    assert not config.compatibility.module_overrides
    assert not config.compatibility.engine_overrides


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


@pytest.mark.parametrize("module", ["nsx-helia-rt", "nsx-cmsis-nn", "nsx-executorch"])
def test_engine_owned_module_override_is_not_a_development_override(
    tmp_path: Path,
    module: str,
) -> None:
    # nsx-helia-rt / nsx-cmsis-nn are resolved by their engine adapters (via
    # engine.config), not build.nsx_modules — firmware/__init__.py silently
    # ignores a build.nsx_modules entry targeting either name (and warns
    # pointing at engine.config instead). Qualification must reflect what
    # was actually applied, so this must not report development-overrides.
    config = _config(
        tmp_path,
        build={"nsx_modules": {module: {"ref": "feature/test"}}},
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


def test_helia_aot_version_check_uses_the_recorded_range(monkeypatch: pytest.MonkeyPatch) -> None:
    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.errors import EngineError

    def _fake_version(name: str) -> str:
        assert name == "helia-aot"
        return "0.23.4"

    monkeypatch.setattr("importlib.metadata.version", _fake_version)
    # Within the recorded range [0.23.0, 0.26.0) -> no error.
    assert aot_compile._check_helia_aot_version() == "0.23.4"

    def _fake_version_too_old(name: str) -> str:
        return "0.22.9"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_old)
    with pytest.raises(
        EngineError, match=r"below the minimum supported version \(v0\.23\.0\)"
    ) as excinfo:
        aot_compile._check_helia_aot_version()
    # The upgrade command stays inside the recorded range.
    assert "'helia-aot>=0.23.0,<0.26.0'" in (excinfo.value.hint or "")

    def _fake_version_too_new(name: str) -> str:
        return "0.26.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_new)
    with pytest.raises(EngineError, match=r"outside the recorded range"):
        aot_compile._check_helia_aot_version()


def test_helia_aot_unparseable_version_warns_full_range(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # An unparseable installed version skips the *entire* qualified-range
    # check (both min and max), not just the floor — the warning must say
    # so and mention both bounds, not just the minimum.
    import logging

    from helia_profiler.engines.helia_aot import compile as aot_compile

    def _fake_version(name: str) -> str:
        return "not-a-version"

    monkeypatch.setattr("importlib.metadata.version", _fake_version)
    with caplog.at_level(logging.WARNING):
        result = aot_compile._check_helia_aot_version()

    assert result == "not-a-version"
    messages = [rec.message for rec in caplog.records]
    assert any("0.23.0" in message and "0.26.0" in message for message in messages)
    assert not any("floor" in message for message in messages)


def test_helia_aot_success_debug_log_only_after_max_check(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    # The "Using helia-aot vX" debug log must only fire once the version has
    # cleared *both* the min and max bound — logging success before the max
    # check would be misleading if that check then raises.
    import logging

    from helia_profiler.engines.helia_aot import compile as aot_compile
    from helia_profiler.errors import EngineError

    def _fake_version_too_new(name: str) -> str:
        return "0.26.0"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_too_new)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(EngineError, match=r"outside the recorded range"):
            aot_compile._check_helia_aot_version()

    assert not any("Using helia-aot" in rec.message for rec in caplog.records)

    def _fake_version_ok(name: str) -> str:
        return "0.23.4"

    monkeypatch.setattr("importlib.metadata.version", _fake_version_ok)
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        aot_compile._check_helia_aot_version()

    assert any("Using helia-aot" in rec.message for rec in caplog.records)


def test_engine_owned_module_names_match_canonical_constants() -> None:
    # ENGINE_OWNED_MODULE_NAMES is a literal mirror (see compatibility.py's
    # comment) of HELIART_MODULE / CMSIS_NN_MODULE, shared by
    # resolve_compatibility() (qualification classification) and
    # firmware/__init__.py (the "use engine.config instead" warning). Guard
    # against the literals drifting from the canonical constants.
    from helia_profiler.deps.compatibility import ENGINE_OWNED_MODULE_NAMES
    from helia_profiler.engines.cmsis_nn import CMSIS_NN_MODULE
    from helia_profiler.engines.executorch import EXECUTORCH_MODULE
    from helia_profiler.engines.helia_rt.artifacts import HELIART_MODULE

    assert ENGINE_OWNED_MODULE_NAMES == {
        HELIART_MODULE,
        CMSIS_NN_MODULE,
        EXECUTORCH_MODULE,
    }


def test_heliart_nsx_fixture_version_tracks_the_qualified_release():
    """#192 NIT: the heliart_nsx fixture's module.version is the one
    field claimed to track the qualified release — enforce it so the next
    promotion cannot silently leave it stale."""
    import yaml

    from helia_profiler.engines.helia_rt.artifacts import HELIART_VERSION

    fixture = Path(__file__).parent / "fixtures" / "heliart_nsx" / "nsx-module.yaml"
    data = yaml.safe_load(fixture.read_text(encoding="utf-8"))
    assert data["module"]["version"] == HELIART_VERSION
