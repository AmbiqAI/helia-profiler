"""Pure completion protocol controls; no instrument or compiler imports."""

from dataclasses import replace
from hashlib import sha256

import pytest
from test_fixture_power import measured, request, state_frames

from helia_profiler._files import FixtureFile
from helia_profiler.fixture_power import _batch_completion
from helia_profiler.power.completion import GatedCompletion, resolve_counter_completion
from helia_profiler.power.counter_gate import CounterGateTrace


def test_gate_fall_is_not_done_and_ready_is_not_done():
    completion = GatedCompletion()
    for state, gate, go in [(False, False, False), (True, False, False), (True, False, True)]:
        assert not completion.observe(state_high=state, gate_high=gate, go_signaled=go)
    assert not completion.observe(state_high=False, gate_high=False, go_signaled=True)
    assert not completion.observe(state_high=False, gate_high=True, go_signaled=True)
    assert not completion.observe(state_high=False, gate_high=False, go_signaled=True)
    assert completion.observe(state_high=True, gate_high=False, go_signaled=True)


@pytest.mark.parametrize(
    "edges",
    [
        (),  # GO timeout / no READY
        ((250, 1),),  # READY only
        ((250, 1), (500, 0)),  # missing DONE
        ((250, 1), (500, 0), (5002000, 1)),  # DONE beyond retained samples
        ((250, 1), (500, 0), (1000, 1)),  # DONE before gate ends
        ((250, 1), (500, 0), (2000, 1), (3000, 0), (5001500, 1)),  # in-batch state
    ],
)
def test_incomplete_or_malformed_state_never_binds_full_batch(tmp_path, edges):
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(b"retained raw")
    raw = FixtureFile(raw_path, sha256(raw_path.read_bytes()).hexdigest())
    observation = replace(measured(), state_frames=state_frames(edges))
    with pytest.raises(ValueError):
        _batch_completion(observation, request(), raw)


def test_complete_counter_sequence_is_typed_and_bound(tmp_path):
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(b"retained raw")
    raw = FixtureFile(raw_path, sha256(raw_path.read_bytes()).hexdigest())
    result = _batch_completion(measured(), request(), raw)
    assert (result.ready_sample, result.go_ack_sample) == (250, 500)
    assert (result.gate_rise_sample, result.gate_fall_sample) == (1000, 5001000)
    assert result.done_sample == 5001500
    assert result.sample_rate == 1000000 and result.raw == raw


def test_startup_ready_without_gate_is_not_the_measured_transaction(tmp_path):
    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(b"retained raw")
    raw = FixtureFile(raw_path, sha256(raw_path.read_bytes()).hexdigest())
    observation = replace(
        measured(),
        state_frames=state_frames(((250, 1), (500, 0), (750, 1), (1000, 0), (5001500, 1))),
    )
    result = _batch_completion(observation, request(), raw)
    assert (result.ready_sample, result.go_ack_sample) == (750, 1000)
    assert (result.gate_rise_sample, result.gate_fall_sample) == (1000, 5001000)
    assert result.done_sample == 5001500


def test_gate_fragmentation_is_not_a_selected_last_window(tmp_path):
    import numpy as np

    raw_path = tmp_path / "raw.json"
    raw_path.write_bytes(b"retained raw")
    raw = FixtureFile(raw_path, sha256(raw_path.read_bytes()).hexdigest())
    observation = measured()
    frame = observation.gate_frames[0]
    levels = np.unpackbits(
        np.frombuffer(bytes.fromhex(frame["data_hex"]), dtype=np.uint8), bitorder="little"
    )
    levels[2500] = 0
    frame["data_hex"] = np.packbits(levels, bitorder="little").tobytes().hex()
    with pytest.raises(ValueError, match="fragmented"):
        _batch_completion(observation, request(), raw)


def trace(edges, *, initial=0, end=1000):
    windows = []
    rise = None
    for counter, level in edges:
        if level:
            rise = counter
        elif rise is not None:
            windows.append((rise, counter))
            rise = None
    return CounterGateTrace(
        rate=1000,
        origin=0,
        windows=tuple(windows),
        edge_resolution=1,
        intervals=tuple((i, i + 10) for i in range(0, 1000, 10)),
        clock_diagnostics={},
        edges=tuple(edges),
        initial_level=initial,
        final_level=edges[-1][1] if edges else initial,
        first_counter=0,
        end_counter=end,
    )


def test_reset_gate_noise_and_abandoned_ready_preserve_one_complete_transaction():
    gate = trace(((10, 1), (20, 0), (30, 1), (40, 0), (300, 1), (700, 0)))
    state = trace(((50, 0), (60, 1), (70, 0), (100, 1), (200, 0), (800, 1)), initial=1)
    result = resolve_counter_completion(gate, state)
    assert (result.ready, result.acknowledge, result.rise, result.fall, result.done) == (
        100,
        200,
        300,
        700,
        800,
    )


@pytest.mark.parametrize(
    "gate_edges,state_edges",
    [
        (((300, 1), (400, 0), (500, 1), (700, 0)), ((100, 1), (200, 0), (800, 1))),
        (((300, 1), (700, 0)), ((100, 1), (200, 0), (350, 1), (400, 0), (800, 1))),
        (((150, 1), (700, 0)), ((100, 1), (200, 0), (800, 1))),
        (((300, 1), (700, 0)), ((100, 1), (200, 0))),
        (((300, 1),), ((100, 1), (200, 0), (800, 1))),
        (((300, 1), (700, 0), (850, 1), (900, 0)), ((100, 1), (200, 0), (800, 1))),
        (((300, 1), (700, 0)), ((100, 1), (200, 0), (800, 1), (900, 0))),
        (((300, 1), (700, 0)), ((200, 0), (800, 1))),
    ],
    ids=(
        "false-fall",
        "in-batch-state",
        "gate-before-ack",
        "missing-done",
        "missing-fall",
        "gate-after-done",
        "state-after-done",
        "missing-ready",
    ),
)
def test_no_partial_or_fragmented_transaction_is_substituted(gate_edges, state_edges):
    with pytest.raises(ValueError):
        resolve_counter_completion(trace(gate_edges), trace(state_edges))


def test_two_complete_transactions_are_ambiguous_not_the_last_gate():
    gate = trace(((300, 1), (400, 0), (700, 1), (800, 0)))
    state = trace(((100, 1), (200, 0), (450, 1), (550, 0), (900, 1)))
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_counter_completion(gate, state)


def test_delayed_stream_callbacks_cannot_establish_completion_early():
    gate = trace(((300, 1), (700, 0)))
    for state in (
        trace(((100, 1), (200, 0)), end=750),
        trace(((100, 1), (200, 0), (800, 1)), end=800),
    ):
        with pytest.raises(ValueError):
            resolve_counter_completion(gate, state)
    state = trace(((100, 1), (200, 0), (800, 1)))
    assert resolve_counter_completion(gate, state).done == 800
