"""Control-plane completion tracking; retained counter streams remain authoritative."""

from dataclasses import dataclass


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
