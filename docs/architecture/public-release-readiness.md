# Public Release Readiness

This document is the remaining release plan for heliaPROFILER. It starts from
the current implementation rather than proposing a rewrite.

## Current baseline

The core profiling architecture is suitable for a public release:

- one explicitly selected engine per run;
- immutable resolved configuration;
- a linear, inspectable stage pipeline;
- separate profile and power firmware;
- typed profile, power, deployment, and terminal records;
- early host dependency and probe selection checks;
- integrity-aware GPIO-gated power capture;
- immutable, branchable interactive sessions;
- Rich terminal progress and summaries;
- portable validation bundles and hardware validation CI;
- tested RTT, UART, SWO, and USB CDC transports.

The remaining work is primarily contract consolidation, correctness policy,
interface consistency, and release operations.

## Release principles

1. Invalid or incomparable measurements must be more visible than the numbers.
2. Human and machine outputs must share one versioned result envelope and
  comparison vocabulary without closing the schema to future measurements.
3. A completed run must be reloadable without access to its original workspace.
4. CLI and programmatic entry points must share behavior without forcing terminal
   output on library users.
5. Public extension points must be narrow, documented, and tested before they
   are advertised as stable.
6. Version 1.0 freezes compatibility rules and required schema fields, not every
  possible result field or internal implementation detail.

## Milestone 1: Versioned result bundle

This is the highest-priority release milestone. Today, `summary.json`,
`run_metadata.json`, `profile_results.csv`, and optional detailed files are
useful, but ordinary profile outputs do not have one versioned bundle contract.
Comparison also assumes the default CSV output, while JSON output follows a
separate serialization path.

The schema should be intentionally loose. It must make identity, validity,
provenance, artifact discovery, and common comparisons dependable while
allowing new counters, engines, power monitors, and reports to add data without
a schema-version bump. Consumers must ignore unknown fields they do not use.

Use a small typed core with open objects around dynamic data:

- Required stable envelope: schema identity/version, run identity, completion
  status, validity, provenance, artifacts, and structured issues.
- Stable common metrics: values carry a key, numeric value, unit, scope, and
  optional denominator rather than requiring every metric as a fixed property.
- Dynamic measurements: PMU counters, engine-specific analysis, transport
  diagnostics, and experimental metrics remain open key/value collections.
- Namespaced extensions: producers place non-core structured data under keys
  such as `engines.helia-aot`, `power.joulescope`, or another collision-resistant
  namespace.
- Additive evolution: optional fields and new metric keys are non-breaking.
  Removing fields, changing meaning or units, or tightening accepted values is
  breaking and requires a new major schema version.
- Unknown-field preservation: load/save operations retain fields unknown to the
  installed HPX version so newer bundles can pass through older tooling without
  silent data loss.

Deliverables:

- Define a versioned `result_manifest.json` with:
  - `schema_version`, HPX version, run ID, timestamp, and completion status;
  - model digest, resolved configuration digest, engine/toolchain identity, and
    firmware artifact identity;
  - artifact names, media types, sizes, and SHA-256 digests;
  - measurement integrity, structured issues, and comparability keys.
- Define typed models for only the stable envelope and common comparison fields.
  Keep layer counters, engine data, diagnostic payloads, and extension objects
  open and JSON-compatible.
- Add `ProfileResult.load(path)` and `ProfileResult.save(path)` or equivalent
  public functions with explicit schema compatibility checks.
- Publish a permissive JSON Schema for the manifest and primary JSON result.
  Require the stable envelope, use `additionalProperties: true` at extension
  boundaries, and check representative bundles into the repository as
  regression fixtures.
- Write artifacts to a temporary run directory and publish the manifest last so
  an interrupted run cannot look complete.
- Make comparison load the versioned bundle, independent of whether CSV or JSON
  convenience views were requested.
- Preserve CSV, compact summary JSON, detailed reports, and Model Explorer files
  as derived views.

Exit criteria:

- Every successful run has exactly one manifest marked complete.
- A failed or interrupted run cannot be loaded as a valid complete result.
- Artifact digest corruption is detected with an actionable error.
- Results written by the oldest supported schema load in the current version.
- Known and unknown fields survive a load/save round trip without semantic or
  numeric changes.
- Adding an extension field or new metric key passes schema validation without a
  schema-version change.
- `hpx compare` produces the same metrics from CSV- and JSON-configured runs.

For regression use, define versioned comparison profiles separately from the
storage schema. A profile selects metric keys, units, aggregation, tolerances,
and required comparability dimensions. This lets CI and a future web tool apply
the same policy without treating every stored field as comparable or stable.
The bundle schema can evolve additively while a named comparison profile remains
reproducible.

### Core artifacts and optional exports

The bundle owns artifact discovery and integrity, but HPX does not own every
artifact's content schema.

- **Core**: HPX summary, run metadata, and the selected primary profile result.
  These have stable HPX schema identities and are required for a complete
  profile bundle.
- **Projection**: detailed tables or alternate views derived from core data.
  They are optional and should be reproducible from the owning result model when
  practical.
- **Extension**: engine- or monitor-specific outputs such as heliaAOT operator
  and memory data. Their namespaces and versions evolve independently.
- **Export**: third-party interoperability products such as Model Explorer
  overlays. The external format owns their schema; HPX records its exporter as
  the producer.
- **Diagnostic**: troubleshooting or acquisition detail that is useful but not
  required to interpret the core result.

Artifact role, semantic name, published schema identity/version, producer, and
optionality are additive manifest metadata. A semantic name supports discovery
without claiming that a formal schema exists. Unknown roles and fields remain valid. An optional artifact
may be omitted, but every artifact actually declared in the manifest is still
required to pass path, size, and digest verification.

### Power-only workflow boundary

Power capture is currently part of a full profile run. The profile phase does
more than collect optional PMU counters: its clean inference timing supplies the
reference used to choose fixed `N` and verify the measured gate duration.
`ProfileResult`, summary/report generation, terminal presentation, and validity
also currently require `PmuResult`.

Do not expose a `--power-only` flag by merely skipping `CapturePmuStage`. That
would either remove the authoritative denominator or leave later stages with an
invalid contract. A first-class power-only workflow requires:

1. A `PowerProfileResult` (or a deliberately generic run result) whose core
   artifacts do not require PMU/layer data.
2. A dedicated pipeline composition that still prepares the engine, model,
   memory plan, firmware workspace, and power firmware.
3. Explicit fixed inference count and reference-inference timing inputs, both
  validated and recorded with provenance, or a short clean timing calibration
  pass. `PlanPowerRunStage` must accept both values; today it accepts only N and
  derives reference timing from `PmuResult`. Auto-N inside the measured window is not acceptable
   because it changes the power workload.
4. Power-only summary, manifest, console, and validity paths with explicit
   measurement scope and denominator provenance.
5. Separate semantics for measuring newly built firmware versus attaching an
   instrument to already-running firmware. The latter is acquisition, not an
   HPX-controlled inference benchmark, unless the firmware implements the HPX
   gate and terminal contracts.

The smallest accurate first product is dedicated power-only with caller-supplied
fixed `N` and reference inference time. A calibration-assisted mode can follow.
Until those result and pipeline contracts exist, full profiling remains required
for `hpx profile --power`.

## Milestone 2: Explicit correctness and comparability

Power integrity and PMU overflow are already tracked, but validity is spread
across flags, metadata, warnings, and report-specific suppression rules. Public
consumers need one authoritative status before reading headline metrics.

Deliverables:

- Add a top-level typed run status such as `valid`, `degraded`, `invalid`, or
  `incomplete`, plus structured issue codes and human guidance.
- Centralize acceptance rules for PMU overflow, transport completeness, firmware
  completion, gate integrity, duration consistency, and fixed-N agreement.
- Add typed comparability evaluation for model, board/SoC, engine, clocks,
  placement, toolchain, counters, power scope, and integrity.
- Make derived metrics carry their scope and denominator. Suppress or clearly
  qualify metrics when their denominator is not authoritative.
- Add an optional hardware-readiness phase that checks probe communication,
  target power/Vref, selected transport prerequisites, and power instrument
  identity before expensive compilation. Keep host-only preflight separate.
- Define stable CLI exit codes for usage, dependency, hardware, build, capture,
  integrity, and report failures. Include the same code and structured context
  in machine-readable errors.
- Add repeated-run and known-fixture regression thresholds to hardware CI.

Exit criteria:

- No degraded or invalid run is presented with an unqualified success summary.
- Every headline metric can identify its source, scope, unit, and denominator.
- Comparison refuses invalid pairings by default and explains each mismatch;
  an explicit override records that policy decision.
- Hardware CI detects statistically meaningful drift, not only command failure.
- All supported failure classes have deterministic exit codes and JSON errors.

## Milestone 3: Public API and session contract

`profile(ProfileConfig)`, `Session`, and typed result records are strong
foundations. Before 1.0, the public boundary should be smaller and library calls
must not implicitly own terminal presentation.

Deliverables:

- Publish a curated public symbol list and classify exports as stable,
  experimental, or internal. Avoid freezing probe/backend implementation types
  as 1.0 API accidentally.
- Separate execution from presentation. Programmatic profiling should be silent
  by default and accept optional progress/event and logging hooks; the CLI owns
  Rich rendering.
- Finish deep immutability or use validated immutable models for nested public
  collections. Do not advertise a frozen contract whose lists and dictionaries
  remain mutable.
- Give `Session` explicit snapshot/export/import operations for unresolved intent
  and resolved configuration, with clear naming for each.
- Align CLI and session operations (`profile`, `analyze`, `compare`, `doctor`,
  boards, probes, ports) around shared application services rather than command
  adapters or presentation helpers.
- Document compatibility and deprecation policy for Python APIs and schemas.
- Add static type checking and public API signature snapshots to CI.

Exit criteria:

- Importing and calling the library produces no unsolicited terminal output.
- Public types are deeply immutable or explicitly documented as mutable.
- A saved session resolves identically after reload on the same supported setup.
- CLI and Python calls return equivalent typed results for the same operation.
- Breaking public API or schema changes fail CI unless intentionally approved.

## Milestone 4: Unified CLI and agent interface

The existing Rich progress, result, comparison, and diagnostic views are a good
base. The remaining work is consistency and responsive information design, not
adding a full-screen TUI.

Deliverables:

- Define a global output policy: `--format table|json|csv`, `--output`,
  `--no-color`, `--quiet`, and verbosity semantics where each applies.
- Keep stdout machine-clean in JSON/CSV modes; route diagnostics and progress to
  stderr. Disable animation when non-interactive or when `NO_COLOR` is set.
- Render terminal, JSON, CSV, and notebook views from the shared result envelope,
  metric vocabulary, and issue models.
- Make the default completion view answer: validity, model/engine/target, latency,
  energy when valid, memory pressure, hottest layers, warnings, and output path.
- Add terminal-width snapshots for narrow, standard, and wide displays and test
  color/no-color and TTY/non-TTY behavior.
- Add `hpx result show`, `hpx result inspect`, and `hpx result verify` around
  saved bundles. Keep `hpx compare` focused on two-run analysis.
- Emit a compact agent-oriented JSON view with stable field names and no ANSI or
  prose parsing requirement. This should be a projection of the versioned
  envelope, not a separate closed AI-specific schema.

Exit criteria:

- Primary commands have consistent help, output options, and error envelopes.
- JSON output is parseable from stdout without filtering progress or warnings.
- Default terminal output remains legible at 80, 120, and 160 columns.
- Humans and agents reach the same validity and comparison conclusions from
  their respective views.

## Milestone 5: Extensibility boundaries

Do this after the public contracts are stable. A general plugin framework is not
required for 1.0.

Deliverables:

- Document protocols and conformance tests for engine adapters, capture
  transports, power monitors, report renderers, and toolchain specifications.
- Keep built-in registries private unless third-party registration is a supported
  product requirement.
- Add one reference extension test for each advertised extension point.
- Keep configuration discriminated and validated; unknown extension data must
  remain namespaced rather than becoming untyped top-level configuration.
- Complete the on-device power producer only when hardware support is available;
  retain early rejection until then.

Exit criteria:

- A documented extension can be implemented without editing pipeline internals.
- Conformance tests reject incomplete adapters before a hardware run.
- Extension failures preserve typed HPX error and artifact contracts.

## Milestone 6: Packaging and release operations

The wheel currently builds and contains runtime templates, example model data,
and vendored SEGGER RTT notices. Public package metadata and release automation
are not yet complete.

Deliverables:

- Add root `LICENSE`, `NOTICE` or third-party notice linkage, `SECURITY.md`,
  `CONTRIBUTING.md`, and `CHANGELOG.md`.
- Complete package metadata: README content type, project URLs, authors or
  maintainers, classifiers, keywords, and license files.
- Test Python 3.11 and 3.12 on Linux, macOS, and Windows as declared by the
  package requirement.
- Add wheel/sdist build, metadata validation, clean-environment install, CLI
  smoke tests, and packaged-resource tests to CI.
- Add a trusted-publishing release workflow with TestPyPI rehearsal, signed or
  attestable artifacts, changelog/version checks, and rollback guidance.
- Define the support matrix for boards, engines, transports, toolchains, power
  instruments, operating systems, and optional dependencies.
- Run the complete hardware release matrix and archive its versioned validation
  bundle for each release candidate.

Exit criteria:

- Wheel and sdist install and run from clean supported environments.
- Package metadata renders correctly on TestPyPI and includes license files.
- CI covers every declared Python/platform combination or narrows the declared
  support policy accordingly.
- A release candidate has a published hardware validation bundle with no
  unresolved correctness issues.

## Priority and release gate

Milestones 1 through 4 and 6 are required before declaring 1.0. Milestone 5 is
required only for extension points advertised as public in 1.0; other registries
may remain internal and evolve after release.

Recommended implementation order:

1. Versioned result bundle.
2. Correctness and comparability policy.
3. Public API and session boundary.
4. Unified CLI and agent rendering.
5. Packaging, CI, documentation, and release candidate rehearsal.
6. Public extension protocols that are genuinely required for launch.

Do not begin by restyling tables. The modern interface should be the final
projection of stable result and issue contracts; otherwise presentation work
will encode today's fragmented artifact shapes and need to be repeated.
