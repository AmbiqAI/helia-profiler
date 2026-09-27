# Fixed-input fixture integration

`helia_profiler.fixture.build_fixed_fixture(config, fixture, runtime=runtime)`
uses the existing host-only NSX pipeline. It does not power, probe, flash or
capture. The initial slice accepts one INT8 TCN input `(1, 240, 14)` and output
`(1, 240, 2)`, SRAM arena, MRAM model, LP Apollo510 EVB and ATfE. It preserves
five warmups and one 100-call batch, restoring the entire input before each call.

The caller supplies frozen `FixedFixture` and `PreparedUpstreamRuntime` records.
Model/input/expected bytes, runtime archive and manifest are SHA256 pinned.
The manifest lists include directories and every vendored header digest plus
provider URLs/revisions and the archive ABI. These source identities are
**manifest-declared**, not independently authenticated by matching the hashes.
Qualification must retain and audit the corresponding source/build record.
The build receipt pins the archive manifest, NSX lock, link map, ELF, flat image
and generated source. It checks the selected local archive and rejects competing
runtime modules. No default adapter behavior changes.

`compile=False` renders only. An explicit preserved work directory is bound to
fixture, configuration and runtime identities; mismatched reuse fails. This is
a prepared-runtime integration, not a source runtime builder or general recipe.

`helia_profiler.firmware.fixture_memory.decode_memory_snapshots` validates the
versioned 32-byte terminal. Three values are current normal allocator use after
I/O access, warmups and the timed batch. They are not allocator peaks, temporary
high-water marks or minimum viable capacity. The firmware captures them outside
the timed interval. Full output is 480 bytes; timing is six words. The caller
still owns numerical acceptance against the pinned full expected output.

Stack/heap peaks are unavailable. Energy is not captured by this API. One batch
is integration smoke evidence, not a variance or performance qualification study.
