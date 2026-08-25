from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from helia_profiler.evaluation import ComparabilitySeverity, assess_comparability
from helia_profiler.evaluation import RunArtifacts
from helia_profiler.results.issues import ComparabilityCode, ComparisonDimension, DIMENSION_DIFFERS, POWER_DIMENSION_MISMATCH


def _run(
    *,
    model: str = "abc",
    engine: str = "helia-rt",
    engine_version: str | None = "1.17.0",
    compiler_version: str = "12.2.1",
    system_clock_hz: int = 250_000_000,
    ops=("CONV_2D",),
    power: dict | None = None,
):
    summary: dict = {"schema_version": 1, "total_cycles": 100}
    if power is not None:
        summary["power"] = power
    # Matches report/metadata.py: a None version is FILTERED OUT of the JSON
    # (tflm/executorch record no resolved version), so absence is the real
    # legacy/no-version shape, not a null.
    engine_block: dict = {"type": engine}
    if engine_version is not None:
        engine_block["version"] = engine_version
    return RunArtifacts(
        path=Path("results"),
        summary=summary,
        metadata={
            "schema_version": 1,
            "hpx_version": "0.1.0",
            "engine": engine_block,
            "model": {"sha256": model},
            "toolchain": {"compiler_version": compiler_version},
            "firmware": {"system_clock_hz": system_clock_hz},
            "platform": {"soc": "apollo510", "cpu_clock_name": "hp"},
            "config": {
                "engine": {"type": engine},
                "target": {
                    "board": "apollo510_evb",
                    "toolchain": "arm-none-eabi-gcc",
                    "transport": "rtt",
                },
                "model": {"arena_location": "tcm", "weights_location": "mram"},
            },
        },
        layers=[{"id": index, "op": op, "cycles": 10} for index, op in enumerate(ops)],
    )


def test_engine_difference_is_informative():
    assessment = assess_comparability(_run(), _run(engine="helia-aot"))

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    issue = next(issue for issue in assessment.issues if issue.code == DIMENSION_DIFFERS.code_for(ComparisonDimension.ENGINE))
    assert issue.severity is ComparabilitySeverity.INFORMATIVE


def test_engine_version_difference_is_informative():
    """#193: a runtime promotion (heliaRT 1.16 -> 1.17, the #191 A/B) must
    surface -- previously the one axis that changed was invisible."""
    assessment = assess_comparability(
        _run(engine_version="1.16.0"), _run(engine_version="1.17.0")
    )

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == DIMENSION_DIFFERS.code_for(ComparisonDimension.ENGINE_VERSION)
    )
    assert issue.severity is ComparabilitySeverity.INFORMATIVE
    assert issue.context["baseline"] == "1.16.0"
    assert issue.context["candidate"] == "1.17.0"


def test_a_baseline_predating_engine_version_is_skipped_not_flagged():
    """Old artifacts (and tflm/executorch runs, which record no resolved
    version) omit the key entirely -- missing is unknown, not different."""
    assessment = assess_comparability(
        _run(engine_version=None), _run(engine_version="1.17.0")
    )

    assert assessment.run_metrics_comparable
    assert not any(
        issue.code == DIMENSION_DIFFERS.code_for(ComparisonDimension.ENGINE_VERSION)
        for issue in assessment.issues
    )


def test_model_mismatch_blocks_all_deltas():
    assessment = assess_comparability(_run(model="abc"), _run(model="def"))

    assert not assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert assessment.issues[0].code == ComparabilityCode.IDENTITY_MODEL_MISMATCH


def test_topology_mismatch_blocks_only_layer_deltas():
    assessment = assess_comparability(_run(), _run(ops=("CONV_2D", "SOFTMAX")))

    assert assessment.run_metrics_comparable
    assert not assessment.layers_comparable
    assert any(issue.code == ComparabilityCode.TOPOLOGY_LAYER_COUNT_MISMATCH for issue in assessment.issues)


def test_cross_machine_provenance_differences_are_structured():
    assessment = assess_comparability(
        _run(compiler_version="12.2.1", system_clock_hz=250_000_000),
        _run(compiler_version="14.3.1", system_clock_hz=96_000_000),
    )

    assert assessment.run_metrics_comparable
    assert {issue.code for issue in assessment.issues} >= {
        DIMENSION_DIFFERS.code_for(ComparisonDimension.COMPILER_VERSION),
        DIMENSION_DIFFERS.code_for(ComparisonDimension.SYSTEM_CLOCK_HZ),
    }


def test_cross_instrument_power_scopes_omit_power_metrics_only():
    """Joulescope (host-gated window) vs INA228 (on-device accumulators) are
    different-instrument measurements: power deltas are omitted with an
    explanatory issue, while run/layer performance deltas stay comparable."""
    joulescope = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    ina228 = _run(
        power={"measurement_scope": "on_device_gated_inference", "integrity": "valid"}
    )

    assessment = assess_comparability(joulescope, ina228)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_SCOPE)
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] == "gpio_gated_clean_window"
    assert issue.context["candidate"] == "on_device_gated_inference"


def test_monitor_presence_mismatch_omits_power_metrics_only():
    """An on-target monitor keeps its IOM powered on the measured rail, so a
    block-present run draws measurably more than a block-absent one even when
    the instrument, mode, and firmware all match. Comparing the two as equals
    would report a phantom power regression."""
    without_monitor = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    with_monitor = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "on_device_summary": {"source": "ina228", "energy_nj": 0},
        }
    )

    assessment = assess_comparability(without_monitor, with_monitor)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_MONITOR)
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] == "none"
    assert issue.context["candidate"] == "ina228"


def test_lockstep_mismatch_omits_power_metrics_only():
    """#114 flips the lock-step default, so runs recorded either side of it
    differ in a baked firmware constant. Lock-step drives the state pin as an
    output and enables the GO pin's input buffer on the measured rail, and the
    host holds GO high into that input until gate rise -- the same class of
    real, rail-level difference that makes monitor-presence power-blocking.

    Adversarial review found both runs comparing clean with integrity: valid,
    which is #115's phantom-delta failure mode: only the runs that LOST the
    gate race are marked degraded, so the ones that won compare silently
    against post-change runs."""
    free_running = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": False},
        }
    )
    lockstepped = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(free_running, lockstepped)

    assert assessment.run_metrics_comparable
    assert assessment.layers_comparable
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_LOCKSTEP)
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] is False
    assert issue.context["candidate"] is True


def test_bundles_with_no_sync_record_at_all_are_skipped():
    """Only bundles carrying no sync record skip the dimension.

    An earlier version of this test claimed pre-#114 runs "have no
    sync.lockstep key at all". Adversarial review showed that is false:
    capture writes ``SyncHandshakeMetadata(lockstep=...)`` on BOTH branches, so
    a real pre-#114 gated external baseline carries ``sync.lockstep: False``
    and IS compared -- correctly, since it genuinely ran with the rail in the
    other state (see the test below). What actually skips is a bundle with no
    sync record at all: internal-mode runs, free-form captures, and anything
    predating the field."""
    legacy = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"}
    )
    current = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(legacy, current)

    assert assessment.power_metrics_comparable
    assert not any(
        issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_LOCKSTEP)
        for issue in assessment.issues
    )


def test_real_pre_change_baselines_do_block_power_comparison():
    """The behaviour change #114 actually ships, pinned deliberately.

    A pre-#114 gated external run recorded ``sync.lockstep: False`` -- that WAS
    the bug: the target free-ran its measured window. Post-#114 the same config
    runs lock-stepped. The rail genuinely differs, so blocking is correct. It
    is pinned here because the consequence is easy to miss: a power-gated
    comparison against a stored baseline flips from pass to fail, since
    ``MissingMetricPolicy.FAIL`` is the default. Documented in
    docs/guide/power.md."""
    pre_change = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": False},
        }
    )
    post_change = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(pre_change, post_change)

    assert not assessment.power_metrics_comparable
    assert assessment.run_metrics_comparable


def test_a_non_dict_sync_record_does_not_crash_comparison():
    """``report/summary.py`` copies power metadata's ``sync`` through on an
    is-not-None check alone, so it reaches disk as whatever was stored -- the
    repo's own report golden fixture holds the bool ``True``. An unguarded
    dereference raised ``AttributeError``, which is not an ``HpxError``: the
    CLI printed a traceback and ``validation/compare.py`` aborted an entire
    multi-case run instead of recording one ``COMPARE_ERROR``."""
    weird = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": True,
        }
    )
    normal = _run(
        power={
            "measurement_scope": "gpio_gated_clean_window",
            "integrity": "valid",
            "sync": {"lockstep": True},
        }
    )

    assessment = assess_comparability(weird, normal)

    assert assessment.power_metrics_comparable


def test_matching_monitor_presence_stays_power_comparable():
    with_monitor = {
        "measurement_scope": "gpio_gated_clean_window",
        "integrity": "valid",
        "on_device_summary": {"source": "ina228", "energy_nj": 0},
    }
    assessment = assess_comparability(_run(power=with_monitor), _run(power=with_monitor))
    assert assessment.power_metrics_comparable


def test_matching_on_device_power_scopes_stay_comparable():
    ina228 = {"measurement_scope": "on_device_gated_inference", "integrity": "valid"}
    assessment = assess_comparability(_run(power=ina228), _run(power=ina228))

    assert assessment.power_metrics_comparable


def test_partial_manifest_dimensions_fall_back_to_metadata():
    baseline = _run(model="abc")
    candidate = _run(model="def")
    from helia_profiler.results import ResultManifest, ResultValidity, RunStatus

    candidate = replace(
        candidate,
        manifest=ResultManifest(
            schema="hpx.result-manifest",
            schema_version=1,
            run_id="candidate",
            timestamp="2026-07-18T00:00:00+00:00",
            hpx_version="0.1.0",
            status=RunStatus.COMPLETE,
            validity=ResultValidity.VALID,
            issues=(),
            provenance={},
            comparability={},
            artifacts=(),
        ),
    )

    assessment = assess_comparability(baseline, candidate)

    assert not assessment.run_metrics_comparable



def _manifest_with_probe(probe: str | None):
    """A manifest carrying the probe dimension, as the writer records it."""
    from helia_profiler.results import ResultValidity
    from helia_profiler.results.manifest import (
        RESULT_MANIFEST_SCHEMA,
        RESULT_MANIFEST_SCHEMA_VERSION,
        ResultManifest,
        RunStatus,
    )

    return ResultManifest(
        schema=RESULT_MANIFEST_SCHEMA,
        schema_version=RESULT_MANIFEST_SCHEMA_VERSION,
        run_id="r",
        timestamp="2026-08-19T00:00:00Z",
        hpx_version="0.1.0",
        status=RunStatus.COMPLETE,
        validity=ResultValidity.VALID,
        issues=(),
        provenance={},
        comparability=(
            {"power_clean_window_probe": probe} if probe is not None else {}
        ),
        artifacts=(),
    )


def _powered(probe: str | None = "infer", **kwargs):
    run = _run(
        power={"measurement_scope": "gpio_gated_clean_window", "integrity": "valid"},
        **kwargs,
    )
    return replace(run, manifest=_manifest_with_probe(probe))


def test_a_spin_window_is_not_power_comparable_with_an_inference_window():
    """#125 item 4: `hpx compare` diffed a CPU spin against a model inference.

    The busy_loop probe replaces the window body with a calibrated spin, so
    the two runs measure different physical quantities -- but every dimension
    the comparison checked matched, and the delta was published as a real
    regression.
    """
    assessment = assess_comparability(_powered("infer"), _powered("busy_loop"))

    assert assessment.run_metrics_comparable, "only power deltas are affected"
    assert not assessment.power_metrics_comparable
    issue = next(
        issue
        for issue in assessment.issues
        if issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_CLEAN_WINDOW_PROBE)
    )
    assert issue.severity is ComparabilitySeverity.METRIC_BLOCKING
    assert issue.context["baseline"] == "infer"
    assert issue.context["candidate"] == "busy_loop"


def test_the_same_probe_stays_comparable():
    """The dimension must not block two runs of the same setup."""
    assessment = assess_comparability(_powered("infer"), _powered("infer"))

    assert assessment.power_metrics_comparable
    assert not any(
        issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_CLEAN_WINDOW_PROBE)
        for issue in assessment.issues
    )


def test_a_baseline_predating_the_dimension_is_skipped_not_blocked():
    """Stored baselines carry no value and must still compare.

    Same policy every dimension here applies: missing is unknown, not
    different.
    """
    assessment = assess_comparability(_powered(None), _powered("busy_loop"))

    assert assessment.power_metrics_comparable


def test_a_run_that_measured_no_power_does_not_block_one_that_did():
    """The regression an earlier, broader version of this dimension caused.

    A digest over the whole window context moved on `power.enabled` alone --
    the power floor raises `window_target_ms` only when power is on -- so
    comparing a quick latency run against a power-instrumented one suppressed
    the candidate's real power numbers and told the user "the measured window
    differs", which they had not chosen. Recording the dimension only for runs
    that measured power is what keeps that from happening: a run with no power
    result has nothing to say about how it measured power.
    """
    unpowered = replace(_run(), manifest=_manifest_with_probe(None))

    assessment = assess_comparability(unpowered, _powered("busy_loop"))

    assert not any(
        issue.code == POWER_DIMENSION_MISMATCH.code_for(ComparisonDimension.POWER_CLEAN_WINDOW_PROBE)
        for issue in assessment.issues
    )


def test_two_socs_running_the_same_probe_stay_power_comparable():
    """Cross-SoC power comparison is a supported question, not a defect.

    The same earlier version folded 8 SoC capability values into the digest,
    so apollo510 vs apollo4p stopped comparing on power entirely -- silently
    reversing the documented decision that board differences stay visible as
    experimental dimensions rather than blocking. What the probe dimension
    asks is narrower and correct: given whatever hardware, did the two runs
    put the same thing inside the window?
    """
    baseline = _powered("infer")
    candidate = replace(
        _powered("infer"),
        metadata={
            **_powered("infer").metadata,
            "platform": {"soc": "apollo4p", "cpu_clock_name": "hp"},
        },
    )

    assessment = assess_comparability(baseline, candidate)

    assert assessment.power_metrics_comparable


def _powered_run(
    fingerprint: str | None,
    *,
    board: str = "apollo510_evb",
    power_firmware: str = "dedicated",
):
    """A power run with the platform scope dimensions present (#138)."""
    power = {
        "measurement_scope": "gpio_gated_clean_window",
        "integrity": "valid",
        "power_firmware": power_firmware,
    }
    if fingerprint is not None:
        power["firmware_code_fingerprint"] = fingerprint
    run = _run(power=power)
    run.metadata["config"]["target"]["board"] = board
    return run


class TestPowerFirmwareFingerprint:
    """#138 / #115 item 1: the measured binary's code hash as a dimension."""

    CODE = POWER_DIMENSION_MISMATCH.code_for(
        ComparisonDimension.POWER_FIRMWARE_FINGERPRINT
    )

    def test_same_platform_fingerprint_mismatch_blocks_power(self):
        """The #115 shape: identical on every prior dimension, different
        measured-binary code — +678% must not present as a real regression."""
        assessment = assess_comparability(_powered_run("aaa"), _powered_run("bbb"))

        assert not assessment.power_metrics_comparable
        mismatch = [issue for issue in assessment.issues if issue.code == self.CODE]
        assert len(mismatch) == 1
        # The registry-declared mismatch_hint, not the generic sentence —
        # two 64-hex digests tell a user nothing actionable (#173 review
        # n4; pinned per round-2 m-D).
        assert "code fingerprint differs" in mismatch[0].message
        assert "Re-baseline" in mismatch[0].message

    def test_equal_fingerprints_compare_freely(self):
        assessment = assess_comparability(_powered_run("aaa"), _powered_run("aaa"))

        assert assessment.power_metrics_comparable
        assert not any(issue.code == self.CODE for issue in assessment.issues)

    def test_legacy_baseline_without_fingerprint_is_skipped(self):
        """Baselines predating the dimension carry no key — zero migration."""
        assessment = assess_comparability(_powered_run(None), _powered_run("bbb"))

        assert assessment.power_metrics_comparable
        assert not any(issue.code == self.CODE for issue in assessment.issues)

    def test_cross_board_pairs_never_consult_the_fingerprint(self):
        """#138 attempt-1 regression 3: board differences are documented as
        visible-not-blocking, and cross-platform renders trivially differ —
        a fingerprint mismatch only means something on a matching platform."""
        assessment = assess_comparability(
            _powered_run("aaa", board="apollo510_evb"),
            _powered_run("bbb", board="apollo4p_evb"),
        )

        assert assessment.power_metrics_comparable
        assert not any(issue.code == self.CODE for issue in assessment.issues)

    def test_firmware_mode_mismatch_scopes_the_fingerprint_out(self):
        """dedicated-vs-shared already blocks via POWER_FIRMWARE; the
        fingerprint (which hashes DIFFERENT binaries in the two modes) must
        not add a second, misleading issue on top."""
        assessment = assess_comparability(
            _powered_run("aaa", power_firmware="dedicated"),
            _powered_run("bbb", power_firmware="shared"),
        )

        assert not assessment.power_metrics_comparable  # POWER_FIRMWARE blocks
        assert not any(issue.code == self.CODE for issue in assessment.issues)

    def test_absent_scope_dimension_skips_the_fingerprint(self):
        """A legacy artifact that cannot even establish the platform match
        (no board recorded) is skipped, not blocked."""
        left = _powered_run("aaa")
        right = _powered_run("bbb")
        del left.metadata["config"]["target"]["board"]

        assessment = assess_comparability(left, right)

        assert not any(issue.code == self.CODE for issue in assessment.issues)


def test_scoped_to_is_declared_only_where_the_comparator_honours_it():
    """#173 review m3: only the POWER_DIMENSION_MISMATCH loop consults
    scoped_to — a spec declaring it under any other effect would be silently
    ignored, the exact failure mode the registry exists to prevent. Pin the
    invariant as registry data until a second loop needs the mechanism."""
    from helia_profiler.results.dimensions import (
        DIMENSION_REGISTRY,
        DimensionEffect,
    )

    for spec in DIMENSION_REGISTRY.values():
        if spec.scoped_to:
            assert spec.effect is DimensionEffect.POWER_METRIC_BLOCKING, (
                f"{spec.dimension} declares scoped_to under {spec.effect}, "
                "which no comparator loop honours"
            )
        # A scope member must itself be resolvable for both runs, i.e. a
        # registry dimension.
        for scope in spec.scoped_to:
            assert scope in DIMENSION_REGISTRY
