# Python API

!!! warning "Alpha API"
    heliaPROFILER's Python API is pre-1.0 and may introduce breaking changes
    between releases without a deprecation period. Pin a version if you
    depend on it in automation.

The explicit API is [`profile()`](profile.md). For notebooks, IPython, and
exploratory scripts, [`Session`](session.md) provides immutable configuration
branching over the same profiling pipeline.

```python
from pathlib import Path

from helia_profiler import profile, ProfileConfig, ModelConfig, EngineConfig, EngineType

config = ProfileConfig(
    model=ModelConfig(path=Path("my_model.tflite")),
    engine=EngineConfig(type=EngineType.HELIA_RT),
)
result = profile(config)
print(f"{result.total_cycles:,.0f} total cycles across {result.layer_count} layers")
```

On failure, `profile()` raises an [`HpxError`](errors.md#helia_profiler.HpxError)
subclass — see [Errors](errors.md).

The installed package version is available as `helia_profiler.__version__`.

## Pages

- [Interactive sessions](session.md) — immutable, branchable configuration
- [`profile()`](profile.md) — the entry point function
- [Configuration](config.md) — `ProfileConfig` and its section classes
- [Results](results.md) — `ProfileResult` and the typed data it carries
- [Errors](errors.md) — the `HpxError` hierarchy
