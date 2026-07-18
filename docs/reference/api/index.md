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

## Stability tiers

Every package-root export is classified in `helia_profiler.__api_stability__`:

- `stable` — intended 1.0 contracts. Breaking changes require deprecation or a
    major version.
- `experimental` — public and documented, but still evolving before 1.0. Pin an
    HPX version when using these types in automation.
- `implementation` — currently available because typed Session discovery or
    configuration surfaces expose them, but they are not an endorsed extension
    boundary. Prefer `Session` and high-level configuration methods.

The registry is descriptive during the alpha period; it does not emit runtime
warnings or remove existing symbols.

CI currently guards complete tier coverage plus the core `profile()`,
`Session.profile()`, and `Session.compare()` call shapes. Broader constructor
and annotation snapshots remain a release-candidate task.

## Pages

- [Interactive sessions](session.md) — immutable, branchable configuration
- [`profile()`](profile.md) — the entry point function
- [Configuration](config.md) — `ProfileConfig` and its section classes
- [Results](results.md) — `ProfileResult` and the typed data it carries
- [Errors](errors.md) — the `HpxError` hierarchy
