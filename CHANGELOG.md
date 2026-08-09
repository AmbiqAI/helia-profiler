# Changelog

All notable changes to heliaPROFILER are documented in this file.

This project follows [Semantic Versioning](https://semver.org/) and uses
[Release Please](https://github.com/googleapis/release-please) to prepare
release pull requests from Conventional Commits.

## [0.1.4](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.3...v0.1.4) (2026-08-09)


### Bug Fixes

* **deps:** raise idna and pydantic-settings above advisory floors ([#93](https://github.com/AmbiqAI/helia-profiler/issues/93)) ([13684f3](https://github.com/AmbiqAI/helia-profiler/commit/13684f3a0220b42a8c0d17c0443002a132cb7756)), closes [#91](https://github.com/AmbiqAI/helia-profiler/issues/91)

## [0.1.3](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.2...v0.1.3) (2026-08-09)


### Bug Fixes

* **compatibility:** promote neuralSPOT-X 0.7.14 ([#90](https://github.com/AmbiqAI/helia-profiler/issues/90)) ([65072dd](https://github.com/AmbiqAI/helia-profiler/commit/65072dd54f13cab0b6c2f8afb0162ec5a42d34fc))

## [0.1.2](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.1...v0.1.2) (2026-08-05)

### Release Highlights

* **Immutable compatibility baseline.** HPX now records a typed, immutable
  compatibility baseline with neuralSPOT-X `0.7.12` and `nsx-ambiq-sdk`
  `v5.2.24` pinned to exact Git object IDs and package provenance ([be9fc4b](https://github.com/AmbiqAI/helia-profiler/commit/be9fc4b34adc3942ce0192121c4dbd289191fa42)).
* **Deterministic NSX builds.** Fingerprinted dependency workspaces and exact
  `nsx.lock` reuse make repeat builds deterministic; `--update-dependencies`
  is the explicit refresh operation and `--offline` provides lock-only reuse
  ([78aab42](https://github.com/AmbiqAI/helia-profiler/commit/78aab42aab439e3e976b9bb0744ecf58e0e5b0d1)).
* **Auditable result bundles.** Profiles carry the exact dependency lock,
  baseline, update/offline mode, source revisions, and runtime provenance
  used for the run ([78aab42](https://github.com/AmbiqAI/helia-profiler/commit/78aab42aab439e3e976b9bb0744ecf58e0e5b0d1), [977eb04](https://github.com/AmbiqAI/helia-profiler/commit/977eb04c78b463c52123cd840065e6a8caa461d6)).
* **Safer field diagnostics.** `hpx doctor --bundle` creates sanitized support
  archives with credential, serial, path, and nested secret-shaped values
  redacted ([a6d37c6](https://github.com/AmbiqAI/helia-profiler/commit/a6d37c6bfb81ca1ea6a2cc4bece9172a16a14589)).
* **Hardware confidence.** Release validation covers Apollo510B cold- and
  warm-start runs. In-repository validation also restores Apollo330 coverage
  and records run origin ([3fbc9d6](https://github.com/AmbiqAI/helia-profiler/commit/3fbc9d6686ffbb59409e7ecf9e0f08defd0b8f11), [a1dcc0c](https://github.com/AmbiqAI/helia-profiler/commit/a1dcc0ca51b59f4ddd3a50f8596efd86a6fde6fe)).
* **PSRAM visibility.** Clock, timing, and placement diagnostics are now
  available in captured metadata and summaries ([983f784](https://github.com/AmbiqAI/helia-profiler/commit/983f7847d09faa041c98b2f631e0d9b7d34eaca6)).
* **Broader host support.** Python 3.11–3.14, Windows diagnostics, and ARM64
  Linux/macOS Nix environments are covered ([46258ef](https://github.com/AmbiqAI/helia-profiler/commit/46258ef4eef0843b0e25ee14190bd801a154d91d), [cc9cb15](https://github.com/AmbiqAI/helia-profiler/commit/cc9cb156540a44874c2015b9b19d51ab7a754a34), [617e610](https://github.com/AmbiqAI/helia-profiler/commit/617e6102ab7f007ccbdd9b4f961f9439119701fb)).
* **Power remains optional.** Standard profiling and validation do not require
  a Joulescope; power capture and power artifacts are produced only when an
  appropriate capture device is explicitly enabled.

### Additional Features

* Added validation comparisons, rich decision summaries, schema 4 bundle
  support, and complete-suite TFLM
  CMSIS-NN coverage ([0ac42b3](https://github.com/AmbiqAI/helia-profiler/commit/0ac42b3794253473ccc95b5edc940006cfde131f), [88ea423](https://github.com/AmbiqAI/helia-profiler/commit/88ea4232b0bd5642d717e3068d2794546110b6ff), [78f164e](https://github.com/AmbiqAI/helia-profiler/commit/78f164e79dd6107edc54422fc6c3216efb910389), [9092c8d](https://github.com/AmbiqAI/helia-profiler/commit/9092c8d1a674a1f8e2b2b2f9fff7a804c5579f00)).
* Added a portable Nix environment, Windows install guidance, and licensed
  J-Link download automation ([85267da](https://github.com/AmbiqAI/helia-profiler/commit/85267da7106174fb6cbd0d0434d744f1b32050c7), [333095d](https://github.com/AmbiqAI/helia-profiler/commit/333095d3d5fccc70c24806ad4b4f0871840c3dd2), [3686519](https://github.com/AmbiqAI/helia-profiler/commit/3686519e125a3f5b1c7ddce12fb0c8672e6d15e1)).
* Expanded validation resources and published power artifacts when capture is
  enabled ([2811f2c](https://github.com/AmbiqAI/helia-profiler/commit/2811f2c9609b0b3635851519a3c8487edc7a7f2a), [c5c51a4](https://github.com/AmbiqAI/helia-profiler/commit/c5c51a428260cceef53539df4ebf7aa249e86d56)).

### Reliability Fixes

* Hardened AOT memory-shape and placement validation, ATFE binary probing, and
  NSX cache/workspace permissions across Nix and Linux environments.
* Pinned validation source and engine-module revisions to commits, preserved
  default source resolution, and kept Apollo330 power validation disabled
  where unsupported ([80c969b](https://github.com/AmbiqAI/helia-profiler/commit/80c969bdee32261e1df1bd02bc22da917ca4a53d), [4c8c253](https://github.com/AmbiqAI/helia-profiler/commit/4c8c25382d6a90aa251c47f8add0ae24ecd493f2), [93f2b63](https://github.com/AmbiqAI/helia-profiler/commit/93f2b63627e9431f9db18ae42baf4e67b3018d63)).
* Added generated quick-install and public feature documentation updates.

## [0.1.1](https://github.com/AmbiqAI/helia-profiler/releases/tag/v0.1.1) (2026-07-19)

### Features

* **release:** add automated PyPI publishing ([0fb627b](https://github.com/AmbiqAI/helia-profiler/commit/0fb627b3d4f70e8ba35d7fb766125630b4cb4767))

## [Unreleased]
