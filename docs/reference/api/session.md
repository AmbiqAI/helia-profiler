# Interactive sessions

::: helia_profiler.Session

::: helia_profiler.examples
    options:
      members:
        - tiny_cnn

Create a session, retain typed results, and render them as Rich tables when
working in a terminal, IPython, or notebook:

```python
import helia_profiler as hpx

session = hpx.Session().with_target(board="apollo510_evb")

doctor = session.show(session.doctor())
probes = session.show(session.probes())
matches = session.show(session.inspect_probes())
```

`Session.show()` returns the original value unchanged, so pretty-printing does
not replace the typed result or prevent later programmatic use.

## Persisting intent

Session snapshots store unresolved user intent, not environment-derived
defaults. Relative model and module paths remain relative strings:

```python
session = (
    hpx.Session()
    .with_model("models/model.tflite")
    .with_target(board="apollo510_evb")
)
session.save("experiment.session.json")

restored = hpx.Session.load("experiment.session.json")
assert restored.intent_dict() == session.intent_dict()
```

Use `resolved_dict()` when a fully validated configuration snapshot is needed
for inspection or provenance. Loading a resolved snapshot as intent is
deliberately not automatic because doing so would freeze defaults that may be
board-, environment-, or version-dependent.

`Session.profile()` and the top-level `profile()` function are silent by
default. Pass `progress_sink=updates.append` to receive typed `ProgressUpdate`
events without enabling terminal presentation. The `hpx` CLI owns Rich progress
and final result rendering.

The versioned envelope is described by the packaged permissive schema
`helia_profiler/data/session_intent.schema.v1.json`.

For a newcomer-oriented walkthrough of discovery, immutable experiment
branches, profiling, filtering, comparisons, overlays, and power, see
[Interactive Python](../../examples/interactive-python.md).

## Interactive result types

::: helia_profiler.ModelAnalysis

::: helia_profiler.CompareResult

::: helia_profiler.DoctorCheck

::: helia_profiler.DoctorResult

::: helia_profiler.BoardDef

::: helia_profiler.PmuCounter

::: helia_profiler.JLinkProbe

::: helia_profiler.JLinkProbeMatch

::: helia_profiler.SerialPortInfo
