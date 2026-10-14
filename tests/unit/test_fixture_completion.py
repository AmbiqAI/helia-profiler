"""Pure completion protocol controls; no instrument or compiler imports."""

from dataclasses import replace
from hashlib import sha256

import pytest
from test_fixture_power import measured, request, state_frames

from helia_profiler.fixture_power import _batch_completion
from helia_profiler.fixture_runtime import FixtureFile
from helia_profiler.power.completion import GatedCompletion


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
        ((250, 1), (500, 0), (750, 1), (1000, 0), (5001500, 1)),  # fragmented state
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
