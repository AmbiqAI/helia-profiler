# Errors

Every failure mode in heliaPROFILER raises a subclass of `HpxError`. Catch
`HpxError` for a catch-all, or a specific subclass to handle one failure
category. Most carry an optional `hint` attribute — a short, human-readable
suggestion for how to fix the problem — which is appended automatically when
the exception is formatted as a string.

::: helia_profiler.HpxError

::: helia_profiler.ConfigError

::: helia_profiler.PlatformError

::: helia_profiler.EngineError

::: helia_profiler.FirmwareError

::: helia_profiler.BuildError

::: helia_profiler.NetworkError

::: helia_profiler.CaptureError

::: helia_profiler.PowerError

::: helia_profiler.ReportError
