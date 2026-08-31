# `hpx doctor`

Check whether required host binaries and Python packages can be found, get a
machine-readable report, or collect a sanitized support bundle for sharing
with a teammate or filing an issue.

## Synopsis

```bash
hpx doctor
hpx doctor --json
hpx doctor --bundle ./support               # writes a .zip into this directory
hpx doctor --bundle ./support.zip           # writes exactly this file
hpx doctor --bundle ./support --workspace ./work/dependency-workspaces/<fingerprint>/profiler_app
hpx doctor --bundle ./support --config hpx.yml
hpx doctor --bundle ./support --no-probes --no-ports
hpx doctor --bundle ./support --raw-probe-ids
```

## Behavior

Reports the current status of the host tools and Python packages that
`hpx` checks before or alongside profiling:

- `arm-none-eabi-gcc`
- `cmake`
- `ninja`
- `JLinkExe`
- `neuralspotx` Python package
- `pylink` Python package (required for RTT/SWO transport, including the
  default RTT flow)
- `helia-aot` Python package (optional)
- `armclang` (optional)
- `fromelf` (optional)
- ATfE's `clang`, `clang++`, `llvm-ar`, `llvm-objcopy`, `llvm-size`, and
  `llvm-nm` when `ATFE_ROOT` is set

With `--json`, the same check plus best-effort **version** checks (`hpx`
itself, `neuralspotx` against the HPX compatibility baseline, `cmake` against
its minimum, and the selected compiler) are emitted as one JSON object instead
of the table. Version checks are informational — `ok` is `true`/`false`/`null`
(unknown, e.g. tool not found) and never changes `doctor`'s exit code.

## Output

```
✓ ARM GCC toolchain: /usr/bin/arm-none-eabi-gcc
✓ CMake (>= 3.24): /usr/bin/cmake
✓ Ninja build system: /usr/bin/ninja
✓ SEGGER J-Link commander: /usr/bin/JLinkExe
✓ neuralspotx Python package: installed
✓ pylink Python package (RTT/SWO transport): installed
– heliaAOT compiler: not installed
– ARM Compiler (armclang): not installed
– ARM fromelf (armclang): not installed
```

| Symbol | Meaning |
|---|---|
| `✓` | Found at the reported path |
| `✗` | **Required** binary missing — `hpx profile` will fail until installed |
| `–` | Optional dependency missing — only matters if you opt into that feature |

## Exit code

| Code | Meaning |
|---|---|
| 0 | Prints the status table (or `--json`/`--bundle` output). Missing required tools are shown as `✗`, but `hpx doctor` does not currently fail its exit status. |
| 1 | `--bundle` collection or archive writing hit an unexpected typed error (e.g. an unwritable output path). |
| 2 | An invalid `--toolchain`/`--transport`/`--engine` value was given. |

`doctor` flags **required** dependencies as failures in the table. Missing
optional capabilities (for example `helia-aot` or Arm Compiler binaries)
report `–`. It does not compile a program.

## `--bundle`: field-diagnostics support archive

`--bundle PATH` collects a sanitized, offline-safe snapshot of the host
environment into a deterministic `.zip` and never fails outright for a
missing optional piece — each section below is collected best-effort and
marked unavailable with a reason instead:

| Section | Always available? | Notes |
|---|---|---|
| `checks` / doctor table + versions | Yes | Same data as `--json` |
| `compatibility` | Yes (offline) | The pinned HPX compatibility baseline |
| `dependencies` / `nsx.lock` | Only with `--workspace` | Exact typed lock provenance via `read_dependency_lock_provenance()`, plus a sanitized/redacted copy of the `nsx.lock` text |
| `modules` | Yes | Baseline-qualified modules, plus exact resolved modules when `--workspace` is given |
| `config` | Only with `--config` | A sanitized snapshot of the resolved `ProfileConfig` |
| `probes` | Unless `--no-probes` | Connected J-Link probes (serials redacted by default) |
| `ports` | Unless `--no-ports` | Host serial ports (serial numbers redacted by default) |

`--workspace` accepts a prepared `profiler_app` directory, its `nsx.lock` or
`hpx-dependencies.json`, or the parent fingerprint workspace — the same
inputs `read_dependency_lock_provenance()` accepts (see
[Results](api/results.md#field-diagnostics-support-bundle)).

**Never included:** model files, firmware/generated sources, ELF/binary
build outputs, raw proprietary payloads, or process environment values.

**Redacted by default:** absolute filesystem paths (only the final path
component is kept, except a bare home directory itself, whose final
component is the account name); URL credentials -- both the
`user:password@host` form and a single bearer-style credential with no
colon -- plus every URL query-parameter value except a narrow allow-list
of clearly non-sensitive names; common credential/token shapes (GitHub
PAT, AWS key, Slack token, JWT, an HTTP bearer credential) wherever they
appear, including inside a URL; `KEY=VALUE`/`KEY: VALUE` secret-looking
text assignments and, structurally, any JSON field whose *key* looks
secret-shaped (`api_key`, `NSX_SECRET`, ...) regardless of its value; and
device serial numbers (J-Link probes, USB serial numbers) -- both by field
name and by substitution everywhere else a known serial value recurs (for
example inside a `hwid` string). Pass `--raw-probe-ids` to keep real
probe/port serial numbers in the bundle -- this prints an explicit warning
and is never the default.

The bundle's `manifest.json` records a `redaction` object with per-category
counts (`paths`, `urls`, `tokens`, `serials`, `env_values`) and whether
`--raw-probe-ids` was used. These counts report what redaction found and
rewrote -- they are useful evidence, not a certificate that a bundle
contains nothing sensitive; review a bundle's contents before sharing it,
as you would any other diagnostic output.

Two bundles built from identical inputs produce the same archive file name
and identical member bytes (except `manifest.json`'s `generated_at`
timestamp) — see `helia_profiler.diagnostics.support_bundle.verify_support_bundle()` to
re-check an existing archive's structure and digests.

## See also

- [Installation](../getting-started/install.md) — how to install each
  tool.
- [Toolchains](../guide/toolchains.md) — extra binaries the optional
  toolchains require.
- [Results](api/results.md#field-diagnostics-support-bundle) — the
  programmatic `collect_support_bundle()` / `write_support_bundle()` /
  `verify_support_bundle()` API `--bundle` is built on.
