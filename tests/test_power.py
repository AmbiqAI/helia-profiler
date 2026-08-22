"""Tests for power driver abstraction."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from helia_profiler.config import CleanWindowProbe, WindowMode
from helia_profiler.errors import PowerError
from helia_profiler.results import DeploymentRecord, FirmwareArtifact, PowerRunPlan
from helia_profiler.power import get_driver, list_drivers, register_driver
from helia_profiler.power.metadata import (
    MeasurementScope,
    ObservationMode,
    PowerIntegrity,
    PowerMetadata,
)
from helia_profiler.power.base import (
    GatedPowerWindow,
    PowerMode,
    PowerResult,
    PowerSample,
    PowerSummary,
)

#: time64 tick rate (2**30 ticks per second), mirrors ``pyjoulescope_driver.time64.SECOND``.
_SECOND = 1 << 30


def _mark_power_firmware_deployed(ctx, tmp_path: Path) -> None:
    """Mark a synthetic dedicated artifact as deployed for capture-only tests."""
    binary = tmp_path / "hpx_profiler_power"
    binary.touch()
    artifact = FirmwareArtifact(
        role="power",
        target_name="hpx_profiler_power",
        app_dir=tmp_path,
        build_dir=tmp_path,
        binary_path=binary,
    )
    ctx.publish_power_plan(PowerRunPlan(
        firmware_mode="dedicated",
        inference_count=5,
        count_source="configured",
    ))
    ctx.publish_power_firmware(artifact)
    ctx.publish_power_deployment(DeploymentRecord(
        firmware=artifact,
        target_id=ctx.config.target.board,
        deployed_at="2026-07-18T00:00:00+00:00",
    ))


class TestPowerTypes:
    def test_power_sample_power_w(self):
        s = PowerSample(timestamp_s=0.0, current_a=0.010, voltage_v=1.8)
        assert abs(s.power_w - 0.018) < 1e-9

    def test_power_summary_frozen(self):
        summary = PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.018,
            peak_current_a=0.05,
            energy_j=0.54,
            duration_s=30.0,
            sample_count=1000,
        )
        with pytest.raises(AttributeError):
            summary.avg_current_a = 0.02  # type: ignore[misc]

    def test_power_result_no_per_layer_by_default(self):
        summary = PowerSummary(
            avg_current_a=0.01,
            avg_power_w=0.018,
            peak_current_a=0.05,
            energy_j=0.54,
            duration_s=30.0,
            sample_count=1000,
        )
        result = PowerResult(summary=summary)
        assert result.per_layer is None
        assert result.samples == []
        assert result.gated_windows == []
        assert result.metadata == PowerMetadata()

    def test_gated_window_is_typed(self):
        window = GatedPowerWindow(
            start_s=0.1,
            end_s=0.3,
            duration_s=0.2,
            charge_c=0.001,
            energy_j=0.002,
            avg_current_a=0.005,
            avg_power_w=0.01,
            peak_current_a=0.02,
            sample_count=123,
        )
        assert window.duration_s == 0.2
        assert window.sample_count == 123


class TestPowerDiagnostics:
    def test_sync_handshake_metadata_serializes_observed_ready(self):
        from helia_profiler.power.diagnostics import SyncHandshakeMetadata

        metadata = SyncHandshakeMetadata(
            lockstep=True,
            ready_wait_s=0.012,
            ready_observed=True,
        ).to_metadata()

        assert metadata == {
            "lockstep": True,
            "ready_wait_s": 0.012,
            "ready_observed": True,
        }

    def test_gate_failure_classifies_missing_rise(self):
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(saw_gate_rise=False, duration_s=7.0)

        assert failure.kind is GateFailureKind.NO_GATE_RISE
        assert "rising edge" in failure.message

    def test_gate_failure_classifies_missing_fall(self):
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(saw_gate_rise=True, duration_s=7.0)

        assert failure.kind is GateFailureKind.NO_GATE_FALL
        assert "did not fall" in failure.message

    def test_no_gate_rise_names_lockstep_when_it_is_off_on_wired_board(self):
        """Issue #114: this exact combination has a non-wiring cause.

        Lock-step off + state/GO pins present means the firmware never waits
        for the host, so the window can open and close before the GPI poller
        is armed. That reads as a dead gate wire, and cost real bench time
        (headers re-seated on an Apollo4 Blue Plus) before the flag was found.
        Both the message -- which is what the degraded-path ``log.warning``
        prints -- and the hint must name the fix.
        """
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(
            saw_gate_rise=False,
            duration_s=7.0,
            lockstep=False,
            lockstep_wiring_available=True,
        )

        assert failure.kind is GateFailureKind.NO_GATE_RISE
        assert "power.lockstep: true" in failure.message
        assert "power.lockstep: true" in failure.hint
        # It must be offered as the *leading* explanation, not buried after
        # the wiring checks the user already exhausted.
        assert failure.hint.index("power.lockstep") < failure.hint.index("wiring")

    @pytest.mark.parametrize(
        ("lockstep", "wired"),
        [
            (True, True),  # lock-step already on: cannot be the cause
            (False, False),  # board has no state/GO wires to run it over
            (None, True),  # caller did not report the handshake state
        ],
    )
    def test_no_gate_rise_does_not_blame_lockstep_when_it_cannot_be_the_cause(
        self, lockstep, wired
    ):
        """The hint is only useful if it stays quiet when it does not apply.

        A hint that blames lock-step on every missed gate is the same
        misdirection as blaming wiring on every missed gate.
        """
        from helia_profiler.power.diagnostics import GateFailureKind, classify_gate_failure

        failure = classify_gate_failure(
            saw_gate_rise=False,
            duration_s=7.0,
            lockstep=lockstep,
            lockstep_wiring_available=wired,
        )

        assert failure.kind is GateFailureKind.NO_GATE_RISE
        assert "power.lockstep" not in failure.message
        assert "power.lockstep" not in failure.hint
        assert "wiring" in failure.hint

    @pytest.mark.parametrize(
        ("saw_rise", "saw_fall"),
        [(True, False), (True, True)],
    )
    def test_lockstep_hint_is_scoped_to_no_gate_rise(self, saw_rise, saw_fall):
        """A gate that DID rise was observed, so lock-step armed in time.

        The other two failure kinds have their own causes (a hung window, a
        stats-timeline mismatch); pointing them at ``power.lockstep`` would be
        a fresh wrong turn.
        """
        from helia_profiler.power.diagnostics import classify_gate_failure

        failure = classify_gate_failure(
            saw_gate_rise=saw_rise,
            saw_gate_fall=saw_fall,
            duration_s=7.0,
            lockstep=False,
            lockstep_wiring_available=True,
        )

        assert "power.lockstep" not in failure.message
        assert "power.lockstep" not in failure.hint


class TestGatedStatsProcessing:
    """Host-side integration of on-device stat packets into gated windows."""

    @staticmethod
    def _packet(u0: int, u1: int, cur_int: float, pwr_int: float, cur_max: float):
        return {
            "time": {"utc": {"value": [u0, u1]}},
            "signals": {
                "current": {
                    "avg": {"value": cur_int / ((u1 - u0) / _SECOND)},
                    "max": {"value": cur_max},
                    "integral": {"value": cur_int},
                },
                "power": {
                    "avg": {"value": pwr_int / ((u1 - u0) / _SECOND)},
                    "integral": {"value": pwr_int},
                },
            },
        }

    @staticmethod
    def _packet_with_host_time(
        u0: int,
        u1: int,
        cur_int: float,
        pwr_int: float,
        cur_max: float,
        host_time64: int,
    ):
        packet = TestGatedStatsProcessing._packet(u0, u1, cur_int, pwr_int, cur_max)
        packet["_host_time64"] = host_time64
        return packet

    def test_gated_window_sums_ondevice_integrals(self):
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        ms = _SECOND // 1000
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            # Inject a transient spike in one in-window packet's max sample.
            cur_max = 0.5 if i == 8 else 0.12
            packets.append(self._packet(u0, u1, 0.0001, 0.00018, cur_max))

        rise = 5 * ms  # window covers packets with midpoint in [5ms, 15ms]
        fall = 15 * ms
        poll_samples = [(0, 0), (rise, 1), (fall, 0)]

        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=poll_samples, io_voltage=1.8
        )

        assert len(windows) == 1
        w = windows[0]
        assert w.sample_count == 10
        assert w.charge_c == pytest.approx(0.001, rel=1e-6)
        assert w.energy_j == pytest.approx(0.0018, rel=1e-6)
        assert w.duration_s == pytest.approx(0.01, rel=1e-6)
        assert w.avg_current_a == pytest.approx(0.1, rel=1e-6)
        assert w.avg_power_w == pytest.approx(0.18, rel=1e-6)
        # Raw peak captures the transient spike; the p99 robust peak rejects it.
        assert w.peak_current_a == pytest.approx(0.5, rel=1e-6)
        assert w.peak_current_p99_a < 0.5
        assert w.median_current_a == pytest.approx(0.1, rel=1e-6)
        assert summary.energy_j == pytest.approx(0.0018, rel=1e-6)

    def test_window_segmentation_rejects_ambiguous_capture_starting_high(self):
        from helia_profiler.power.joulescope.stats import _segment_gpi_windows

        ms = _SECOND // 1000
        windows = _segment_gpi_windows(
            [(0 * ms, 1), (1 * ms, 1), (2 * ms, 0)]
        )

        assert windows == []

    def test_no_windows_returns_empty(self):
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        ms = _SECOND // 1000
        packets = [self._packet(0, ms, 0.0001, 0.00018, 0.12)]
        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=[], io_voltage=1.8
        )
        assert windows == []
        assert summary.sample_count == 0

    def test_net_negative_gated_current_raises(self):
        """Backfeed/reversed-wiring corruption must fail loudly, not abs()."""
        from helia_profiler.errors import PowerError
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, -0.0001, 0.00018, 0.12)
            for i in range(20)
        ]
        poll_samples = [(0, 0), (5 * ms, 1), (15 * ms, 0)]

        with pytest.raises(PowerError, match="net NEGATIVE"):
            _process_gated_stats(
                packets=packets, poll_samples=poll_samples, io_voltage=1.8
            )

    def test_net_negative_gated_current_env_escape_hatch(self, monkeypatch):
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        monkeypatch.setenv("HPX_POWER_ALLOW_NEGATIVE", "1")
        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, -0.0001, 0.00018, 0.12)
            for i in range(20)
        ]
        poll_samples = [(0, 0), (5 * ms, 1), (15 * ms, 0)]

        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=poll_samples, io_voltage=1.8
        )
        assert len(windows) == 1
        assert summary.avg_current_a == pytest.approx(0.1, rel=1e-6)

    def test_gated_diagnostics_separates_selected_packets(self):
        from helia_profiler.power.joulescope.diagnostics import _gated_stats_diagnostics

        ms = _SECOND // 1000
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            cur_int = 0.0001 if 5 <= i < 15 else 0.00002
            pwr_int = cur_int * 1.8
            packets.append(self._packet(u0, u1, cur_int, pwr_int, 0.12))

        rise = 5 * ms
        fall = 15 * ms
        poll_samples = [(0, 0), (rise, 1), (fall, 0)]

        diagnostics = _gated_stats_diagnostics(packets=packets, poll_samples=poll_samples)

        assert diagnostics["window_count"] == 1
        assert diagnostics["selected_packets"] == 10
        assert diagnostics["rejected_packets"] == 10
        assert diagnostics["selected_median_current_a"] == pytest.approx(0.1, rel=1e-6)
        assert diagnostics["rejected_median_current_a"] == pytest.approx(0.02, rel=1e-6)

    def test_gated_stats_uses_host_packet_time_axis_when_available(self):
        from helia_profiler.power.joulescope.diagnostics import _gated_stats_diagnostics
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        ms = _SECOND // 1000
        host_base = 10_000 * ms
        packets = []
        for i in range(20):
            u0 = i * ms
            u1 = (i + 1) * ms
            host_tick = host_base + ((i * ms) + (ms // 2))
            cur_int = 0.0001 if 5 <= i < 15 else 0.00002
            pwr_int = cur_int * 1.8
            packets.append(
                self._packet_with_host_time(u0, u1, cur_int, pwr_int, 0.12, host_tick)
            )

        rise = host_base + 5 * ms
        fall = host_base + 15 * ms
        poll_samples = [(host_base, 0), (rise, 1), (fall, 0)]

        windows, summary = _process_gated_stats(
            packets=packets, poll_samples=poll_samples, io_voltage=1.8
        )
        diagnostics = _gated_stats_diagnostics(packets=packets, poll_samples=poll_samples)

        assert len(windows) == 1
        assert windows[0].sample_count == 10
        assert summary.avg_current_a == pytest.approx(0.1, rel=1e-6)
        assert diagnostics["mask_time_axis"] == "host_packet_arrival_time64"
        assert diagnostics["selected_packets"] == 10

    def test_maps_gpi_polls_to_instrument_packet_timeline(self):
        from helia_profiler.power.joulescope.stats import (
            _map_poll_samples_to_packet_time,
            _process_gated_stats,
        )

        ms = _SECOND // 1000
        packets = [
            self._packet_with_host_time(
                i * ms,
                (i + 1) * ms,
                0.0001,
                0.00018,
                0.12,
                host_time64=(100 + i) * ms,
            )
            for i in range(20)
        ]

        mapped = _map_poll_samples_to_packet_time(
            packets=packets,
            poll_samples=[
                (100 * ms, 0),
                ((209 * ms) // 2, 1),
                ((229 * ms) // 2, 0),
            ],
        )

        assert [level for _tick, level in mapped] == [0, 1, 0]
        assert abs(mapped[0][0] - (ms // 2)) <= 1
        assert abs(mapped[1][0] - (5 * ms)) <= 1
        assert abs(mapped[2][0] - (15 * ms)) <= 1
        windows, summary = _process_gated_stats(
            packets=packets,
            poll_samples=mapped,
            io_voltage=1.8,
            prefer_device_time=True,
        )
        assert len(windows) == 1
        assert windows[0].sample_count == 10
        assert summary.avg_current_a == pytest.approx(0.1, rel=1e-6)

    def test_js320_burst_timestamps_still_map_to_device_time(self):
        from helia_profiler.power.joulescope.stats import _map_poll_samples_to_packet_time

        ms = _SECOND // 1000
        packets = [
            self._packet_with_host_time(
                i * ms,
                (i + 1) * ms,
                0.0001,
                0.00018,
                0.12,
                host_time64=(100 if i < 10 else 110) * ms,
            )
            for i in range(20)
        ]

        mapped = _map_poll_samples_to_packet_time(
            packets=packets,
            poll_samples=[(100 * ms, 0), (105 * ms, 1), (110 * ms, 0)],
        )

        assert [level for _tick, level in mapped] == [0, 1, 0]
        assert mapped[0][0] < mapped[1][0] < mapped[2][0]

    def test_gated_stats_filters_short_gpio_glitch(self):
        from helia_profiler.power.joulescope.stats import _process_gated_stats

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12)
            for i in range(30)
        ]
        poll_samples = [
            (0, 0),
            (2 * ms, 1),
            (3 * ms, 0),
            (10 * ms, 1),
            (25 * ms, 0),
        ]

        windows, summary = _process_gated_stats(
            packets=packets,
            poll_samples=poll_samples,
            io_voltage=1.8,
            minimum_window_s=0.005,
        )

        assert len(windows) == 1
        assert windows[0].duration_s == pytest.approx(0.015, rel=1e-6)
        assert summary.energy_j == pytest.approx(0.0027, rel=1e-6)

    def test_gate_duration_integrity_allows_js_packet_jitter(self):
        from helia_profiler.power.diagnostics import assess_gate_duration

        integrity = assess_gate_duration(
            measured_s=4.954,
            clean_infer_count=235,
            clean_infer_avg_us=21079,
            stats_rate_hz=1000,
            minimum_s=1.0,
        )

        assert integrity.valid is True
        assert integrity.ratio == pytest.approx(1.0004, rel=1e-3)

    def test_gate_duration_integrity_rejects_sub_inference_pulse(self):
        from helia_profiler.power.diagnostics import assess_gate_duration

        integrity = assess_gate_duration(
            measured_s=0.0074,
            clean_infer_count=235,
            clean_infer_avg_us=21156,
            stats_rate_hz=1000,
            minimum_s=1.0,
        )

        assert integrity.valid is False
        assert integrity.ratio < 0.002

    def test_gate_duration_integrity_rejects_consistent_but_short_window(self):
        from helia_profiler.power.diagnostics import assess_gate_duration

        integrity = assess_gate_duration(
            measured_s=0.04,
            clean_infer_count=2000,
            clean_infer_avg_us=20,
            stats_rate_hz=1000,
            minimum_s=1.0,
        )

        assert integrity.ratio == pytest.approx(1.0)
        assert integrity.valid is False

    @pytest.mark.parametrize(
        ("measured_s", "clean_infer_count", "clean_infer_avg_us"),
        [
            (4.978970, 93, 53206),
            (4.886971, 2596, 1870),
        ],
    )
    def test_gate_duration_integrity_allows_bounded_dedicated_binary_drift(
        self, measured_s, clean_infer_count, clean_infer_avg_us
    ):
        from helia_profiler.power.diagnostics import assess_gate_duration

        integrity = assess_gate_duration(
            measured_s=measured_s,
            clean_infer_count=clean_infer_count,
            clean_infer_avg_us=clean_infer_avg_us,
            stats_rate_hz=1000,
            minimum_s=1.0,
        )

        assert 1.0 < integrity.ratio < 1.01
        assert integrity.valid is True

    def test_whole_summary_sums_all_packets(self):
        from helia_profiler.power.joulescope.stats import _whole_summary_from_stats

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12) for i in range(10)
        ]
        summary = _whole_summary_from_stats(packets)
        assert summary.sample_count == 10
        assert summary.energy_j == pytest.approx(0.0018, rel=1e-6)
        assert summary.duration_s == pytest.approx(0.01, rel=1e-6)
        assert summary.avg_power_w == pytest.approx(0.18, rel=1e-6)

    @pytest.mark.parametrize(
        ("saw_rise", "saw_fall", "failure_kind"),
        [
            (False, False, "no_gate_rise"),
            (True, False, "no_gate_fall"),
            (True, True, "no_stats_window"),
        ],
    )
    def test_missing_gate_returns_degraded_whole_capture(
        self, saw_rise: bool, saw_fall: bool, failure_kind: str
    ):
        from helia_profiler.power.joulescope.capture_gated import (
            _degraded_observation_result,
        )

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12)
            for i in range(10)
        ]

        result = _degraded_observation_result(
            packets=packets,
            family="js320",
            device_path="u/js320/test",
            io_voltage=1.8,
            sync_input_index=0,
            stats_rate_hz=1000,
            scnt=1000,
            poll_count=20,
            duration_s=7.0,
            captured_s=7.1,
            saw_gate_rise=saw_rise,
            saw_gate_fall=saw_fall,
            short_pulses_ignored=0,
        )

        assert result.gated_windows == []
        assert result.summary.sample_count == 10
        assert result.metadata.measurement_scope == "free_form_capture"
        assert result.metadata.observation_mode == "free_form"
        assert result.metadata.integrity == "degraded"
        assert result.metadata.gate_failure.kind == failure_kind
        assert result.metadata.gate_rise_observed is saw_rise
        assert result.metadata.gate_fall_observed is saw_fall

    def test_degraded_artifact_records_the_lockstep_diagnosis(self):
        """The retained degraded artifact must carry the lock-step diagnosis.

        The console warning scrolls away; ``summary.json`` is what gets
        attached to a bug report days later, so the stored ``gate_failure``
        metadata has to name ``power.lockstep: true`` too -- not just the live
        log line (issue #114).
        """
        from helia_profiler.power.joulescope.capture_gated import (
            _degraded_observation_result,
        )

        ms = _SECOND // 1000
        packets = [
            self._packet(i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12) for i in range(10)
        ]

        result = _degraded_observation_result(
            packets=packets,
            family="js320",
            device_path="u/js320/test",
            io_voltage=1.8,
            sync_input_index=0,
            stats_rate_hz=1000,
            scnt=1000,
            poll_count=20,
            duration_s=7.0,
            captured_s=7.1,
            saw_gate_rise=False,
            saw_gate_fall=False,
            short_pulses_ignored=0,
            lockstep=False,
            lockstep_wiring_available=True,
        )

        gate_failure = result.metadata.gate_failure
        assert gate_failure.kind == "no_gate_rise"
        assert "power.lockstep: true" in gate_failure.message
        assert "power.lockstep: true" in gate_failure.hint


class TestMissedGateWarningNamesTheFix:
    """End-to-end: the degraded-path ``log.warning`` must name the fix.

    Issue #114's whole cost was that the user only ever saw "no GPIO gate
    rising edge detected" and went looking for a wiring fault. This drives the
    real :func:`capture_gated` against a fake instrument whose GPI never goes
    high -- the exact bench signature -- and reads the log record back, so it
    pins what the operator actually sees rather than what a helper returns.
    """

    class _FakeJoulescopeDriver:
        """Minimal pyjoulescope_driver.Driver stand-in for the gated path."""

        def __init__(self) -> None:
            self._stats_cb = None

        def publish(self, _topic, _value, **_kwargs) -> None:
            pass

        def subscribe(self, _topic, _flags, callback) -> None:
            self._stats_cb = callback

        def unsubscribe(self, _topic, _callback) -> None:
            pass

        def emit_packets(self, count: int) -> None:
            ms = _SECOND // 1000
            for i in range(count):
                self._stats_cb(
                    "u/js320/test/s/stats/value",
                    TestGatedStatsProcessing._packet(
                        i * ms, (i + 1) * ms, 0.0001, 0.00018, 0.12
                    ),
                )

    def _run_capture(self, monkeypatch, *, lockstep: bool, wired: bool):
        from helia_profiler.power.joulescope import capture_gated as module
        from helia_profiler.power.joulescope.driver import JoulescopeDriver

        fake = self._FakeJoulescopeDriver()
        monkeypatch.setattr(
            module, "_open_device", lambda _serial: (fake, "u/js320/test", "js320")
        )
        # GPI never goes high: the gate was missed entirely.
        monkeypatch.setattr(module, "_read_gpi_snapshot", lambda _d, _p: 0)
        monkeypatch.setattr(module, "_close_device", lambda *_a, **_k: None)

        return module.capture_gated(
            JoulescopeDriver(),
            duration_s=0.3,
            io_voltage=1.8,
            sync_input_index=0,
            stats_rate_hz=1000,
            clean_infer_count=5,
            clean_infer_avg_us=1000,
            poll_interval_s=0.005,
            on_started=lambda _wait: fake.emit_packets(10),
            lockstep=lockstep,
            lockstep_wiring_available=wired,
        )

    def test_warning_names_power_lockstep_when_it_is_the_suspect(
        self, monkeypatch, caplog
    ):
        with caplog.at_level(logging.WARNING, logger="hpx"):
            result = self._run_capture(monkeypatch, lockstep=False, wired=True)

        assert result.metadata.integrity == "degraded"
        assert result.metadata.gate_failure.kind == "no_gate_rise"
        warnings = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        )
        assert "No GPIO gate rising edge detected" in warnings
        assert "power.lockstep: true" in warnings

    def test_warning_stays_wiring_only_when_lockstep_was_already_on(
        self, monkeypatch, caplog
    ):
        with caplog.at_level(logging.WARNING, logger="hpx"):
            result = self._run_capture(monkeypatch, lockstep=True, wired=True)

        assert result.metadata.gate_failure.kind == "no_gate_rise"
        warnings = "\n".join(
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        )
        assert "No GPIO gate rising edge detected" in warnings
        assert "power.lockstep" not in warnings
        assert "wiring" in warnings


class TestJoulescopeUngatedCapture:
    """Exercises :meth:`JoulescopeDriver.capture` (the non-gated path).

    Regression test for a real (pre-existing) bug: the ``_on_stats``
    callback referenced the ``pyjoulescope_driver.time64`` module without
    importing it in this method's scope (it was only imported in the
    sibling ``capture_gated`` method), so any stats packet arriving during
    a plain (non-gated) capture crashed with ``NameError: name 'time64' is
    not defined``.
    """

    class _FakeDriver:
        """Minimal pyjoulescope_driver.Driver stand-in.

        ``subscribe`` invokes the callback synchronously with one fake stat
        packet so ``capture()``'s ``_on_stats`` closure runs for real.
        """

        def __init__(self, packet: dict):
            self._packet = packet
            self.published: list[tuple[str, object]] = []

        def publish(self, topic, value, **kwargs):
            self.published.append((topic, value))

        def subscribe(self, topic, _flag, callback):
            callback(topic, self._packet)

        def unsubscribe(self, topic, callback):
            pass

    @staticmethod
    def _stats_packet():
        return {
            "signals": {
                "current": {"avg": {"value": 0.01}, "max": {"value": 0.02}},
                "voltage": {"avg": {"value": 1.8}},
            }
        }

    def test_capture_processes_stats_packet_without_crashing(self, monkeypatch: pytest.MonkeyPatch):
        from helia_profiler.power.joulescope.driver import JoulescopeDriver

        fake_driver = self._FakeDriver(self._stats_packet())
        monkeypatch.setattr(
            "helia_profiler.power.joulescope.driver._open_device",
            lambda serial: (fake_driver, "u/js220/000123", "js220"),
        )
        monkeypatch.setattr("helia_profiler.power.joulescope.driver.time.sleep", lambda _s: None)

        driver = JoulescopeDriver()
        result = driver.capture(duration_s=0.01, io_voltage=1.8)

        assert result.summary.sample_count == 1
        assert result.summary.avg_current_a == pytest.approx(0.01, rel=1e-6)

    def test_capture_processes_js320_stats_packet(self, monkeypatch: pytest.MonkeyPatch):
        from helia_profiler.power.joulescope.driver import JoulescopeDriver

        fake_driver = self._FakeDriver(self._stats_packet())
        monkeypatch.setattr(
            "helia_profiler.power.joulescope.driver._open_device",
            lambda serial: (fake_driver, "u/js320/25QG", "js320"),
        )
        monkeypatch.setattr("helia_profiler.power.joulescope.driver.time.sleep", lambda _s: None)

        driver = JoulescopeDriver(serial="25QG")
        result = driver.capture(duration_s=0.01, io_voltage=1.8)

        assert result.summary.sample_count == 1
        assert result.summary.avg_current_a == pytest.approx(0.01, rel=1e-6)
        assert ("u/js320/25QG/s/i/range/mode", "auto") in fake_driver.published


class TestPowerMode:
    def test_external(self):
        assert PowerMode.EXTERNAL == "external"
        assert PowerMode("external") is PowerMode.EXTERNAL

    def test_internal(self):
        assert PowerMode.INTERNAL == "internal"
        assert PowerMode("internal") is PowerMode.INTERNAL


class TestDriverRegistry:
    def test_list_drivers(self):
        drivers = list_drivers()
        assert "joulescope" in drivers
        assert "ondevice" in drivers

    def test_get_joulescope(self):
        driver = get_driver("joulescope")
        assert driver.name == "Joulescope"
        assert driver.mode is PowerMode.EXTERNAL

    def test_get_ondevice(self):
        driver = get_driver("ondevice")
        assert driver.name == "On-Device"
        assert driver.mode is PowerMode.INTERNAL

    def test_unknown_driver_raises(self):
        with pytest.raises(PowerError, match="Unknown power driver"):
            get_driver("nonexistent")


class TestJoulescopeDriver:
    def test_mode_is_external(self):
        driver = get_driver("joulescope")
        assert driver.mode is PowerMode.EXTERNAL

    def test_check_available_raises_without_package(self):
        """Joulescope check_available should raise PowerError if not installed."""
        driver = get_driver("joulescope")
        try:
            import pyjoulescope_driver  # noqa: F401

            # If pyjoulescope_driver is actually installed, skip this test
            pytest.skip("pyjoulescope_driver is installed — cannot test import failure")
        except ImportError:
            with pytest.raises(PowerError, match="not installed"):
                driver.check_available()


class TestOnDeviceDriver:
    def test_mode_is_internal(self):
        driver = get_driver("ondevice")
        assert driver.mode is PowerMode.INTERNAL

    def test_check_available_passes(self):
        driver = get_driver("ondevice")
        driver.check_available()  # Should not raise

    def test_capture_raises_not_implemented(self):
        driver = get_driver("ondevice")
        with pytest.raises(PowerError, match="not yet implemented"):
            driver.capture(duration_s=10.0, io_voltage=1.8)

    def test_power_cycle_raises_not_supported(self):
        driver = get_driver("ondevice")
        with pytest.raises(PowerError, match="cannot power-cycle"):
            driver.power_cycle()


class TestPowerConfig:
    def test_default_config(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {"model": {"path": str(model)}, "engine": {"type": "helia-rt"}},
        )
        assert config.power.enabled is False
        assert config.power.driver == "joulescope"
        assert config.power.mode == "external"
        assert config.power.sync_gpio_pin == 29
        assert config.power.firmware == "dedicated"

    def test_power_firmware_yaml_round_trip(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True, "firmware": "shared"},
            },
        )
        assert config.power.firmware == "shared"

    def test_power_firmware_invalid_value_raises(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.errors import ConfigError

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        with pytest.raises(ConfigError, match="power.firmware"):
            load_config(
                None,
                {
                    "model": {"path": str(model)},
                    "engine": {"type": "helia-rt"},
                    "power": {"firmware": "bogus"},
                },
            )

    def test_default_sync_gpio_pin_uses_board_metadata(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")

        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {
                    "board": "apollo510_lab",
                    "custom_boards": {
                        "apollo510_lab": {
                            "based_on": "apollo510_evb",
                            "default_sync_gpio_pin": 27,
                        }
                    },
                },
            },
        )

        assert config.power.sync_gpio_pin == 27

    def test_custom_power_config(self, tmp_path: Path):
        from helia_profiler.config import load_config

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "ondevice",
                    "mode": "internal",
                    "sync_gpio_pin": 42,
                    "duration_s": 60,
                },
            },
        )
        assert config.power.enabled is True
        assert config.power.driver == "ondevice"
        assert config.power.mode == "internal"
        assert config.power.sync_gpio_pin == 42
        assert config.power.duration_s == 60

    def test_power_reset_strategy_config(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.target.lifecycle import ResetStrategy

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"reset_strategy": "swpoi_reset"},
            },
        )

        assert config.power.reset_strategy is ResetStrategy.SWPOI_RESET


class TestCapturePowerStage:
    def test_skipped_when_disabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {"model": {"path": str(model)}, "engine": {"type": "helia-rt"}},
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is True

    def test_not_skipped_when_enabled(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        stage = CapturePowerStage()
        assert stage.should_skip(ctx) is False

    def test_resets_target_before_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Power capture must re-launch the firmware so the gated window fires
        # under the live poller; relay-cycled boards drawing USB bench power are
        # not rebooted, so a J-Link reset is the deterministic restart.
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {"transport": "uart", "jlink_serial": "1160002204"},
                "power": {"enabled": True, "driver": "joulescope"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board("apollo510_evb")
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="shared"))
        reset_calls: dict[str, object] = {}

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: reset_calls.update(k),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: reset_calls.setdefault("swpoi", k),
        )

        def fake_capture_power(ctx, **kwargs):
            plan = kwargs["prepare_target"](FakeDriver(), "joulescope")
            return PowerResult(
                summary=PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0),
                metadata=PowerMetadata(target_lifecycle=plan),
            )

        monkeypatch.setattr(
            "helia_profiler.capture.capture_power",
            fake_capture_power,
        )
        CapturePowerStage().run(ctx)
        assert reset_calls["jlink_serial"] == "1160002204"
        assert reset_calls["device"] == ctx.soc.jlink_device
        assert ctx.power_result is not None
        lifecycle = ctx.power_result.metadata.target_lifecycle.to_metadata()
        assert {k: v for k, v in lifecycle.items() if k != "timings_s"} == {
            "phase": "power",
            "power_cycle_attempted": False,
            "power_cycle_succeeded": False,
            "reset_strategy": "auto",
            "reset_action": "debug_reset+swpoi_reset",
            "actions": ["debug_reset+swpoi_reset"],
        }
        assert set(lifecycle["timings_s"]) == {"reset"}

    def test_degraded_driver_result_publishes_free_form_observation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True, "firmware": "shared"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="shared"))
        degraded = PowerResult(
            summary=PowerSummary(0.01, 0.018, 0.02, 0.18, 10.0, 10000),
            metadata=PowerMetadata(
                measurement_scope=MeasurementScope.FREE_FORM_CAPTURE,
                observation_mode=ObservationMode.FREE_FORM,
                gate_rise_observed=False,
                gate_fall_observed=False,
                integrity=PowerIntegrity.DEGRADED,
            ),
        )
        monkeypatch.setattr(
            "helia_profiler.capture.capture_power",
            lambda *_args, **_kwargs: degraded,
        )

        CapturePowerStage().run(ctx)

        assert ctx.power_run is not None
        assert ctx.power_run.observation is not None
        assert ctx.power_run.observation.mode == "free_form"
        assert ctx.power_run.observation.integrity == "degraded"
        assert ctx.power_result is degraded

    def test_busy_loop_progress_message_says_pass_not_inference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """#139: the arm/reset progress message must not call the busy_loop
        probe's one calibrated spin an "inference" -- it runs none."""
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "profiling": {"clean_window_probe": "busy_loop"},
                "power": {"enabled": True, "firmware": "shared"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.publish_power_plan(
            PowerRunPlan(
                firmware_mode="shared",
                inference_count=1,
                reference_inference_us=1_000_000,
                count_source="probe_window",
            )
        )
        degraded = PowerResult(
            summary=PowerSummary(0.01, 0.018, 0.02, 0.18, 10.0, 10000),
            metadata=PowerMetadata(
                measurement_scope=MeasurementScope.FREE_FORM_CAPTURE,
                observation_mode=ObservationMode.FREE_FORM,
                gate_rise_observed=False,
                gate_fall_observed=False,
                integrity=PowerIntegrity.DEGRADED,
            ),
        )
        monkeypatch.setattr(
            "helia_profiler.capture.capture_power",
            lambda *_args, **_kwargs: degraded,
        )
        messages: list[str] = []
        ctx.progress_sink = lambda update: messages.append(update.message)

        CapturePowerStage().run(ctx)

        arming = next(m for m in messages if m.startswith("Arming instrument"))
        assert "1 busy-loop pass" in arming
        assert "inference" not in arming

    def test_internal_mode_skips_host_capture(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.capture_power import CapturePowerStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "ondevice",
                    "mode": "internal",
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)

        assert CapturePowerStage().should_skip(ctx) is True

    def test_unknown_driver_scope_is_degraded_by_default(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True, "firmware": "shared"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="shared"))
        result = PowerResult(
            summary=PowerSummary(0.01, 0.018, 0.02, 0.18, 10.0, 10000),
            metadata=PowerMetadata(measurement_scope="custom_gated"),
        )

        ctx.publish_power_result(result)

        assert ctx.power_run is not None
        assert ctx.power_run.observation is not None
        assert ctx.power_run.observation.mode == "free_form"
        assert ctx.power_run.observation.integrity == "degraded"
        assert result.metadata.observation_mode == "free_form"
        assert result.metadata.integrity == "degraded"


class TestTargetLifecycle:
    def _make_ctx(self, tmp_path: Path, *, board: str):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {"board": board, "jlink_serial": "1160002204"},
                "power": {"enabled": True, "driver": "joulescope"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board(board)
        return ctx

    def test_ap4_power_phase_uses_debug_reset_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto AP4 reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.phase is CapturePhase.POWER
        assert plan.power_cycle_attempted is False
        assert plan.power_cycle_succeeded is False
        assert plan.reset_action is ResetAction.DEBUG_RESET
        assert plan.actions == ("debug_reset",)
        assert [name for name, _ in calls] == ["reset"]

    def test_ap5_power_phase_preserves_current_swpoi_policy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo510_evb")
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("auto AP5 reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.reset_action is ResetAction.DEBUG_RESET_THEN_SWPOI
        assert plan.actions == ("debug_reset+swpoi_reset",)
        assert [name for name, _ in calls] == ["reset", "swpoi"]

    def test_explicit_ap4_swpoi_uses_swpoi_as_replacement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import (
            CapturePhase,
            ResetAction,
            prepare_target_for_phase,
        )

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="swpoi_reset"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("explicit SWPOI reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.reset_action is ResetAction.SWPOI_RESET
        assert plan.actions == ("swpoi_reset",)
        assert [name for name, _ in calls] == ["swpoi"]

    def test_explicit_no_reset_does_not_touch_hardware(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="none"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("none reset must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.reset_action is ResetAction.NONE
        assert plan.actions == ()
        assert [name for name, _ in calls] == []

    def test_explicit_power_cycle_requires_rail_toggle_and_skips_jlink_reset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="power_cycle"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                calls.append(("power_cycle", kwargs))

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target_poi",
            lambda **k: calls.append(("swpoi", k)),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.POWER,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.reset_action is ResetAction.NONE
        assert plan.actions == ("power_cycle",)
        assert [name for name, _ in calls] == ["power_cycle"]

    def test_explicit_power_cycle_fails_if_rail_toggle_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import CapturePhase, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo4p_blue_kxr_evb")
        ctx.config = replace(ctx.config, power=replace(ctx.config.power, reset_strategy="power_cycle"))
        calls: list[tuple[str, dict]] = []

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise PowerError("no rail control")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: calls.append(("reset", k)),
        )

        with pytest.raises(PowerError, match="no rail control"):
            prepare_target_for_phase(
                ctx,
                phase=CapturePhase.POWER,
                power_driver=FakeDriver(),
                power_driver_name="joulescope",
            )

        assert calls == []

    def test_non_power_phase_does_not_touch_hardware(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.target.lifecycle import CapturePhase, ResetAction, prepare_target_for_phase

        ctx = self._make_ctx(tmp_path, board="apollo510_evb")

        class FakeDriver:
            def power_cycle(self, **kwargs):
                raise AssertionError("non-power phase must not power-cycle")

        monkeypatch.setattr(
            "helia_profiler.target.probe.jlink.reset_target",
            lambda **k: (_ for _ in ()).throw(AssertionError("must not reset")),
        )

        plan = prepare_target_for_phase(
            ctx,
            phase=CapturePhase.PMU,
            power_driver=FakeDriver(),
            power_driver_name="joulescope",
        )

        assert plan.phase is CapturePhase.PMU
        assert plan.power_cycle_attempted is False
        assert plan.reset_action is ResetAction.NONE


class TestEstimateCaptureDuration:
    """Regression coverage for the auto-tuned capture-duration estimate.

    Bug: the estimate previously only accounted for the per-layer PMU
    passes (presets x (warmup + iterations)) and ignored the separately
    configured GPIO-gated clean window, so a long clean window (window_mode
    'auto' with a large window_target_ms, or a large 'fixed' iterations
    count) produced a safety bound shorter than the actual firmware run,
    causing the Joulescope poller to miss the window's falling edge.
    """

    def _make_ctx(self, tmp_path: Path, *, profiling_overrides: dict):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.platform import get_soc_for_board
        from helia_profiler.results import FirmwareMeta, LayerResult, PlatformInfo, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "profiling": profiling_overrides,
                "power": {"enabled": True},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.soc = get_soc_for_board("apollo510_evb")
        ctx.run_metadata.platform = PlatformInfo(cpu_clock_mhz=96)
        # 96,000 cycles at 96 MHz == 1 ms/inference, a convenient round number.
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(presets=("basic_cpu",)),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=96_000.0)],
        )
        return ctx

    def test_fixed_window_includes_clean_iterations(self, tmp_path: Path):
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "fixed",
                "iterations": 300,
                "warmup": 1,
            },
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # profiled pass: 1 * (1 + 300) = 301 inferences.
        # clean pass (fixed): max(1, 300) iterations + max(3, 1) warmups
        # = 303 inferences — the warmup floors at 3 because the fixed+STIMER
        # firmware arm floors its measured warmup there (#164), and for
        # DWT-timed fixed builds the overestimate only adds headroom.
        # total = 604 inferences * 1 ms/inference = 0.604 s.
        expected = _BOOT_SETTLE_S + 0.604 + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)

    def test_auto_window_scales_with_target_ms(self, tmp_path: Path):
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "auto",
                "window_target_ms": 8000,
                "window_min": 10,
                "window_max": 500,
                "iterations": 3,
                "warmup": 1,
            },
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # profiled pass: 1 * (1 + 3) = 4 inferences = 4ms.
        # clean pass (auto): target 8000ms / 1ms = 8000 iters, clamped to
        # window_max=500, plus 3 hardcoded warm reps = 503 inferences = 0.503s.
        expected = _BOOT_SETTLE_S + (0.004 + 0.503) + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)

    def test_a_spin_window_is_estimated_in_seconds_not_inferences(
        self, tmp_path: Path
    ):
        """The busy_loop window is a spin; sizing it in inferences under-bounds it.

        Reached whenever there is no resolved plan to early-return from --
        every `firmware: shared` run. The fixed-mode branch sized the clean
        window as `iterations x inference_time`, which for a 20 s spin gave a
        deadline well inside the window: exactly the miss this function's
        docstring says it exists to prevent (found by review of #136).
        """
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "clean_window_probe": "busy_loop",
                "window_mode": "fixed",
                "window_target_ms": 20000,
                "iterations": 3,
                "warmup": 1,
            },
        )

        estimated = _estimate_capture_duration(ctx)

        assert estimated is not None
        # profiled pass: 1 * (1 + 3) = 4 inferences = 4 ms. The clean pass is
        # the spin itself -- 20 s, whatever the inference count says -- plus
        # the warm reps, which main.cc.j2 runs above the spin whatever the
        # probe is: max(1, warmup) = 1 inference = 1 ms in fixed mode.
        expected = _BOOT_SETTLE_S + (0.004 + 20.0 + 0.001) + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)
        assert estimated > 20.0, "the bound must outlast the window it contains"

    def test_fixed_power_plan_controls_capture_duration(self, tmp_path: Path):
        from helia_profiler.stages.capture_power import (
            _BOOT_SETTLE_S,
            _SAFETY_MARGIN_S,
            _estimate_capture_duration,
        )

        ctx = self._make_ctx(tmp_path, profiling_overrides={})
        ctx.power_plan = PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=2247,
            reference_inference_us=2226,
            count_source="profile_guided",
        )

        estimated = _estimate_capture_duration(ctx)

        expected = _BOOT_SETTLE_S + (2247 * 2226 / 1_000_000) + _SAFETY_MARGIN_S
        assert estimated == pytest.approx(expected, rel=1e-6)

    def test_auto_window_regression_reproduces_prior_underestimate_bug(
        self, tmp_path: Path
    ):
        # This mirrors the real config that triggered "No GPIO-high windows
        # detected": a model with representative per-inference timing and
        # window_target_ms 8000 needs ~379 clean iterations (~8s), which the
        # old estimate (based only on the 4 profiled PMU passes) undercounted
        # as ~7.1s.
        from helia_profiler.stages.capture_power import _estimate_capture_duration
        from helia_profiler.results import FirmwareMeta, LayerResult, PmuResult

        ctx = self._make_ctx(
            tmp_path,
            profiling_overrides={
                "window_mode": "auto",
                "window_target_ms": 8000,
                "window_min": 10,
                "window_max": 500,
                "iterations": 3,
                "warmup": 1,
            },
        )
        # 2,029,073 cycles at 96 MHz == ~21.136ms/inference (representative values).
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(presets=("basic_cpu",)),
            layers=[LayerResult(id=0, op="CONV_2D", cycles=2_029_073.0)],
        )
        estimated = _estimate_capture_duration(ctx)
        assert estimated is not None
        # The real firmware run was observed at ~8.16s wall-clock; the fixed
        # estimate must cover that, unlike the old ~7.1s underestimate.
        assert estimated > 8.16


class TestCapturePowerWrapper:
    def test_capture_power_uses_gated_joulescope_path_and_preserves_serial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope",
                    "serial": "004204",
                    "sync_input_index": 0,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])
        _mark_power_firmware_deployed(ctx, tmp_path)

        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)
        called: dict[str, object] = {}

        class FakeDriver:
            supports_gated_capture = True

            def check_available(self):
                called["checked"] = True

            def capture(self, **kwargs):
                called["capture"] = kwargs
                return PowerResult(summary=summary)

            def capture_gated(self, **kwargs):
                called["capture_gated"] = kwargs
                return PowerResult(
                    summary=summary,
                    metadata=PowerMetadata(measurement_scope=MeasurementScope.GPIO_GATED_CLEAN_WINDOW),
                )

        def fake_get_driver(name: str, *, serial: str | None = None):
            called["name"] = name
            called["serial"] = serial
            return FakeDriver()

        monkeypatch.setattr("helia_profiler.power.get_driver", fake_get_driver)

        result = capture_power(ctx, duration_override_s=7.0)

        assert result.metadata.measurement_scope == "gpio_gated_clean_window"
        assert called["name"] == "joulescope"
        assert called["serial"] == "004204"
        assert called["checked"] is True
        assert "capture" not in called
        gated = dict(called["capture_gated"])
        on_started = gated.pop("on_started")
        assert callable(on_started)
        # GO backfeed fix: the gate-rise hook must be wired so the GO line is
        # dropped as soon as the window is observed high.
        on_gate_rise = gated.pop("on_gate_rise")
        assert callable(on_gate_rise)
        phase_getter = gated.pop("phase_getter")
        assert callable(phase_getter)
        assert gated == {
            "duration_s": 7.0,
            "io_voltage": 1.8,
            "sync_input_index": 0,
            "state_input_index": 1,
            "stats_rate_hz": 1000,
            "clean_infer_count": 5,
            "work_noun": "inferences",
            "clean_infer_avg_us": None,
            "minimum_gate_s": 1.0,
            "gate_relative_tolerance": 0.10,
            # Lock-step facts for the gate-failure classifier (issue #114).
            # This FakeDriver has no make_sync_controller, so the host degrades
            # to the null controller regardless of config -- which is exactly
            # the runtime truth the classifier needs.
            "lockstep": False,
            "lockstep_wiring_available": True,
        }

    def test_capture_power_waits_for_lockstep_ready_before_go(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope",
                    "lockstep": True,
                    "state_gpio_pin": 23,
                    "go_gpio_pin": 24,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])
        _mark_power_firmware_deployed(ctx, tmp_path)

        calls: list[str] = []
        hooks: dict[str, object] = {}
        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)

        class FakeSync:
            lockstep = True

            def arm(self):
                calls.append("arm")

            def wait_ready(self, *, timeout_s: float):
                calls.append(f"wait_ready:{timeout_s}")
                return True

            def signal_go(self):
                assert hooks["phase_getter"]() == "go_signaled"
                calls.append("go")

            def release_go(self):
                calls.append("release_go")

            def read_state(self):
                raise AssertionError("read_state should not be called on ready path")

            def release(self):
                calls.append("release")

        class FakeDriver:
            supports_gated_capture = True

            def check_available(self):
                calls.append("check")

            def make_sync_controller(self, wiring):
                calls.append("make_sync")
                return FakeSync()

            def capture_gated(self, **kwargs):
                calls.append("capture_gated")
                hooks["phase_getter"] = kwargs["phase_getter"]
                kwargs["on_started"]()
                return PowerResult(summary=summary)

        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: FakeDriver())

        def prepare_target(driver, name):
            calls.append(f"prepare:{name}")

            class Plan:
                def to_metadata(self):
                    return {"reset_action": "debug_reset"}

            return Plan()

        result = capture_power(ctx, duration_override_s=7.0, prepare_target=prepare_target)

        # Revised ordering (AP510 combo-reset gate-race fix): capture_gated
        # starts the GPI poller first; prepare/wait_ready/go run inside its
        # on_started hook so no reset can race an unobserved gate window.
        assert calls == [
            "check",
            "make_sync",
            "arm",
            "capture_gated",
            "prepare:joulescope",
            "wait_ready:7.0",
            "go",
            "release",
        ]
        assert result.metadata.sync.lockstep is True
        assert result.metadata.sync.ready_wait_s >= 0.0
        assert result.metadata.sync.ready_observed is True
        assert result.metadata.target_lifecycle.to_metadata() == {"reset_action": "debug_reset"}

    def test_capture_power_releases_sync_when_prepare_raises_after_arm(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """``sync.release()`` must run even if prepare_target raises after arm().

        Regression test: previously ``sync.arm()`` and ``_prepare_target_once()``
        executed before the try/finally that guarantees ``sync.release()``, so a
        prepare-time exception (e.g. a failed reset) left the host GO line held
        low with no release.
        """
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope",
                    "lockstep": True,
                    "state_gpio_pin": 23,
                    "go_gpio_pin": 24,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])
        _mark_power_firmware_deployed(ctx, tmp_path)

        calls: list[str] = []

        class FakeSync:
            lockstep = True

            def arm(self):
                calls.append("arm")

            def wait_ready(self, *, timeout_s: float):  # pragma: no cover - unreachable
                raise AssertionError("wait_ready should not be reached")

            def signal_go(self):  # pragma: no cover - unreachable
                raise AssertionError("signal_go should not be reached")

            def release_go(self):  # pragma: no cover - unreachable
                raise AssertionError("release_go should not be reached")

            def read_state(self):  # pragma: no cover - unreachable
                raise AssertionError("read_state should not be reached")

            def release(self):
                calls.append("release")

        class FakeDriver:
            supports_gated_capture = True

            def check_available(self):
                calls.append("check")

            def make_sync_controller(self, wiring):
                calls.append("make_sync")
                return FakeSync()

            def capture_gated(self, **kwargs):
                # Mirrors JoulescopeDriver.capture_gated: the prepare/handshake
                # now runs inside on_started, whose exceptions the driver
                # swallows (logs) — the capture wrapper re-raises them after.
                calls.append("capture_gated")
                try:
                    kwargs["on_started"]()
                except Exception:
                    pass
                return PowerResult(summary=PowerSummary(0.0, 0.0, 0.0, 0.0, 0.0, 0))

        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: FakeDriver())

        def prepare_target(driver, name):
            calls.append("prepare")
            raise RuntimeError("reset failed")

        with pytest.raises(RuntimeError, match="reset failed"):
            capture_power(ctx, duration_override_s=7.0, prepare_target=prepare_target)

        assert calls == ["check", "make_sync", "arm", "capture_gated", "prepare", "release"]


class TestPowerFirmwareSelection:
    """WP3: flashing the dedicated power binary before gated power capture."""

    def _make_ctx(
        self,
        tmp_path: Path,
        *,
        firmware: str,
        transport: str = "rtt",
        board: str = "apollo510_evb",
    ):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult
        from helia_profiler.stages.resolve_platform import ResolvePlatformStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "target": {"board": board, "transport": transport},
                "power": {
                    "enabled": True,
                    "driver": "joulescope",
                    "firmware": firmware,
                    "sync_input_index": 0,
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ResolvePlatformStage().run(ctx)
        ctx.resolved_jlink_serial = "1160002204"
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=11), layers=[])
        if firmware == "shared":
            ctx.publish_power_plan(PowerRunPlan(firmware_mode="shared"))
        return ctx

    class _FakeDriver:
        supports_gated_capture = True

        def __init__(self, calls: list[str]):
            self._calls = calls

        def check_available(self):
            self._calls.append("check")

        def capture_gated(self, **kwargs):
            self._calls.append("capture_gated")
            kwargs["on_started"]()
            return PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6))

        def capture(self, **kwargs):  # pragma: no cover - gated path used
            return PowerResult(summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6))

    def test_dedicated_flash_stage_deploys_before_capture(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.power_binary_path = power_bin
        ctx.publish_power_plan(PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=5,
            count_source="configured",
        ))
        ctx.publish_power_firmware(FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=tmp_path,
            build_dir=tmp_path,
            binary_path=power_bin,
        ))

        calls: list[str] = []
        flash_calls: list[dict] = []

        monkeypatch.setattr(
            "helia_profiler.power.get_driver", lambda *a, **k: self._FakeDriver(calls)
        )

        def fake_flash_binary(binary_path, **kwargs):
            flash_calls.append({"binary_path": binary_path, **kwargs})
            calls.append("flash")

        monkeypatch.setattr("helia_profiler.target.probe.flash.flash_binary", fake_flash_binary)

        FlashPowerFirmwareStage().run(ctx)
        result = capture_power(ctx, duration_override_s=7.0)

        assert calls == ["flash", "check", "capture_gated"]
        assert flash_calls[0]["binary_path"] == power_bin
        assert flash_calls[0]["jlink_serial"] == "1160002204"
        # The stage resolves the SoC's app flash load address for the .bin
        # fallback; apollo510_evb is AP5, so it must not be an AP3/AP4 address.
        assert flash_calls[0]["load_addr"] == 0x00410000
        assert result.metadata.power_firmware == "dedicated"
        assert ctx.power_run is not None
        assert ctx.power_run.deployment is not None
        assert ctx.power_run.deployment.firmware is ctx.power_firmware

    def test_dedicated_flash_stage_resolves_load_addr_per_soc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """The stage must resolve the address from the SoC, not hardcode one.

        Deliberately a non-AP5 board: on AP5 a call site that hardcoded the
        Apollo5 address would still look correct, so only a part whose address
        differs can tell "resolved from capabilities" from "hardcoded".
        """
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated", board="apollo4p_blue_kxr_evb")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.publish_power_plan(
            PowerRunPlan(firmware_mode="dedicated", inference_count=5, count_source="configured")
        )
        ctx.publish_power_firmware(
            FirmwareArtifact(
                role="power",
                target_name="hpx_profiler_power",
                app_dir=tmp_path,
                build_dir=tmp_path,
                binary_path=power_bin,
            )
        )

        flash_calls: list[dict] = []
        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary",
            lambda binary_path, **kwargs: flash_calls.append(kwargs),
        )

        FlashPowerFirmwareStage().run(ctx)

        assert flash_calls[0]["load_addr"] == 0x00018000
        assert flash_calls[0]["load_addr"] != 0x00410000

    def test_dedicated_flash_retries_after_power_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.errors import CaptureError
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.publish_power_plan(
            PowerRunPlan(firmware_mode="dedicated", inference_count=5, count_source="configured")
        )
        ctx.publish_power_firmware(
            FirmwareArtifact(
                role="power",
                target_name="hpx_profiler_power",
                app_dir=tmp_path,
                build_dir=tmp_path,
                binary_path=power_bin,
            )
        )
        attempts = 0

        def flash_binary(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise CaptureError("debug domain unavailable")

        monkeypatch.setattr("helia_profiler.target.probe.flash.flash_binary", flash_binary)
        cycles: list[str] = []
        monkeypatch.setattr(
            "helia_profiler.stages.flash_power.try_power_cycle_for_context",
            lambda _ctx: cycles.append("cycle") or True,
        )

        FlashPowerFirmwareStage().run(ctx)

        assert attempts == 2
        assert cycles == ["cycle"]
        assert ctx.power_run is not None and ctx.power_run.deployment is not None

    def test_dedicated_flash_deterministic_error_does_not_power_cycle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A config-gap refusal raises straight through: no rail cycle, no retry.

        The missing-image and unknown-load-address refusals are deterministic —
        a power cycle reproduces them identically — so cycling the DUT rail and
        retrying only frames a configuration gap as flaky hardware (#151).
        """
        from helia_profiler.errors import DeterministicCaptureError
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.publish_power_plan(
            PowerRunPlan(firmware_mode="dedicated", inference_count=5, count_source="configured")
        )
        ctx.publish_power_firmware(
            FirmwareArtifact(
                role="power",
                target_name="hpx_profiler_power",
                app_dir=tmp_path,
                build_dir=tmp_path,
                binary_path=power_bin,
            )
        )
        attempts = 0

        def flash_binary(*_args, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise DeterministicCaptureError(
                "No flashable image for hpx_profiler_power",
                hint="Re-run the build; the NSX build emits both per target.",
            )

        monkeypatch.setattr("helia_profiler.target.probe.flash.flash_binary", flash_binary)
        cycles: list[str] = []
        monkeypatch.setattr(
            "helia_profiler.stages.flash_power.try_power_cycle_for_context",
            lambda _ctx: cycles.append("cycle") or True,
        )

        with pytest.raises(DeterministicCaptureError) as exc_info:
            FlashPowerFirmwareStage().run(ctx)

        assert attempts == 1
        assert cycles == []
        # Re-wrapped with the stage context (same type — the taxonomy signal
        # survives) so the user still sees WHICH deployment step refused;
        # the hint renders exactly once (#172 review).
        assert str(exc_info.value).startswith("Power firmware deployment failed: ")
        assert "No flashable image" in str(exc_info.value)
        assert str(exc_info.value).count("Hint:") == 1

    @pytest.mark.parametrize("cycle_succeeds", [True, False])
    def test_dedicated_flash_failure_prints_the_hint_exactly_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cycle_succeeds: bool
    ):
        """Wrapping must not embed the already-hint-suffixed str(exc) (#151).

        ``HpxError.__str__`` appends ``Hint: …``, and the stage re-attaches the
        inner hint via ``hint=``; interpolating ``str(exc)`` into the wrapper's
        message therefore printed the hint twice, on both the no-recovery and
        the retry-exhausted paths.
        """
        from helia_profiler.errors import BuildError, CaptureError
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.publish_power_plan(
            PowerRunPlan(firmware_mode="dedicated", inference_count=5, count_source="configured")
        )
        ctx.publish_power_firmware(
            FirmwareArtifact(
                role="power",
                target_name="hpx_profiler_power",
                app_dir=tmp_path,
                build_dir=tmp_path,
                binary_path=power_bin,
            )
        )

        def flash_binary(*_args, **_kwargs):
            raise CaptureError(
                "debug domain unavailable",
                hint="Check the probe connection.",
            )

        monkeypatch.setattr("helia_profiler.target.probe.flash.flash_binary", flash_binary)
        monkeypatch.setattr(
            "helia_profiler.stages.flash_power.try_power_cycle_for_context",
            lambda _ctx: cycle_succeeds,
        )

        with pytest.raises(BuildError) as exc_info:
            FlashPowerFirmwareStage().run(ctx)

        message = str(exc_info.value)
        assert message.count("Check the probe connection.") == 1
        assert message.count("Hint:") == 1
        assert "debug domain unavailable" in message

    def test_shared_mode_does_not_flash_and_uses_dtr_holder_for_usb_cdc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power

        ctx = self._make_ctx(tmp_path, firmware="shared", transport="usb_cdc")
        # Even if a power binary happens to be present, shared mode must not
        # touch it.
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.power_binary_path = power_bin

        calls: list[str] = []
        flash_calls: list[dict] = []
        dtr_calls: list[str] = []

        monkeypatch.setattr(
            "helia_profiler.power.get_driver", lambda *a, **k: self._FakeDriver(calls)
        )
        monkeypatch.setattr(
            "helia_profiler.target.probe.flash.flash_binary",
            lambda *a, **k: flash_calls.append({}),
        )

        class FakeDtrHolder:
            def __init__(self, **kwargs):
                dtr_calls.append("init")

            def open(self):
                dtr_calls.append("open")

            def close(self):
                dtr_calls.append("close")

        monkeypatch.setattr("helia_profiler.capture._UsbDtrHolder", FakeDtrHolder)

        result = capture_power(ctx, duration_override_s=7.0)

        assert flash_calls == []
        assert dtr_calls == ["init", "open", "close"]
        assert result.metadata.power_firmware == "shared"

    def test_dedicated_flash_stage_requires_built_artifact(self, tmp_path: Path):
        from helia_profiler.errors import BuildError
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        assert ctx.power_binary_path is None

        with pytest.raises(BuildError, match="no power artifact"):
            FlashPowerFirmwareStage().run(ctx)

    def test_dedicated_flash_rejects_legacy_path_without_artifact(self, tmp_path: Path):
        from helia_profiler.errors import BuildError
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        power_bin = tmp_path / "hpx_profiler_power"
        power_bin.write_bytes(b"\x00")
        ctx.power_binary_path = power_bin

        with pytest.raises(BuildError, match="no power artifact"):
            FlashPowerFirmwareStage().run(ctx)

    def test_direct_dedicated_capture_requires_deployment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.publish_power_plan(PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=5,
            count_source="configured",
        ))
        monkeypatch.setattr(
            "helia_profiler.power.get_driver",
            lambda *a, **k: self._FakeDriver([]),
        )

        with pytest.raises(PowerError, match="has not been deployed"):
            capture_power(ctx, duration_override_s=7.0)

    def test_dedicated_capture_requires_authoritative_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        _mark_power_firmware_deployed(ctx, tmp_path)
        artifact = ctx.power_run.firmware
        assert artifact is not None
        ctx.publish_power_plan(PowerRunPlan(firmware_mode="dedicated"))
        ctx.publish_power_firmware(artifact)
        ctx.publish_power_deployment(DeploymentRecord(
            firmware=artifact,
            target_id=ctx.config.target.board,
            deployed_at="2026-07-18T00:00:00+00:00",
        ))
        monkeypatch.setattr(
            "helia_profiler.power.get_driver",
            lambda *a, **k: self._FakeDriver([]),
        )

        with pytest.raises(PowerError, match="no authoritative inference count"):
            capture_power(ctx, duration_override_s=7.0)

    def test_shared_result_metadata_records_firmware(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power

        ctx = self._make_ctx(tmp_path, firmware="shared")
        calls: list[str] = []
        monkeypatch.setattr(
            "helia_profiler.power.get_driver", lambda *a, **k: self._FakeDriver(calls)
        )
        result = capture_power(ctx, duration_override_s=7.0)
        assert result.metadata.power_firmware == "shared"

    def test_power_plan_accepts_external_count_without_pmu_result(self, tmp_path: Path):
        from helia_profiler.stages.plan_power import plan_power_run

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.pmu_result = None

        plan = plan_power_run(ctx, inference_count=123)

        assert plan.inference_count == 123
        assert plan.reference_inference_us is None
        assert plan.count_source == "configured"

    def test_power_plan_derives_count_from_profile_timing(self, tmp_path: Path):
        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_avg_us=2226),
            layers=[],
        )

        plan = plan_power_run(ctx)

        assert plan.inference_count == 2247
        assert plan.reference_inference_us == 2226
        assert plan.target_duration_ms == 5000
        assert plan.count_source == "profile_guided"

    def test_busy_loop_plan_sizes_the_window_in_probe_units(self, tmp_path: Path):
        """#125: external busy_loop could never complete a run.

        The busy_loop probe runs no inferences -- the window is ONE calibrated
        spin sized from window_target_ms, and firmware reports 1 requested /
        1 completed (#112). But the plan derived N from `clean_infer_avg_us`,
        which under this probe is the WHOLE spin. With a 5 s window that gives
        `ceil(5s / 5s) = 1`, clamped up to `window_min` = 10, so the host
        expected 10 x 5 s = 50 s against a 5 s gate. `capture_gated` RAISES on
        that mismatch rather than warning, so the default `firmware:
        dedicated` path could not finish. `firmware: shared` only worked by
        accident, because both binaries spin.

        Verified against the pre-fix code: `count=10, ref_us=5,000,000`,
        expected 50.000 s vs measured 5.000 s, ratio 0.100.
        """
        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        object.__setattr__(
            ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
        )
        # What the profile pass reports under busy_loop: one spin, whole window.
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=5_000_000),
            layers=[],
        )

        plan = plan_power_run(ctx)

        assert plan.count_source == "probe_window"
        assert plan.inference_count == 1, "still sizing a spin window in inferences"
        # Against the config property the FIRMWARE render reads -- not against
        # plan.target_duration_ms, which is the plan restating itself. Review
        # found the earlier form tautological: inflating the probe target 3x
        # left every test in this file passing.
        assert plan.reference_inference_us == ctx.config.effective_window_target_ms * 1000
        assert plan.target_duration_ms == ctx.config.effective_window_target_ms

    def test_a_shared_busy_loop_gate_tolerates_boot_to_boot_spin_variation(
        self, tmp_path: Path
    ):
        """A healthy shared run must not be rejected for ordinary jitter.

        In `firmware: shared` the plan carries no count, so capture fills both
        the count and the reference from `pmu_result.meta` -- the PROFILE
        boot's spin -- and compares them against the POWER boot's gate. Two
        boots, not one measurement. The `firmware_auto` band was 0.01 on the
        claim that they were the same measurement, which only stayed harmless
        while the per-unit slack (half the whole spin) dominated it. Gating
        that slack on count > 1 exposed the 1% band, and `capture_gated`
        RAISES: two boots' spins differing 1.2% killed a healthy run.
        """
        from helia_profiler.power.diagnostics import (
            assess_gate_duration,
            gate_relative_tolerance_for,
        )

        profile_boot_spin_us = 4_980_000
        integrity = assess_gate_duration(
            measured_s=5.04,  # the power boot's gate, 1.2% longer
            clean_infer_count=1,
            clean_infer_avg_us=profile_boot_spin_us,
            stats_rate_hz=1000,
            relative_tolerance=gate_relative_tolerance_for("firmware_auto"),
        )

        assert integrity.valid, (
            f"a healthy shared run is rejected: ratio {integrity.ratio:.4f} "
            f"against a {integrity.tolerance_s:.4f}s band"
        )

    def test_shared_busy_loop_reports_the_window_the_firmware_runs(
        self, tmp_path: Path
    ):
        """`shared` produces no count, but still publishes a window length.

        `target_duration_ms` reaches summary.json verbatim, and the
        firmware-mode branch is tested before the probe branch -- so a shared
        busy_loop run used to report the counted-window goal (5000 ms) for a
        window the firmware spun for 1000 ms. The window length is a property
        of the PROBE, not of the plan's firmware mode.
        """
        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="shared")
        object.__setattr__(
            ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
        )
        object.__setattr__(ctx.config.profiling, "window_mode", WindowMode.FIXED)
        object.__setattr__(ctx.config.profiling, "window_target_ms", 1000)
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=1_000_000),
            layers=[],
        )

        plan = plan_power_run(ctx)

        assert plan.count_source == "firmware_auto"
        assert plan.inference_count is None
        assert plan.target_duration_ms == ctx.config.effective_window_target_ms == 1000

    def test_infer_probe_plan_is_unchanged(self, tmp_path: Path):
        """The default probe must keep deriving N from per-inference timing."""
        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_avg_us=2226), layers=[])

        plan = plan_power_run(ctx)

        assert plan.count_source == "profile_guided"
        assert plan.inference_count == 2247
        assert plan.reference_inference_us == 2226

    def test_busy_loop_refuses_an_explicit_count_it_cannot_honour(
        self, tmp_path: Path
    ):
        """An N the firmware ignores must not become a plan.

        The busy_loop window runs exactly one calibrated spin whatever the
        host asked for, so a `configured` plan of N spins describes a window
        that cannot happen -- and every consumer computing `count x
        reference_us` would then disagree with the real window by N, which is
        the spurious-mismatch shape #112 removed. Reachable through the public
        `plan_power_run(ctx, inference_count=...)` API; the shipping pipeline
        constructs `PlanPowerRunStage()` with no count.
        """
        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        object.__setattr__(
            ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
        )
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=5_000_000),
            layers=[],
        )

        with pytest.raises(PowerError, match="runs no inferences"):
            plan_power_run(ctx, inference_count=50)

    def test_the_gate_band_follows_the_probe_not_the_firmware_mode(self):
        """The same probe must get the same band in both firmware modes.

        This band was first keyed on `plan.count_source`, which structurally
        cannot name "busy_loop on a shared binary": count_source is
        `probe_window` under `dedicated` but `firmware_auto` under `shared`.
        So the shared case -- which has MORE error, being two independent
        per-boot calibrations rather than one -- got the tighter band, on a
        check that RAISES. A spin 11% off target raised on shared and passed
        on dedicated (found by review).
        """
        from helia_profiler.power.diagnostics import (
            COUNTED_WINDOW_TOLERANCE,
            PREDICTED_WINDOW_TOLERANCE,
            gate_relative_tolerance_for,
        )

        assert gate_relative_tolerance_for("busy_loop") == PREDICTED_WINDOW_TOLERANCE
        assert gate_relative_tolerance_for("infer") == COUNTED_WINDOW_TOLERANCE
        # A predicted window is never bounded more tightly than a counted one:
        # its length was calibrated, not counted.
        assert PREDICTED_WINDOW_TOLERANCE > COUNTED_WINDOW_TOLERANCE

    def test_a_shared_and_a_dedicated_busy_loop_run_get_the_same_band(
        self, tmp_path: Path
    ):
        """Driven through the real plan, so the count_source difference is real."""
        from helia_profiler.power.diagnostics import gate_relative_tolerance_for
        from helia_profiler.results import FirmwareMeta, PmuResult
        from helia_profiler.stages.plan_power import plan_power_run

        bands = {}
        for firmware in ("dedicated", "shared"):
            ctx = self._make_ctx(tmp_path, firmware=firmware)
            object.__setattr__(
                ctx.config.profiling, "clean_window_probe", CleanWindowProbe.BUSY_LOOP
            )
            ctx.pmu_result = PmuResult(
                meta=FirmwareMeta(clean_infer_count=1, clean_infer_avg_us=5_000_000),
                layers=[],
            )
            plan = plan_power_run(ctx)
            bands[firmware] = (
                plan.count_source,
                gate_relative_tolerance_for(ctx.config.profiling.clean_window_probe),
            )

        assert bands["dedicated"][0] != bands["shared"][0], "count_source differs"
        assert bands["dedicated"][1] == bands["shared"][1], (
            f"same probe, different band: {bands}"
        )

    def test_a_mis_sized_spin_is_not_absorbed_by_the_per_unit_slack(self):
        """The gate check must actually bound a one-unit window.

        `assess_gate_duration` allows half of one unit of work for a window
        that ends part-way through a unit. When there is only ONE unit that
        term is half the entire measurement, so it swamped every other bound
        and left a +/-50% check: a spin 40% short of target read as valid.
        A busy_loop plan is exactly that shape.
        """
        from helia_profiler.power.diagnostics import (
            assess_gate_duration,
            gate_relative_tolerance_for,
        )

        # One unit lasting 5 s -- the plan a busy_loop run produces.
        def assess(measured_s: float):
            return assess_gate_duration(
                measured_s=measured_s,
                clean_infer_count=1,
                clean_infer_avg_us=5_000_000,
                stats_rate_hz=1000,
                relative_tolerance=gate_relative_tolerance_for("busy_loop"),
            )

        assert assess(5.0).valid, "a perfect run must pass"
        assert assess(4.0).valid, "a 20% miscalibrated spin is still usable"
        assert not assess(3.0).valid, "a 40% short window must be caught"
        assert not assess(0.006).valid, "the calibration-fallback shape must be caught"


    def test_shared_infer_reports_the_window_the_firmware_was_built_with(
        self, tmp_path: Path
    ):
        """`shared` never re-renders the binary, so the host's floor is fiction.

        The unconditional power floor exists to size N on a DEDICATED binary,
        which is then rebuilt with clean_iters=N. Nothing is rebuilt in shared
        mode: in `window_mode: fixed` the firmware runs exactly
        `profiling.iterations`, and neither the 5000 ms floor NOR the
        configured target has anything to do with how long that takes.

        The first fix here reported the configured target (1000 ms), which the
        window matrix then showed was wrong by up to 90x -- this test asserted
        it, so the test was wrong too. See
        tests/contracts/test_window_matrix.py, which enumerates every cell
        rather than the one the fix happened to look at.
        """
        from helia_profiler.results import FirmwareMeta, PmuResult
        from helia_profiler.stages.plan_power import plan_power_run

        ctx = self._make_ctx(tmp_path, firmware="shared")
        object.__setattr__(ctx.config.profiling, "window_mode", WindowMode.FIXED)
        object.__setattr__(ctx.config.profiling, "window_target_ms", 1000)
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_avg_us=2226), layers=[]
        )

        plan = plan_power_run(ctx)

        assert plan.inference_count is None, "shared plans no count"
        # 100 iterations x 2226 us = 222.6 ms, to the nearest millisecond --
        # not the 1000 ms target and not the 5000 ms floor.
        assert plan.target_duration_ms == 223

    def test_dedicated_infer_keeps_the_power_floor_that_sizes_n(
        self, tmp_path: Path
    ):
        """And the one case that DOES pick N keeps its floor.

        Here the goal is the window: N is derived from it and the binary is
        rebuilt with clean_iters=N, so raising a 1 s target to the 5 s power
        floor is what guarantees an integrable window.
        """
        from helia_profiler.results import FirmwareMeta, PmuResult
        from helia_profiler.stages.plan_power import plan_power_run

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        object.__setattr__(ctx.config.profiling, "window_mode", WindowMode.FIXED)
        object.__setattr__(ctx.config.profiling, "window_target_ms", 1000)
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_avg_us=2226), layers=[]
        )

        plan = plan_power_run(ctx)

        assert plan.count_source == "profile_guided"
        assert plan.target_duration_ms == 5000
        assert plan.inference_count == 2247

    def test_power_plan_flags_a_stalled_profile_reference(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """A stalled clean window contaminates the window sizing too (#121).

        ``clean_infer_avg_us`` reads low by the stalled fraction, so the
        derived N comes out short by the same factor. (``report/summary.py``'s
        ``active_window_estimated_*`` are NOT affected -- they come from
        ``profiled_infer_total_us``.) The count is deliberately still
        derived (dropping to ``firmware_auto`` would make
        ``BuildPowerFirmwareStage`` skip the fixed-N build and change what runs
        on the bench), so the contamination has to be stated rather than
        silently absorbed.
        """
        import logging

        from helia_profiler.stages.plan_power import plan_power_run
        from helia_profiler.results import FirmwareMeta, PmuResult

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(
                clean_infer_avg_us=2226,
                clean_infer_count=1092,
                clean_stalled_iters=233,
                clean_partial_iters=0,
            ),
            layers=[],
        )

        with caplog.at_level(logging.WARNING, logger="hpx"):
            plan = plan_power_run(ctx)

        assert plan.count_source == "profile_guided"
        warnings = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ]
        assert any("stalled clean-window reference" in message for message in warnings), (
            f"power plan sized from a stalled reference without saying so: {warnings}"
        )

        # The healthy case is asserted here rather than as its own test: a lone
        # "no warning" assertion also passes against a build that never checks,
        # so on its own it would guard nothing.
        caplog.clear()
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(
                clean_infer_avg_us=2226,
                clean_infer_count=1092,
                clean_stalled_iters=0,
                clean_partial_iters=0,
            ),
            layers=[],
        )
        with caplog.at_level(logging.WARNING, logger="hpx"):
            plan_power_run(ctx)
        assert not any(
            "stalled clean-window reference" in record.getMessage()
            for record in caplog.records
        )

        # A pure-partial stall must report a real magnitude. The bound used to
        # be frozen-only, so this case printed "reads at least ~0.0% low" in a
        # sentence that then said "short by about the same factor".
        caplog.clear()
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(
                clean_infer_avg_us=2226,
                clean_infer_count=1091,
                clean_stalled_iters=0,
                clean_partial_iters=1091,
            ),
            layers=[],
        )
        with caplog.at_level(logging.WARNING, logger="hpx"):
            plan_power_run(ctx)
        partial_msgs = [
            r.getMessage()
            for r in caplog.records
            if "stalled clean-window reference" in r.getMessage()
        ]
        assert partial_msgs, "pure-partial stall was not flagged at all"
        assert "~0.0%" not in partial_msgs[0], (
            f"pure-partial stall reported a zero magnitude: {partial_msgs[0]}"
        )

    def test_power_build_replaces_stale_output_and_publishes_artifact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.stages.build_power_firmware import BuildPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.firmware_dir = tmp_path / "app"
        ctx.dependency_workspace = object()  # type: ignore[assignment]
        ctx.build_dir = ctx.firmware_dir / "build" / "apollo510_evb"
        ctx.build_dir.mkdir(parents=True)
        stale_binary = ctx.build_dir / "hpx_profiler_power"
        stale_binary.write_bytes(b"stale")
        stale_artifact = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=ctx.firmware_dir,
            build_dir=ctx.build_dir,
            binary_path=stale_binary,
        )
        ctx.publish_power_plan(PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=123,
            reference_inference_us=1000,
            count_source="profile_guided",
        ))
        ctx.publish_power_firmware(stale_artifact)
        ctx.publish_power_deployment(DeploymentRecord(
            firmware=stale_artifact,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        ))
        rendered: list[int] = []
        build_calls: list[dict] = []

        monkeypatch.setattr(
            "helia_profiler.firmware.render_power_source",
            lambda _ctx, *, inference_count: rendered.append(inference_count),
        )

        def fake_build(*args, **kwargs):
            assert not stale_binary.exists()
            build_calls.append({"args": args, **kwargs})
            stale_binary.write_bytes(b"fresh")

        monkeypatch.setattr("helia_profiler.nsx.build", fake_build)
        monkeypatch.setattr(
            "helia_profiler.dependencies.workspace_mutex",
            lambda _workspace: __import__("contextlib").nullcontext(),
        )

        BuildPowerFirmwareStage().run(ctx)

        assert rendered == [123]
        assert build_calls[0]["target"] == "hpx_profiler_power"
        assert ctx.power_binary_path == stale_binary
        assert ctx.power_firmware is not None
        assert ctx.power_firmware.binary_path == stale_binary
        assert stale_binary.read_bytes() == b"fresh"
        assert ctx.deployed_power_firmware is None
        assert ctx.power_run is not None
        assert ctx.power_run.deployment is None

    def test_failed_power_rebuild_invalidates_prior_artifact_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.errors import BuildError
        from helia_profiler.power.base import PowerResult, PowerSummary
        from helia_profiler.stages.build_power_firmware import BuildPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware="dedicated")
        ctx.firmware_dir = tmp_path / "app"
        ctx.dependency_workspace = object()  # type: ignore[assignment]
        ctx.build_dir = ctx.firmware_dir / "build" / "apollo510_evb"
        ctx.build_dir.mkdir(parents=True)
        binary = ctx.build_dir / "hpx_profiler_power"
        binary.write_bytes(b"old")
        artifact = FirmwareArtifact(
            role="power",
            target_name="hpx_profiler_power",
            app_dir=ctx.firmware_dir,
            build_dir=ctx.build_dir,
            binary_path=binary,
        )
        ctx.publish_power_plan(PowerRunPlan(
            firmware_mode="dedicated",
            inference_count=12,
            count_source="configured",
        ))
        ctx.publish_power_firmware(artifact)
        ctx.publish_power_deployment(DeploymentRecord(
            firmware=artifact,
            target_id="apollo510_evb",
            deployed_at="2026-07-18T00:00:00+00:00",
        ))
        ctx.power_result = PowerResult(
            summary=PowerSummary(0.01, 0.02, 0.03, 0.04, 1.0, 10)
        )
        ctx.power_binary_path = binary

        monkeypatch.setattr(
            "helia_profiler.firmware.render_power_source",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "helia_profiler.nsx.build",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(BuildError("compile failed")),
        )
        monkeypatch.setattr(
            "helia_profiler.dependencies.workspace_mutex",
            lambda _workspace: __import__("contextlib").nullcontext(),
        )

        with pytest.raises(BuildError, match="compile failed"):
            BuildPowerFirmwareStage().run(ctx)

        assert not binary.exists()
        assert ctx.power_run is not None
        assert ctx.power_run.firmware is None
        assert ctx.power_run.deployment is None
        assert ctx.power_run.observation is None
        assert ctx.power_firmware is None
        assert ctx.deployed_power_firmware is None
        assert ctx.power_binary_path is None
        assert ctx.power_result is None

    def test_shared_power_plan_does_not_claim_unbuilt_fixed_count(self, tmp_path: Path):
        from helia_profiler.results import FirmwareMeta, PmuResult
        from helia_profiler.stages.plan_power import plan_power_run

        ctx = self._make_ctx(tmp_path, firmware="shared")
        ctx.pmu_result = PmuResult(
            meta=FirmwareMeta(clean_infer_avg_us=2226),
            layers=[],
        )

        plan = plan_power_run(ctx)

        assert plan.inference_count is None
        assert plan.count_source == "firmware_auto"

    def test_power_plan_rejects_driver_mode_mismatch(self, tmp_path: Path):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.plan_power import PlanPowerRunStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "joulescope",
                    "mode": "internal",
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)

        with pytest.raises(PowerError, match="uses mode 'external'"):
            PlanPowerRunStage().run(ctx)

    def test_internal_power_rejects_driver_without_firmware_producer(
        self, tmp_path: Path
    ):
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.stages.plan_power import PlanPowerRunStage

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {
                    "enabled": True,
                    "driver": "ondevice",
                    "mode": "internal",
                },
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)

        with pytest.raises(PowerError, match="no firmware-side measurement producer"):
            PlanPowerRunStage().run(ctx)

    @pytest.mark.parametrize("enabled,firmware", [(False, "dedicated"), (True, "shared")])
    def test_power_flash_stage_skips_without_dedicated_run(
        self, tmp_path: Path, enabled: bool, firmware: str
    ):
        from dataclasses import replace
        from helia_profiler.stages.flash_power import FlashPowerFirmwareStage

        ctx = self._make_ctx(tmp_path, firmware=firmware)
        if not enabled:
            ctx.config = replace(ctx.config, power=replace(ctx.config.power, enabled=False))

        assert FlashPowerFirmwareStage().should_skip(ctx) is True


class TestGatedCaptureCapabilityDetection:
    """``capture_power`` selects the gated path via ``supports_gated_capture``.

    Any driver — built-in or registered via ``register_driver`` — that sets
    ``supports_gated_capture = True`` and implements a working
    ``capture_gated`` gets the gated path automatically; the decision is no
    longer a hardcoded driver-name allowlist.
    """

    def test_custom_registered_driver_with_gated_capture_uses_gated_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)
        calls: list[str] = []

        class CustomGatedDriver:
            """A third-party driver registered via ``register_driver``."""

            supports_gated_capture = True

            def __init__(self, *, serial: str | None = None) -> None:
                del serial

            def check_available(self) -> None:
                calls.append("check")

            def capture(self, **kwargs):  # pragma: no cover - unreachable
                raise AssertionError("ungated capture should not be reached")

            def capture_gated(self, **kwargs):
                calls.append("capture_gated")
                return PowerResult(
                    summary=summary, metadata=PowerMetadata(measurement_scope="custom_gated")
                )

        register_driver("custom-gated-test-driver", CustomGatedDriver)

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True, "driver": "custom-gated-test-driver"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=5), layers=[])
        _mark_power_firmware_deployed(ctx, tmp_path)

        result = capture_power(ctx, duration_override_s=3.0)

        assert "capture_gated" in calls
        assert "check" in calls
        assert result.metadata.measurement_scope == "custom_gated"

    def test_driver_without_capability_flag_uses_ungated_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A driver that doesn't set ``supports_gated_capture`` (even if it
        happens to implement ``capture_gated``) is treated as ungated —
        matches the ``getattr(..., False)`` default at the call site.
        """
        from helia_profiler.capture import capture_power
        from helia_profiler.config import load_config
        from helia_profiler.pipeline import PipelineContext
        from helia_profiler.results import FirmwareMeta, PmuResult

        summary = PowerSummary(0.01, 0.02, 0.03, 0.04, 0.05, 6)
        calls: list[str] = []

        class UngatedDriver:
            def check_available(self) -> None:
                calls.append("check")

            def capture(self, **kwargs):
                calls.append("capture")
                return PowerResult(summary=summary, metadata=PowerMetadata(measurement_scope="ungated"))

            def capture_gated(self, **kwargs):  # pragma: no cover - unreachable
                raise AssertionError("gated capture should not be reached")

        monkeypatch.setattr("helia_profiler.power.get_driver", lambda *a, **k: UngatedDriver())

        model = tmp_path / "model.tflite"
        model.write_bytes(b"\x00")
        config = load_config(
            None,
            {
                "model": {"path": str(model)},
                "engine": {"type": "helia-rt"},
                "power": {"enabled": True, "driver": "joulescope"},
            },
        )
        ctx = PipelineContext(config=config, work_dir=tmp_path)
        ctx.pmu_result = PmuResult(meta=FirmwareMeta(clean_infer_count=5), layers=[])
        _mark_power_firmware_deployed(ctx, tmp_path)

        result = capture_power(ctx, duration_override_s=3.0)

        assert calls == ["check", "capture"]
        assert result.metadata.measurement_scope == "ungated"


def test_clean_window_probe_classification_is_exhaustive():
    """#139 item 4: _NON_INFERENCE_PROBES is a hand-kept literal (the hard
    circular import keeps power.diagnostics below config), so nothing forced
    a new CleanWindowProbe member to get a classification decision. This
    table IS that decision: extending the enum fails here until the new
    probe is classified — and the classifier is checked against it."""
    from helia_profiler.config import CleanWindowProbe
    from helia_profiler.power.diagnostics import (
        _NON_INFERENCE_PROBES,
        probe_runs_inferences,
    )

    assert _NON_INFERENCE_PROBES <= {p.value for p in CleanWindowProbe}
    classified = {
        CleanWindowProbe.INFER: True,
        CleanWindowProbe.BUSY_LOOP: False,
    }
    assert set(classified) == set(CleanWindowProbe)
    for probe, runs_inferences in classified.items():
        assert probe_runs_inferences(probe) is runs_inferences


def test_plan_power_progress_message_is_probe_aware(tmp_path, monkeypatch):
    """#172 review: the seventh 'inferences' site — 'Power run planned ·
    1 inferences' for a busy-loop plan that runs none."""
    from helia_profiler.config import load_config
    from helia_profiler.pipeline import PipelineContext
    from helia_profiler.stages.plan_power import PlanPowerRunStage

    model = tmp_path / "model.tflite"
    model.write_bytes(b"\x00")
    config = load_config(
        None,
        {
            "model": {"path": str(model)},
            "engine": {"type": "helia-rt"},
            "profiling": {"clean_window_probe": "busy_loop"},
            "power": {"enabled": True},
        },
    )
    ctx = PipelineContext(config=config, work_dir=tmp_path)
    messages: list[str] = []
    ctx.progress_sink = lambda update: messages.append(update.message)

    PlanPowerRunStage().run(ctx)

    planned = next(m for m in messages if m.startswith("Power run planned"))
    assert "1 busy-loop pass" in planned
    assert "inference" not in planned
