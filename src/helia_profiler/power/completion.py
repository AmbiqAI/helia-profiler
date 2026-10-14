"""Control-plane completion tracking; retained counter streams remain authoritative."""

from dataclasses import dataclass

from .counter_gate import CounterGateTrace


@dataclass(frozen=True)
class CounterCompletion:
    ready: int
    acknowledge: int
    rise: int
    fall: int
    done: int


def resolve_counter_completion(
    gate: CounterGateTrace, state: CounterGateTrace
) -> CounterCompletion:
    """Resolve one complete firmware transaction, never a ranked or last gate.

    Reset may produce pin activity and READY may be abandoned before measurement.
    Once an acknowledged transaction raises its gate, every transition belongs
    to that transaction until DONE. A second transaction is ambiguous.
    """
    if gate.rate != state.rate:
        raise ValueError("Completion stream counter rates differ")
    start = max(gate.first_counter, state.first_counter)
    end = min(gate.end_counter, state.end_counter, gate.intervals[-1][1])
    candidates = []
    for index, (ready, level) in enumerate(state.edges):
        if level != 1 or ready <= start:
            continue
        prior_gate = [(counter, value) for counter, value in gate.edges if counter <= ready]
        idle, gate_level = (
            prior_gate[-1] if prior_gate else (gate.first_counter, gate.initial_level)
        )
        if gate_level or idle >= ready:
            continue
        following = state.edges[index + 1 : index + 3]
        if not following:
            continue
        acknowledge = following[0][0]
        done = following[1][0] if len(following) == 2 else gate.end_counter
        transitions = [(counter, value) for counter, value in gate.edges if ready < counter <= done]
        if not transitions:
            continue
        if (
            len(following) != 2
            or [value for _, value in following] != [0, 1]
            or [value for _, value in transitions] != [1, 0]
        ):
            raise ValueError("Incomplete or fragmented batch transaction")
        rise, fall = (counter for counter, _ in transitions)
        if not ready < acknowledge <= rise < fall <= done < end:
            raise ValueError("Completion transaction order or coverage mismatch")
        gate.window_evidence(rise, fall)
        candidates.append(CounterCompletion(ready, acknowledge, rise, fall, done))
    if len(candidates) != 1:
        raise ValueError("Missing or ambiguous complete batch transaction")
    result = candidates[0]
    if (
        gate.final_level != 0
        or state.final_level != 1
        or gate.edges[-1] != (result.fall, 0)
        or state.edges[-1] != (result.done, 1)
    ):
        raise ValueError("Transitions after completed batch transaction")
    return result


@dataclass
class GatedCompletion:
    acknowledged: bool = False
    gate_seen: bool = False

    def observe(self, *, state_high: bool, gate_high: bool, go_signaled: bool) -> bool:
        if not go_signaled:
            self.acknowledged = self.gate_seen = False
            return False
        if not state_high:
            self.acknowledged = True
        if self.acknowledged and gate_high:
            self.gate_seen = True
        return self.acknowledged and self.gate_seen and state_high and not gate_high
