# Proposed internal power terminal deadline

The internal INA228 collector can finish its current host wait before stalled
firmware completes a valid measurement window. This proposal addresses the
remaining internal-terminal requirement in [#302](https://github.com/AmbiqAI/helia-profiler/issues/302).
External capture, reset recovery and previously delivered fixes are outside scope.

`CollectPowerTerminalStage.run` currently selects a 5–30 second wait from the
planned window. The clean-window assessment already estimates reference stalls
for external capture, with unavailable/inconsistent evidence falling back to one
and a maximum factor of ten. Internal use of this assessment needs an explicit
measurement-owner decision; the external contract alone does not settle it.

## Proposed rule

Keep one factor implementation in `power/clean_window.py`, consumed by capture
and the terminal stage. For a known planned window, an inference probe and usable
factor `f > 1`, wait for the larger of the legacy deadline and
`2 * planned_window_s * f + 5` seconds. Otherwise preserve the legacy deadline.
Busy-loop measurements and the external post-observation wait remain unchanged.
Every branch retains a finite monotonic deadline and the existing terminal errors.

## Acceptance

For a ten-second window with four of five reference windows stalled, the factor
is five: the proposed deadline is 105 seconds rather than 25. A deterministic
terminal arriving between those bounds must fail before and succeed after the
change. Missing-terminal input must still expire; no-stall, unavailable, partial,
inconsistent and busy-loop controls must preserve their selected contract.
Transport teardown and malformed-terminal diagnostics must remain unchanged.

Implementation is limited to the owning stage, clean-window factor and capture
consumer, focused tests and the existing capture-timeline contract. Before code,
accept the internal policy and confirm permitted named host-validation boundaries.
Host controls do not establish physical stall/reset behavior. No device recovery,
benchmark campaign, driver installation or broader power protocol is proposed.
