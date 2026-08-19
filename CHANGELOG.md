# Changelog

All notable changes to heliaPROFILER are documented in this file.

This project follows [Semantic Versioning](https://semver.org/) and uses
[Release Please](https://github.com/googleapis/release-please) to prepare
release pull requests from Conventional Commits.

## [0.1.6](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.5...v0.1.6) (2026-08-19)


### Features

* **compatibility:** promote nsx-executorch to the PR [#4](https://github.com/AmbiqAI/helia-profiler/issues/4) merge ([d3d649c](https://github.com/AmbiqAI/helia-profiler/commit/d3d649c9e20089386df53c22c4488c9b223cd796))
* **compatibility:** qualify nsx-executorch main at the PR [#2](https://github.com/AmbiqAI/helia-profiler/issues/2) merge ([9107d20](https://github.com/AmbiqAI/helia-profiler/commit/9107d205509c85f67d3280d969f3ef3d7a0e3d75))
* **executorch:** ns_ops support and PTE sidecar self-configuration ([ec6f52e](https://github.com/AmbiqAI/helia-profiler/commit/ec6f52ef94ea04291a07b14ec6646f63652cf717))
* **executorch:** per-buffer memory region placement ([3b294e7](https://github.com/AmbiqAI/helia-profiler/commit/3b294e700d9728a5ea92e9ad9e278641f32032ec))
* **executorch:** Tier-1 arm-vs-ns comparison assets and kernel verification ([4ec084f](https://github.com/AmbiqAI/helia-profiler/commit/4ec084f510a019eaabe2de447119530a17f9811d))


### Bug Fixes

* **compare:** key power comparability on what the window measures ([#125](https://github.com/AmbiqAI/helia-profiler/issues/125)) ([#137](https://github.com/AmbiqAI/helia-profiler/issues/137)) ([39d5e53](https://github.com/AmbiqAI/helia-profiler/commit/39d5e53985ecc25c4c02533e7cd09bc04b0d0177))
* **executorch:** address PR review — sidecar validation, nm resolution, portable config paths ([b2dbda1](https://github.com/AmbiqAI/helia-profiler/commit/b2dbda1183ca81b76f3721b75245c056575d980e))
* **power:** let a no-inference probe complete an external run, and check its window ([#125](https://github.com/AmbiqAI/helia-profiler/issues/125)) ([#136](https://github.com/AmbiqAI/helia-profiler/issues/136)) ([8457a9c](https://github.com/AmbiqAI/helia-profiler/commit/8457a9c7e9cc39bd21e47108894ec8c0e7a72376))
* **power:** verify the 32.768 kHz crystal has settled before timing a window ([#110](https://github.com/AmbiqAI/helia-profiler/issues/110)) ([#128](https://github.com/AmbiqAI/helia-profiler/issues/128)) ([6c22da7](https://github.com/AmbiqAI/helia-profiler/commit/6c22da7c376120e86c0ee5656e8caa5b55a819e1))
* **report:** stop counting the linker's .heap reservation as bss ([#24](https://github.com/AmbiqAI/helia-profiler/issues/24)) ([#131](https://github.com/AmbiqAI/helia-profiler/issues/131)) ([3139e1b](https://github.com/AmbiqAI/helia-profiler/commit/3139e1b2724668961f4ae63044cf5074d049cb07))
* **report:** stop publishing energy-per-inference for windows with no inferences ([#125](https://github.com/AmbiqAI/helia-profiler/issues/125)) ([#127](https://github.com/AmbiqAI/helia-profiler/issues/127)) ([f92658f](https://github.com/AmbiqAI/helia-profiler/commit/f92658fca984ba6532c4598f5cb80d0bc14dc43a))


### Documentation

* **executorch:** ns_ops, sidecar self-configuration, and memory placement ([6f7097b](https://github.com/AmbiqAI/helia-profiler/commit/6f7097bb5f6175eafdaef67c2c6f0e56fb6e3069))

## [0.1.5](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.4...v0.1.5) (2026-08-19)


### Measurement notes for existing users

Two fixes in this release change reported numbers on Apollo3/Apollo4 boards —
in both cases because the old numbers were wrong, not because the measurement
changed:

* **AP3/AP4 clean-window latency may read higher than in 0.1.4, by up to
  ~21% ([#121](https://github.com/AmbiqAI/helia-profiler/issues/121)).** The
  profile firmware's clean window was intermittently losing cycles while the
  host probe attached, under-reporting `device_clean_infer_avg_us` on some
  runs (bench-measured: 3.9% run-to-run spread on identical binaries, worst
  case 21% low). The window now waits for the probe and self-checks its
  clock; the higher, stable readings are the correct ones. Re-record AP3/AP4
  latency baselines taken with earlier releases.
* **Gated power capture on wired AP3/AP4 boards now uses the 3-wire
  lock-step handshake by default
  ([#114](https://github.com/AmbiqAI/helia-profiler/issues/114)).** This is a
  real electrical difference on the measured rail, so `hpx compare` will
  refuse power deltas against baselines recorded free-running
  (`metric.power_power_lockstep_mismatch`) — a power-gated comparison
  against an old baseline fails rather than reporting a phantom delta.
  Re-record power baselines, or set `power.lockstep: false` to keep the old
  behaviour. An explicit setting always wins.


### Features

* add native ExecuTorch profiling ([dc4b5e3](https://github.com/AmbiqAI/helia-profiler/commit/dc4b5e35a57deaac019393da9d5289799f9015eb))
* add native ExecuTorch profiling ([40b4ff3](https://github.com/AmbiqAI/helia-profiler/commit/40b4ff375b595ac09ed5b9ee53e15d1ebcecbf9f))
* **power:** on-device INA228 power measurement ([#96](https://github.com/AmbiqAI/helia-profiler/issues/96)) ([80ebedc](https://github.com/AmbiqAI/helia-profiler/commit/80ebedc3d98ff9902ece38c0efb67407ba66f7c0)), closes [#95](https://github.com/AmbiqAI/helia-profiler/issues/95)


### Bug Fixes

* **compatibility:** promote neuralSPOT-X 0.7.17 ([#102](https://github.com/AmbiqAI/helia-profiler/issues/102)) ([2b20811](https://github.com/AmbiqAI/helia-profiler/commit/2b208116c4985c4c32462cafb220907a18f23eeb))
* **compatibility:** record peeled commits for TFLM module baseline refs ([#105](https://github.com/AmbiqAI/helia-profiler/issues/105)) ([7ba6594](https://github.com/AmbiqAI/helia-profiler/commit/7ba659426eb35b1f1c572a10ea96fd9233fbf8dc))
* **executorch:** align NSX module consumption ([1d3d826](https://github.com/AmbiqAI/helia-profiler/commit/1d3d82697df06a6bd3a461217b9fdd87ca1fa580))
* **executorch:** consume nsx-executorch's real CMSIS-NN module contract ([7a70f40](https://github.com/AmbiqAI/helia-profiler/commit/7a70f4058a7f88a6569dd6174414410674595351))
* **executorch:** consume qualified provider modules ([5c8c1ef](https://github.com/AmbiqAI/helia-profiler/commit/5c8c1ef9513a4d15525d309ef68b26e73b3d6e46))
* **power:** auto-enable lockstep on any wired board, and name it in no_gate_rise ([#122](https://github.com/AmbiqAI/helia-profiler/issues/122)) ([dbbbbdc](https://github.com/AmbiqAI/helia-profiler/commit/dbbbbdcaa350982044817ffcfd1d3f91d2c73b4a))
* **power:** hold the clean window shut until the host probe attaches ([#121](https://github.com/AmbiqAI/helia-profiler/issues/121)) ([#123](https://github.com/AmbiqAI/helia-profiler/issues/123)) ([56cc077](https://github.com/AmbiqAI/helia-profiler/commit/56cc0774c0ccaffa2b5c06e859786b2213399083))
* **power:** INA228 Apollo4 bus shutdown + firmware gates, decouple monitor from driver ([#99](https://github.com/AmbiqAI/helia-profiler/issues/99)) ([9291e4b](https://github.com/AmbiqAI/helia-profiler/commit/9291e4b3c17f6645214598248c52f3167a5f3c8f))
* **power:** stop timing the AP4 power window with a clock it powers down ([#106](https://github.com/AmbiqAI/helia-profiler/issues/106)) ([f044809](https://github.com/AmbiqAI/helia-profiler/commit/f0448099fa80b3fdffa2b84cd172769c0bf552e7))
* **power:** time the busy-loop probe with a clock the binary can read ([#112](https://github.com/AmbiqAI/helia-profiler/issues/112)) ([#120](https://github.com/AmbiqAI/helia-profiler/issues/120)) ([e88a23d](https://github.com/AmbiqAI/helia-profiler/commit/e88a23d75296eee498b6afa927f3dc95a4c260ab))
* **power:** time the free-running power window with a clock it can actually read ([#107](https://github.com/AmbiqAI/helia-profiler/issues/107)) ([3736ef7](https://github.com/AmbiqAI/helia-profiler/commit/3736ef7b6f526d264be7b43af536664453293de1))
* **probe:** derive the J-Link fallback flash address per SoC ([#117](https://github.com/AmbiqAI/helia-profiler/issues/117)) ([df34b6e](https://github.com/AmbiqAI/helia-profiler/commit/df34b6e9b6cc78ea6ecbfc952d60c57b101391e3))
* **probe:** require explicit J-Link flash confirmation for the power binary ([#103](https://github.com/AmbiqAI/helia-profiler/issues/103)) ([ac448c8](https://github.com/AmbiqAI/helia-profiler/commit/ac448c8b3844f8f44f0715fa60e38990e8a7eda2))

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
