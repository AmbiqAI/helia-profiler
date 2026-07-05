"""Contract: J-Link reset ownership and ordering.

Snapshots *who* issues a target reset and *in what order* for each meaningful
(SoC family x transport x power-capture on/off) combination.  Two independent
owners exist in the baseline and this contract freezes both:

* **PMU capture** — the per-transport *reader* owns the reset.  SWO/RTT and the
  released UART/USB path use ``reset_target`` (JLinkExe ``r;g;exit`` — probe
  released, required by the AP5 secure bootloader).  The attached UART/USB path
  (AP3/AP4, whose DWT->CYCCNT lives in the debug power domain) instead uses
  ``attached_reset_session`` (pylink reset+go, probe held for the whole
  capture).

* **Power capture** — ``target_lifecycle.prepare_target_for_phase`` owns the
  reset.  The ``auto`` policy resolves to ``debug_reset`` on AP3/AP4 and to
  ``debug_reset`` **then** ``swpoi_reset`` on AP5 (the RSTGEN SWPOI deep reset
  added in the transport-hardening PR, which also clears PMU/power state).

External tools are never touched: reset primitives are monkeypatched to record
their invocation, and — for the reader paths — to stop execution immediately
after the reset (raising a ``CaptureError`` subclass the readers re-raise
verbatim) so no pylink/pyserial I/O is reached.
"""

from __future__ import annotations

import contextlib
from unittest import mock

import pytest

from helia_profiler.errors import CaptureError
from helia_profiler.target.lifecycle import (
    CapturePhase,
    ResetAction,
    prepare_target_for_phase,
)

from .conftest import BOARD_FOR_FAMILY, make_pmu_ctx


@pytest.fixture(autouse=True)
def _fake_pylink_dll(monkeypatch):
    """Make pylink.JLink construction hermetic.

    The RTT reader constructs ``pylink.JLink()`` before reaching the recorded
    reset primitive.  The constructor loads the SEGGER J-Link DLL, which
    exists on hardware benches but not on CI runners — and a contract test
    must never depend on (or touch) the real DLL either way.  The recorder
    raises ``_ResetStop`` at the reset primitive, so a permissive mock handle
    is never exercised beyond construction/attach setup.
    """
    monkeypatch.setattr("pylink.JLink", lambda *a, **k: mock.MagicMock())


class _ResetStop(CaptureError):
    """Sentinel raised right after a reader records its reset primitive.

    A ``CaptureError`` subclass so every reader re-raises it verbatim
    (``except CaptureError: raise``) instead of wrapping it — letting the test
    observe the reset without driving any downstream capture I/O.
    """


class _FakeDriver:
    """Records rail power-cycle requests; everything else is a no-op."""

    def __init__(self) -> None:
        self.power_cycle_calls: list[dict] = []

    def check_available(self) -> None:  # pragma: no cover - trivial
        pass

    def power_cycle(self, *, off_time_s: float = 0.5, settle_time_s: float = 1.0) -> None:
        self.power_cycle_calls.append({"off_time_s": off_time_s, "settle_time_s": settle_time_s})


# ---------------------------------------------------------------------------
# Recorder helpers
# ---------------------------------------------------------------------------


def _install_lifecycle_recorder(monkeypatch) -> list[str]:
    """Record ``reset_target`` / ``reset_target_poi`` calls in call order.

    ``target.lifecycle`` imports these lazily from
    ``helia_profiler.target.probe.jlink``, so patching that module namespace
    is sufficient.
    """
    events: list[str] = []

    def _debug_reset(**_kwargs) -> None:
        events.append("debug_reset")

    def _swpoi_reset(**_kwargs) -> None:
        events.append("swpoi_reset")

    monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", _debug_reset)
    monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target_poi", _swpoi_reset)
    return events


def _install_reader_reset_recorder(monkeypatch, module: str) -> list[str]:
    """Record the reset primitive a reader owns, then stop before I/O.

    Patches, in the reader's own namespace:
      * ``reset_target`` -> records ``"jlinkexe_reset"`` (probe released)
      * ``attached_reset_session`` -> records ``"pylink_attached_reset"``
        (probe held) as a context manager.
    Both raise ``_ResetStop`` so no pylink/pyserial call is reached.
    """
    events: list[str] = []

    def _reset_target(**_kwargs):
        events.append("jlinkexe_reset")
        raise _ResetStop("reset recorded")

    @contextlib.contextmanager
    def _attached(**_kwargs):
        events.append("pylink_attached_reset")
        raise _ResetStop("attached reset recorded")
        yield  # pragma: no cover - unreachable

    monkeypatch.setattr("helia_profiler.target.probe.jlink.reset_target", _reset_target)
    monkeypatch.setattr("helia_profiler.target.probe.jlink.attached_reset_session", _attached)
    return events


# ---------------------------------------------------------------------------
# Power-capture lifecycle reset sequences
# ---------------------------------------------------------------------------


class TestPowerLifecycleResetSequences:
    @pytest.mark.parametrize(
        "family,expected",
        [
            ("ap3", ["debug_reset"]),
            ("ap4", ["debug_reset"]),
            ("ap5", ["debug_reset", "swpoi_reset"]),
        ],
    )
    def test_auto_strategy_sequence_per_family(self, tmp_path, monkeypatch, family, expected):
        events = _install_lifecycle_recorder(monkeypatch)
        ctx = make_pmu_ctx(
            tmp_path, board=BOARD_FOR_FAMILY[family], power_enabled=True, reset_strategy="auto"
        )
        plan = prepare_target_for_phase(
            ctx, phase=CapturePhase.POWER, power_driver=_FakeDriver(), power_driver_name="joulescope"
        )
        assert events == expected
        # Plan metadata mirrors the executed sequence.
        if family == "ap5":
            assert plan.reset_action is ResetAction.DEBUG_RESET_THEN_SWPOI
        else:
            assert plan.reset_action is ResetAction.DEBUG_RESET

    @pytest.mark.parametrize(
        "strategy,expected_events,expected_action",
        [
            ("none", [], ResetAction.NONE),
            ("debug_reset", ["debug_reset"], ResetAction.DEBUG_RESET),
            ("swpoi_reset", ["swpoi_reset"], ResetAction.SWPOI_RESET),
            (
                "debug_reset+swpoi_reset",
                ["debug_reset", "swpoi_reset"],
                ResetAction.DEBUG_RESET_THEN_SWPOI,
            ),
        ],
    )
    def test_explicit_strategy_sequence(
        self, tmp_path, monkeypatch, strategy, expected_events, expected_action
    ):
        events = _install_lifecycle_recorder(monkeypatch)
        ctx = make_pmu_ctx(
            tmp_path, board="apollo510_evb", power_enabled=True, reset_strategy=strategy
        )
        plan = prepare_target_for_phase(
            ctx, phase=CapturePhase.POWER, power_driver=_FakeDriver(), power_driver_name="joulescope"
        )
        assert events == expected_events
        assert plan.reset_action is expected_action

    def test_swpoi_is_ap5_only_under_auto(self, tmp_path, monkeypatch):
        """The SWPOI deep reset must NOT sneak into AP3/AP4 auto policy."""
        events = _install_lifecycle_recorder(monkeypatch)
        for family in ("ap3", "ap4"):
            events.clear()
            ctx = make_pmu_ctx(
                tmp_path, board=BOARD_FOR_FAMILY[family], power_enabled=True, reset_strategy="auto"
            )
            prepare_target_for_phase(
                ctx, phase=CapturePhase.POWER, power_driver=_FakeDriver(), power_driver_name="js"
            )
            assert "swpoi_reset" not in events, family


class TestPmuPhaseHasNoLifecycleReset:
    @pytest.mark.parametrize("family", ["ap3", "ap4", "ap5"])
    def test_non_power_phase_issues_no_reset(self, tmp_path, monkeypatch, family):
        """Only the POWER phase drives a lifecycle reset; PMU/self-test do not.

        (For PMU capture, the reader owns the reset — see the reader tests.)
        """
        events = _install_lifecycle_recorder(monkeypatch)
        ctx = make_pmu_ctx(tmp_path, board=BOARD_FOR_FAMILY[family], power_enabled=True)
        plan = prepare_target_for_phase(
            ctx, phase=CapturePhase.PMU, power_driver=_FakeDriver(), power_driver_name="js"
        )
        assert events == []
        assert plan.reset_action is ResetAction.NONE
        assert plan.actions == ()


# ---------------------------------------------------------------------------
# PMU-capture reader reset ownership
# ---------------------------------------------------------------------------


class TestReaderResetOwnership:
    def test_swo_reader_uses_released_jlinkexe_reset(self, monkeypatch):
        from helia_profiler.transport.swo import capture_swo_output

        events = _install_reader_reset_recorder(
            monkeypatch, "helia_profiler.transport.swo"
        )
        with pytest.raises(_ResetStop):
            capture_swo_output(jlink_device="AP510NFA-CBR", jlink_serial="1160002204")
        assert events == ["jlinkexe_reset"]

    def test_rtt_reader_uses_released_jlinkexe_reset(self, monkeypatch):
        from helia_profiler.transport.rtt import capture_rtt_output

        events = _install_reader_reset_recorder(
            monkeypatch, "helia_profiler.transport.rtt"
        )
        # known_block_address skips the pre-clean scan so reset is the first
        # real primitive after pylink.JLink() construction.
        with pytest.raises(_ResetStop):
            capture_rtt_output(
                jlink_device="AP510NFA-CBR",
                jlink_serial="1160002204",
                rtt_scan_ranges=((0x20000000, 0x40000),),
                known_block_address=0x20088010,
            )
        assert events == ["jlinkexe_reset"]

    @pytest.mark.parametrize(
        "keep_attached,expected",
        [(False, "jlinkexe_reset"), (True, "pylink_attached_reset")],
    )
    def test_uart_reader_reset_owner_follows_keep_attached(
        self, monkeypatch, keep_attached, expected
    ):
        import helia_profiler.transport.uart as uart_reader

        events = _install_reader_reset_recorder(
            monkeypatch, "helia_profiler.transport.uart"
        )
        monkeypatch.setattr(uart_reader, "_find_jlink_vcom_port", lambda _serial: "PORT")

        class _FakeSerial:
            def __init__(self, *a, **k):
                self.is_open = True

            def reset_input_buffer(self):
                pass

            def close(self):
                self.is_open = False

        monkeypatch.setattr(uart_reader.serial, "Serial", _FakeSerial)

        with pytest.raises(_ResetStop):
            uart_reader.capture_uart_output(
                jlink_device="AMAP42KP-KBR",
                jlink_serial="1160002204",
                keep_attached=keep_attached,
            )
        assert events == [expected]

    @pytest.mark.parametrize(
        "keep_attached,expected",
        [(False, "jlinkexe_reset"), (True, "pylink_attached_reset")],
    )
    def test_usb_reader_reset_owner_follows_keep_attached(
        self, monkeypatch, keep_attached, expected
    ):
        import helia_profiler.transport.usb_cdc as usb_reader

        events = _install_reader_reset_recorder(
            monkeypatch, "helia_profiler.transport.usb_cdc"
        )
        monkeypatch.setattr(usb_reader, "_snapshot_cdc_ports", lambda: set())

        with pytest.raises(_ResetStop):
            usb_reader.capture_usb_output(
                jlink_device="AMAP42KP-KBR",
                jlink_serial="1160002204",
                keep_attached=keep_attached,
            )
        assert events == [expected]


# ---------------------------------------------------------------------------
# Full (SoC family x transport x power on/off) reset-owner snapshot
# ---------------------------------------------------------------------------

# For SWO/RTT the reader always releases the probe (JLinkExe reset).  For
# UART/USB the owner tracks the SoC debug-domain capability: AP3/AP4 hold the
# probe attached (pylink), AP5 releases it (JLinkExe).
_ATTACHED_FAMILIES = {"ap3", "ap4"}


def _expected_pmu_reset_owner(family: str, transport: str) -> str:
    if transport in ("swo", "rtt"):
        return "jlinkexe_reset"
    # uart / usb_cdc
    return "pylink_attached_reset" if family in _ATTACHED_FAMILIES else "jlinkexe_reset"


def _drive_reader_reset(monkeypatch, tmp_path, family: str, transport: str) -> list[str]:
    """Return the reset-owner label sequence the PMU reader emits for a combo."""
    module = {
        "swo": "helia_profiler.transport.swo",
        "rtt": "helia_profiler.transport.rtt",
        "uart": "helia_profiler.transport.uart",
        "usb_cdc": "helia_profiler.transport.usb_cdc",
    }[transport]
    events = _install_reader_reset_recorder(monkeypatch, module)

    ctx = make_pmu_ctx(tmp_path, board=BOARD_FOR_FAMILY[family], transport=transport)
    keep = ctx.soc.requires_attached_probe_for_cycles

    if transport == "uart":
        import helia_profiler.transport.uart as uart_reader

        monkeypatch.setattr(uart_reader, "_find_jlink_vcom_port", lambda _s: "PORT")

        class _FakeSerial:
            def __init__(self, *a, **k):
                self.is_open = True

            def reset_input_buffer(self):
                pass

            def close(self):
                self.is_open = False

        monkeypatch.setattr(uart_reader.serial, "Serial", _FakeSerial)
        with pytest.raises(_ResetStop):
            uart_reader.capture_uart_output(
                jlink_device=ctx.soc.jlink_device, jlink_serial="1", keep_attached=keep
            )
    elif transport == "usb_cdc":
        import helia_profiler.transport.usb_cdc as usb_reader

        monkeypatch.setattr(usb_reader, "_snapshot_cdc_ports", lambda: set())
        with pytest.raises(_ResetStop):
            usb_reader.capture_usb_output(
                jlink_device=ctx.soc.jlink_device, jlink_serial="1", keep_attached=keep
            )
    elif transport == "swo":
        from helia_profiler.transport.swo import capture_swo_output

        with pytest.raises(_ResetStop):
            capture_swo_output(jlink_device=ctx.soc.jlink_device, jlink_serial="1")
    else:  # rtt
        from helia_profiler.transport.rtt import capture_rtt_output

        with pytest.raises(_ResetStop):
            capture_rtt_output(
                jlink_device=ctx.soc.jlink_device,
                jlink_serial="1",
                rtt_scan_ranges=((0x20000000, 0x40000),),
                known_block_address=0x20088010,
            )
    return events


@pytest.mark.parametrize("family", ["ap3", "ap4", "ap5"])
@pytest.mark.parametrize("transport", ["rtt", "swo", "uart", "usb_cdc"])
@pytest.mark.parametrize("power_on", [False, True])
def test_reset_owner_matrix(monkeypatch, tmp_path, family, transport, power_on):
    """Full snapshot: reset owners for every meaningful capture combination.

    The complete reset-owner sequence for a run is the PMU reader's owner
    followed by (when power capture is enabled) the power lifecycle's owner(s).
    """
    pmu_owner = _drive_reader_reset(monkeypatch, tmp_path, family, transport)
    assert pmu_owner == [_expected_pmu_reset_owner(family, transport)]

    lifecycle_events = _install_lifecycle_recorder(monkeypatch)
    if power_on:
        ctx = make_pmu_ctx(
            tmp_path, board=BOARD_FOR_FAMILY[family], transport=transport,
            power_enabled=True, reset_strategy="auto",
        )
        prepare_target_for_phase(
            ctx, phase=CapturePhase.POWER, power_driver=_FakeDriver(), power_driver_name="js"
        )
        expected = (
            ["debug_reset", "swpoi_reset"] if family == "ap5" else ["debug_reset"]
        )
        assert lifecycle_events == expected
    else:
        # No power capture => no lifecycle reset is issued at all.
        assert lifecycle_events == []
