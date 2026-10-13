# Internal power terminal deadline

Internal INA228 collection accounts for reference clock stalls
([#302](https://github.com/AmbiqAI/helia-profiler/issues/302)), retaining its legacy 5–30 second floor.

For a known window `W`, inference probe and usable factor `f > 1`, wait
`max(legacy, 2 * W * f + 5)` seconds. Capture and collection share the factor in
`power/clean_window.py`, capped at ten. Missing/inconsistent evidence, busy-loop
and external waits retain their behavior, as do terminal errors and teardown.

A ten-second plan with four of five iterations frozen selects 105 seconds rather
than 25. A terminal at 50 seconds must fail before and pass after; absent input
must expire. Host controls do not establish physical stall/reset behavior.
