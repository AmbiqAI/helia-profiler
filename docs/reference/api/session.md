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
