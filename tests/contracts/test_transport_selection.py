"""Contract: transport selection → reader dispatch.

Pins the mapping from ``config.Transport`` to the concrete reader
``capture_pmu`` constructs, and the key parameters it forwards.  This freezes
the dispatch table (currently an ``if/elif`` ladder in
``capture/__init__.py``) so that when the refactor moves it behind a
transport/probe registry the observable behaviour is identical.

The pylink / pyserial boundary is never reached: each reader is replaced by a
recorder that captures its kwargs and returns a canned, parseable stream.
"""

from __future__ import annotations

import pytest

from helia_profiler import capture as capture_pkg
from helia_profiler.capture import capture_pmu
from helia_profiler.config import Transport

from .conftest import BOARD_FOR_FAMILY, CANNED_PMU_LINES


# The authoritative set of transports the profiler supports.  Iterating the
# enum means a newly added transport forces this contract to be updated.
ALL_TRANSPORTS = [t.value for t in Transport]


@pytest.fixture()
def reader_recorder(monkeypatch):
    """Replace every per-transport reader with a kwargs recorder.

    Returns a dict mapping transport-name -> captured kwargs (populated when
    that reader is invoked).  Exactly one entry should appear per run.
    """
    calls: dict[str, dict] = {}

    def _make(name: str):
        def _reader(**kwargs):
            calls[name] = kwargs
            return list(CANNED_PMU_LINES)

        return _reader

    monkeypatch.setattr(
        "helia_profiler.transport.usb_cdc.capture_usb_output", _make("usb_cdc")
    )
    monkeypatch.setattr(
        "helia_profiler.transport.rtt.capture_rtt_output", _make("rtt")
    )
    monkeypatch.setattr(
        "helia_profiler.transport.swo.capture_swo_output", _make("swo")
    )
    monkeypatch.setattr(
        "helia_profiler.transport.uart.capture_uart_output", _make("uart")
    )
    return calls


def test_enum_membership_is_frozen():
    """The transports this contract covers must equal the config enum.

    If someone adds/removes a Transport, this fails first with a clear list —
    the reader dispatch contract below must then be extended.
    """
    assert set(ALL_TRANSPORTS) == {"rtt", "usb_cdc", "swo", "uart"}


@pytest.mark.parametrize("transport", ALL_TRANSPORTS)
def test_transport_dispatches_to_exactly_one_reader(
    transport, reader_recorder, pmu_ctx_factory
):
    ctx = pmu_ctx_factory(board="apollo510_evb", transport=transport)

    result = capture_pmu(ctx)

    # Exactly the reader for this transport ran — no other path was taken.
    assert set(reader_recorder) == {transport}, (
        f"transport {transport!r} dispatched to {sorted(reader_recorder)}"
    )
    # The canned stream parsed into the expected single layer.
    assert result.layers[0].cycles == 1


def test_rtt_reader_receives_soc_scan_ranges_and_device(reader_recorder, pmu_ctx_factory):
    ctx = pmu_ctx_factory(board="apollo510_evb", transport="rtt")
    capture_pmu(ctx)
    kwargs = reader_recorder["rtt"]
    assert kwargs["jlink_device"] == ctx.soc.jlink_device
    assert kwargs["rtt_scan_ranges"] == ctx.soc.rtt_scan_ranges
    assert kwargs["weights_region"] == "mram"
    # RTT never holds the probe attached — it resets and re-attaches.
    assert "keep_attached" not in kwargs


def test_swo_reader_receives_resolved_cpu_clock(reader_recorder, pmu_ctx_factory):
    ctx = pmu_ctx_factory(board="apollo510_evb", transport="swo")
    capture_pmu(ctx)
    kwargs = reader_recorder["swo"]
    assert kwargs["jlink_device"] == ctx.soc.jlink_device
    # SWO baud is derived from the resolved trace/CPU clock — never 0/guessed.
    assert kwargs["cpu_freq"] > 0
    assert kwargs["cpu_freq"] == ctx.run_metadata.platform.cpu_clock_mhz * 1_000_000
    assert "keep_attached" not in kwargs


def test_uart_reader_receives_device_and_keep_attached(reader_recorder, pmu_ctx_factory):
    ctx = pmu_ctx_factory(board="apollo510_evb", transport="uart")
    capture_pmu(ctx)
    kwargs = reader_recorder["uart"]
    assert kwargs["jlink_device"] == ctx.soc.jlink_device
    assert "keep_attached" in kwargs


def test_usb_reader_receives_marker_and_keep_attached(reader_recorder, pmu_ctx_factory):
    ctx = pmu_ctx_factory(board="apollo510_evb", transport="usb_cdc")
    capture_pmu(ctx)
    kwargs = reader_recorder["usb_cdc"]
    assert kwargs["jlink_device"] == ctx.soc.jlink_device
    assert "usb_marker" in kwargs
    assert "keep_attached" in kwargs


@pytest.mark.parametrize(
    "family,board", sorted(BOARD_FOR_FAMILY.items())
)
@pytest.mark.parametrize("transport", ["uart", "usb_cdc"])
def test_keep_attached_tracks_soc_debug_domain(
    family, board, transport, reader_recorder, pmu_ctx_factory
):
    """keep_attached is driven by the SoC, not the transport name.

    The Cortex-M4F families (AP3/AP4) gate DWT->CYCCNT behind the debug power
    domain, so the UART/USB readers must hold the probe attached; AP5 (Armv8-M
    PMU) does not, and releases the probe.  This is the invariant that lets the
    refactor turn ``requires_attached_probe_for_cycles`` into a capability.
    """
    ctx = pmu_ctx_factory(board=board, transport=transport)
    capture_pmu(ctx)
    kwargs = reader_recorder[transport]
    assert kwargs["keep_attached"] is ctx.soc.requires_attached_probe_for_cycles


def test_capture_pmu_requires_resolved_jlink_device(pmu_ctx_factory):
    """A missing J-Link device string is a hard error, never a silent guess."""
    from helia_profiler.errors import CaptureError

    ctx = pmu_ctx_factory(board="apollo510_evb", transport="rtt")
    ctx.soc = None
    with pytest.raises(CaptureError):
        capture_pmu(ctx)


def test_capture_package_exposes_capture_pmu():
    """capture_pmu stays the public capture entry point used by stage 6."""
    assert hasattr(capture_pkg, "capture_pmu")
