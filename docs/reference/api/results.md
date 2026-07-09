# Results

The typed data returned by [`profile()`](profile.md). All result types are
frozen dataclasses — see the [architecture overview](../../architecture/index.md)
for why bare dicts aren't used between pipeline stages (the one exception is
`LayerResult.counters`, whose keys are dynamic PMU counter names).

::: helia_profiler.ProfileResult

::: helia_profiler.PmuResult

::: helia_profiler.PresetResult

::: helia_profiler.LayerResult

::: helia_profiler.FirmwareMeta

::: helia_profiler.RunMetadata

::: helia_profiler.NsxModuleRef

::: helia_profiler.PowerResult
