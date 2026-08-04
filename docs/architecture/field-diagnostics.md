# Field-diagnostics support bundle

`hpx doctor --bundle` exists so a customer or field engineer can hand a
maintainer one sanitized archive instead of a screen-share, without either
side worrying it contains a model, firmware source, a credential, or a
device identifier that shouldn't leave the building.

## Design goals

- **Never fail outright.** A missing `--workspace`, an unresolved
  `--config`, absent hardware, or a host with no network access must each
  degrade exactly the section that depends on them — the rest of the bundle
  still gets collected. See `SupportBundleSection.available`/`.reason` in
  `results/support_bundle.py`.
- **Safe by default.** Absolute paths, URL credentials/tokens, common
  credential/token shapes, secret-looking `KEY=VALUE` assignments, and
  device serial numbers are redacted unless a caller explicitly opts out
  (`--raw-probe-ids`, which also prints a warning). See `redact.py`.
- **Reuse, don't reparse.** Dependency lock provenance is read through the
  Stage 5 `read_dependency_lock_provenance()` provider — the collector
  never re-parses `nsx.lock` itself, and never resolves, synchronizes, or
  mutates a workspace.
- **Deterministic.** Two bundles built from identical inputs produce
  byte-identical members (except `manifest.json`'s `generated_at`
  timestamp) and the same archive file name, so a diff between two bundles
  is a diff between two *environments*, not two invocations.
- **A distinct, versioned contract.** The bundle's manifest
  (`hpx.support-bundle-manifest`, schema v1 — `SupportBundleManifest`) is
  not `ResultManifest`: a support bundle has no `RunStatus`/`ResultValidity`/
  comparability concept, since it isn't a profiling run. It reuses
  `ResultArtifact` for member entries because that shape (content-addressed
  path/size/sha256) is identical either way.

## What is collected

| Section | Source | Always available? |
|---|---|---|
| `checks` | `doctor.inspect_environment(include_versions=True)` | Yes |
| `compatibility` | `compatibility.load_compatibility_baseline()` | Yes (offline) |
| `dependencies` / `nsx.lock` | `dependencies.read_dependency_lock_provenance()` + the verbatim `nsx.lock` bytes | Only with `--workspace` |
| `modules` | Baseline-qualified modules, plus exact resolved modules from the same provenance read | Yes (resolved half only with `--workspace`) |
| `config` | `config.load_config()` + `pipeline._serialize_config()` | Only with `--config` |
| `probes` | `target.probe.jlink.list_connected_probes()` | Unless `--no-probes` |
| `ports` | `transport.ports.list_serial_ports()` | Unless `--no-ports` |

Host info (`platform.system()`/`.release()`/`.machine()`/`.python_version()`,
`sys.platform`) is embedded in the manifest directly. `platform.node()`
(hostname) and any username are deliberately excluded — neither is needed to
diagnose a toolchain/build problem, and both are more identifying than the
default redaction policy is designed to catch.

## What is never collected

Models, firmware/generated sources, ELF/binary build outputs, raw
proprietary payloads, credentials, and secret environment values are never
read or embedded — not merely redacted after the fact. The archive writer
and `verify_support_bundle()` both enforce a strict allow-list: every member
must be named exactly `nsx.lock` or end in `.json`; anything else — a zip
entry engineered to look like a model or binary, an absolute path, a `..`
traversal — is rejected before any bytes are trusted.

## Redaction

See `redact.py` for the full pattern set. In short: absolute paths keep
only their final path component (`/Users/alice/model.tflite` →
`<redacted-path>/model.tflite`); URL userinfo and token-shaped query
parameters are stripped; known credential/token shapes (GitHub PAT, AWS
access key, Slack token, JWT, `Bearer <token>`) and secret-looking
`KEY=VALUE`/`KEY: VALUE` assignments are replaced; device serial numbers are
redacted **structurally** (by field name — `serial`, `serial_number`, ...)
rather than by digit-pattern matching, to avoid false positives on ordinary
counters and sizes. Every redaction pass returns a `RedactionCounts`, summed
into the manifest's `redaction` object so a reviewer can see exactly what
categories were touched, and how many times, without ever needing the
original value to confirm it.

`--raw-probe-ids` is the one explicit opt-out: it keeps real probe/port
serial numbers in the bundle and prints a warning to make the choice hard to
miss in a script or CI log.

## Deterministic archiving

`write_support_bundle()` writes a `zipfile.ZIP_DEFLATED` archive with:

- members sorted lexicographically, `manifest.json` always last;
- a fixed `(1980, 1, 1, 0, 0, 0)` timestamp and `create_system = 0` on every
  entry, so the archive doesn't encode the build host's clock or OS;
- a filename derived from `content_fingerprint()` — a SHA-256 over every
  *other* member's `(name, sha256)` pairs, deliberately excluding
  `manifest.json` (the one member whose content always differs run to run,
  via `generated_at`).

`verify_support_bundle()` re-derives and checks every declared artifact's
size and SHA-256, requires the declared and actual member sets match
exactly, and rejects unsafe member paths (absolute, `..`/empty segments,
backslashes, NUL bytes, duplicates) before trusting anything else in the
archive — defense in depth against a corrupted or hand-edited archive, even
though this module is the only thing that ever writes one.

## Deferred / follow-up

A validation-matrix "bundle on failure" hook (automatically attaching a
support bundle to a failed `hpx validate` case) was considered but not
implemented here to avoid conflicting with `validation/matrix.py`'s existing
bundling and to keep this change scoped to `hpx doctor`. It remains a
natural follow-up once this collector has shipped.
