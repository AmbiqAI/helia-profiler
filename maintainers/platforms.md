# Regenerate linker layouts

HPX owns linker choices, memory admission and ELF classification. The shared
catalog supplies hardware application ranges; it does not select a linker
script. The runtime ownership and saved-contract boundaries are described in
the [platform guide](../astro-site/src/content/docs/guide/concepts/platforms.mdx).

`src/helia_profiler/data/linker-windows-v1.json` is generated from two clean
source checkouts:

- `nsx-ambiq-sdk` at the `projects.nsx-ambiq-sdk.ref` in
  `src/helia_profiler/data/compatibility-baseline-v1.json`;
- `helia-platforms` at the commit in `tools/linker_windows/extractor.json`.

The generator reuses the catalog's script and C-expression readers. These are
maintainer dependencies, not runtime imports. Its manifest records the parser
file hashes and SDK input hashes; source text uses UTF-8 with LF-normalized line
endings so Git checkout conventions do not change identity.

```bash
uv run python tools/gen_linker_windows.py \
  --sdk-root /path/to/nsx-ambiq-sdk \
  --extractor-root /path/to/helia-platforms

uv run python tools/gen_linker_windows.py \
  --sdk-root /path/to/nsx-ambiq-sdk \
  --extractor-root /path/to/helia-platforms --check
```

CI runs the check with pinned sparse checkouts. The SDK checkout needs its
`cmake/socs/`, toolchain selector helper, selected GNU/scatter scripts, register
base-address headers and Apollo3 flash HAL header. The extractor checkout needs
`scripts/snapshot_extractor/`.

When advancing the SDK or extractor, regenerate and review every affected
region, application extent and fixed reservation against its source. Run
`tests/platform/`, the build/planner/placement tests, and the public receipt and
native-reader tests. Exercise actual configuration and compile/link with affected
installed toolchains. A successful host link is not hardware qualification.

SDK descriptors characterize expected defaults; board configuration may select
them without executing those descriptors. Actual authority is the configured
executable's CMake link information plus current module revisions and input
content. Do not require a generation descriptor to have executed or infer the
actual script from a requested profile. Unsupported metadata must remain an
explicit unavailable result. Strict compiled receipts refuse such a build;
ordinary development builds retain that limitation in their metadata.

Positional link files are checked by binary header, regardless of extension:
GNU linkers can treat an unknown text input as an implicit script. A missing
file qualifies only when CMake declares it as a binary library/object product;
an existing file must pass the format check even if it is a declared product.
Named library searches without explicit search paths remain toolchain inputs.
Explicit library search paths, opaque response files and unsupported script
selections leave source verification unavailable; HPX does not resolve a second
library graph to characterize possible scripts in those searches.

Automatic placement uses the same interval-constrained capacity before AOT
conversion and during memory planning. Custom or uncharacterized layouts retain
their declared capacities; actual-source admission still occurs at the link
boundary.
