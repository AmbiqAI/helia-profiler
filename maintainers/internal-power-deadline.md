# Internal power terminal deadline

The internal INA228 collector must wait for stalled firmware to complete a valid
measurement window ([#302](https://github.com/AmbiqAI/helia-profiler/issues/302)).
`CollectPowerTerminalStage.run` keeps its legacy 5–30 second wait as a floor.

For a known planned window `W`, an inference probe and usable reference stall
factor `f > 1`, the wait is `max(legacy, 2 * W * f + 5)` seconds. Capture and the
terminal stage share `power/clean_window.py`'s factor, capped at ten. Missing or
inconsistent evidence and busy-loop plans keep the legacy wait. External
post-observation collection is unchanged; all waits retain monotonic deadlines,
terminal errors and transport teardown.

A ten-second plan with four of five reference iterations frozen has factor five:
its deadline is 105 seconds instead of 25. A terminal at 50 seconds must fail before
and succeed after the fix; absent input must expire. Controls cover partial stalls,
the cap, no stall, unknown plans/evidence, inconsistent counts, busy-loop and
external waits, malformed records and collector teardown.
Host controls do not establish physical stall/reset behavior.
