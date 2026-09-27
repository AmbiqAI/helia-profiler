# Fixed-input fixture measurements

`helia_profiler.fixture.build_fixed_fixture(config, fixture, method=method,
runtime=runtime, compile=True)` composes the existing platform, engine,
memory-plan, firmware-generation and NSX build stages. It never probes or
flashes. `compile=False` prepares and renders without linking.

Supply SHA256-pinned `FixtureFile` records for model/input/reference and
`FixedFixture` tensor declarations. Model analysis verifies dimensions, indices,
INT8 type and per-tensor quantization, then derives the used operator resolver.
The supported capability is one static, stateless INT8 input and output on
Apollo510 EVB LP, ATfE, SRAM arena and MRAM weights. Unsupported graphs fail
before engine preparation. Advisory batch wildcards are accepted only when
the stored shape has batch one; firmware never resizes or rewrites the model.
No model names or fixed shapes select execution.

`FixtureMethod(FixtureTimingScope.RESTORE_AND_INVOKE)` measures a batch including
full input restoration. `INVOKE_ONLY` restores each input outside each measured
invocation and sums the timer intervals; timer quantization applies per call.
Both use `config.profiling.warmup` and `iterations`. The build identity binds
fixture, configuration, provider and timing scope. Reusing a work directory with
different intent fails. Historical measurements retain their original methods.

TFLM requires explicit `PreparedUpstreamRuntime`: a pinned archive, header root
and manifest. Strict typed ingress validates provider URLs/revisions, archive
ABI, include directories and all header hashes. Copies recheck hashes and path
containment. Provider source identities remain manifest-declared; retain the
corresponding audited source/build record. AOT accepts no upstream override and
uses the ordinary AOT adapter and its generated module. Ordinary profiler
configuration and provider defaults are unchanged.

`FixtureBuild` pins generated sources, ELF, flat image, dependency lock and map;
its runtime manifest is absent for AOT. A prepared result is not a successful
build or a numerical validation result.

`helia_profiler.fixture_capture.capture_fixture(request, guard=guard)` captures
raw evidence using existing profiler flash/probe APIs. `FixtureCaptureRequest`
binds ELF/image pins, device/serial (`AP510NFA-CBR` for the supported board), output extent, load address, evidence
directory, settle interval, timing scope and optional TFLM arena capacity.
The caller's `FixtureCaptureGuard.check(require_free=..., remaining_s=...)`
must enforce exclusive ownership and its bounded deadline. Image extents,
symbols, full readback, exact poison writes and stable halted terminal reads are
checked. Each attempt preserves started, identity, binary terminal and final
receipt artifacts; existing attempt directories are never overwritten.

Transport success establishes completion, checksum and supported clock/timer
metadata; it does **not** establish numerical acceptance. The caller binds the
request to its build receipt, compares every output byte under its policy and
checks observed iteration/warmup counts against the requested method. The
seven-word timing terminal includes firmware-reported scope, which must match
the request. Failed attempts retain available raw bytes.

The optional versioned 32-byte memory terminal reports normal TFLM allocator
use after I/O access, warmups and measured calls, outside the timed interval.
AOT planned regions are not allocator observations. Neither value is a transient
peak or minimum capacity. Stack/heap peaks and energy are unavailable here.
