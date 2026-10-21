# Changelog

All notable changes to heliaPROFILER are documented in this file.

This project follows [Semantic Versioning](https://semver.org/) and uses
[Release Please](https://github.com/googleapis/release-please) to prepare
release pull requests from Conventional Commits.

## Unreleased

### Reporting changes for existing users

* **`hpx validate` keeps its default toolchain axis inside the qualified NSX
  board module's declaration
  ([#310](https://github.com/AmbiqAI/helia-profiler/issues/310)).** The
  matrix used to schedule every toolchain hpx can drive for every board, and
  `nsx lock` refused the ones the board module does not declare before any
  firmware was built. With neuralspotx 0.8.1 that is `atfe` and `armclang`
  on `apollo4l_blue_evb`. Without `--toolchains` or a suite, those cases no
  longer appear for the board. A toolchain requested explicitly, which the
  suite presets do (`complete` and the model suites select `gcc,atfe`), is
  still enumerated and records a skip naming the module instead of a
  failure, so the complete suite still lists 24 apollo4l cases: 12 run and 12
  skip. `hpx validate --list` now prints that reason next to each case the
  harness will skip.

* **Gated power durations no longer inherit the Joulescope driver's clock fit
  ([#249](https://github.com/AmbiqAI/helia-profiler/issues/249)).** A gated
  window's duration was summed from each stat packet's `utc` span. `utc` is not
  a device timestamp: jsdrv fits a sample-counter-to-UTC map while streaming and
  the packet's span is that fit applied to its counter values. The fit converges
  over the first minutes of a stream, so the reported window inherited its
  error — measured on a JS320 at 130 ppm once settled, 1.5 % on the first
  captures after the stream started, and 2.9 % at the coldest reading, which is
  143 ms on a 5 s window.

  Durations now come from the divisor the driver itself used to build the
  packet's charge and energy integrals, so `energy_j / duration_s` is consistent
  by construction.

  **What moves:** `power.duration_s`, `avg_current_a`, `avg_power_w`, and the
  TOPS figures derived from them, by amounts that depend on the fitted UTC
  scale during the capture. **What does not:** `energy_j`, which the instrument
  integrates on-device, and TOPS-per-watt, where the duration cancels.

  **Results captured before and after this change are not directly comparable**
  on those fields, and `summary.json` now carries `schema_version` 5 to say so;
  a comparison between a schema 4 and a schema 5 run reports the difference. A
  run whose gate previously disagreed with the firmware's own window clock may
  now agree: across a 19-capture bench series the observer error fell from up to
  1.47 % to at most 0.026 %, and two runs changed verdict from `INVALID` to
  valid. None changed the other way.

  Gated captures with usable rate metadata now publish
  `power.gating_diagnostics.instrument_time_map`, reporting the fit against the instrument's nameplate rate as a range, so a
  capture taken while it was still settling says so.


## [0.4.0](https://github.com/AmbiqAI/helia-profiler/compare/v0.3.0...v0.4.0) (2026-10-21)


### ⚠ BREAKING CHANGES

* **api:** retire fixture facade and pre-change readers ([#576](https://github.com/AmbiqAI/helia-profiler/issues/576))
* **run:** save verified run results and portable observations ([#575](https://github.com/AmbiqAI/helia-profiler/issues/575))
* **identity:** namespace semantic hashes ([#573](https://github.com/AmbiqAI/helia-profiler/issues/573))
* **run:** run summaries advance to v18 and the interim fixture API to 6.1 for the explicit energy-method contract.
* **engines:** tflm is no longer an accepted engine selector; use litert in configuration, CLI and records.
* **run:** BuildRecord is now CompiledBuild, saved as hpx.compiled-build v2, and run summaries are v16.

### Features

* **engines:** rename the tflm engine to litert ([#562](https://github.com/AmbiqAI/helia-profiler/issues/562)) ([d2c8f93](https://github.com/AmbiqAI/helia-profiler/commit/d2c8f93f669dd31b278d7efefb0be6c6cc978790)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* expose portable target configuration and CPU clock facts ([#578](https://github.com/AmbiqAI/helia-profiler/issues/578)) ([47a3a4e](https://github.com/AmbiqAI/helia-profiler/commit/47a3a4e7514771cdac7dfea4b02b9bc9f6aca74c)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* **identity:** namespace semantic hashes ([#573](https://github.com/AmbiqAI/helia-profiler/issues/573)) ([6b4a538](https://github.com/AmbiqAI/helia-profiler/commit/6b4a5380da917a1eb8cf3c2b47a2ba312a48c804)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* **run:** build, record and run a frozen plan with an engine-owned lifecycle ([#561](https://github.com/AmbiqAI/helia-profiler/issues/561)) ([f8a6fa5](https://github.com/AmbiqAI/helia-profiler/commit/f8a6fa5b6450a2d9e0410583f18eaeb31dc1d660)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* **run:** expose run workflow through CLI and package API ([#563](https://github.com/AmbiqAI/helia-profiler/issues/563)) ([b87b6cc](https://github.com/AmbiqAI/helia-profiler/commit/b87b6ccec70519e9370030fc962a1d24c9f226fc)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* **run:** measure rail energy and supply-only timing through the run API ([#564](https://github.com/AmbiqAI/helia-profiler/issues/564)) ([93cda08](https://github.com/AmbiqAI/helia-profiler/commit/93cda08bff983b08a124c5390adcf09fdf8a9fdf)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)
* **run:** save verified run results and portable observations ([#575](https://github.com/AmbiqAI/helia-profiler/issues/575)) ([b2230ce](https://github.com/AmbiqAI/helia-profiler/commit/b2230ce6b9a2076d16462aec5cbf57c2860d1763)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)


### Bug Fixes

* validate saved run timing admission ([#577](https://github.com/AmbiqAI/helia-profiler/issues/577)) ([b7d31f5](https://github.com/AmbiqAI/helia-profiler/commit/b7d31f5546baf7bacfdbbe0cb6c2e076e6bbfa5f)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)


### Documentation

* regenerate reference for Placement docstring ([83e2a10](https://github.com/AmbiqAI/helia-profiler/commit/83e2a101a52fec0964cfee6484fcbf535d9eeabd))


### Code Refactoring

* **api:** retire fixture facade and pre-change readers ([#576](https://github.com/AmbiqAI/helia-profiler/issues/576)) ([2a90ead](https://github.com/AmbiqAI/helia-profiler/commit/2a90ead96e09f677ee36f43b98a5bf6883cdd76d)), closes [#558](https://github.com/AmbiqAI/helia-profiler/issues/558)

## [0.3.0](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.5...v0.3.0) (2026-10-19)


### ⚠ BREAKING CHANGES

* **aot:** heliaAOT releases before 0.33.0 are no longer accepted.

### Features

* **aot:** require heliaAOT 0.33.0, the 1.0 API, and drop older releases ([#559](https://github.com/AmbiqAI/helia-profiler/issues/559)) ([6695937](https://github.com/AmbiqAI/helia-profiler/commit/669593798ae6f50f50f949854a98e23d25479eac))

## [0.2.5](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.4...v0.2.5) (2026-10-19)


### Features

* **deps:** adopt heliaAOT 0.32.0 ([#557](https://github.com/AmbiqAI/helia-profiler/issues/557)) ([34a7932](https://github.com/AmbiqAI/helia-profiler/commit/34a79327e3ea71527d7453f5cb98e116b9c973a5)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)
* **results:** state a profile run's RunIdentity ([#555](https://github.com/AmbiqAI/helia-profiler/issues/555)) ([0db88da](https://github.com/AmbiqAI/helia-profiler/commit/0db88dac9c7512001748deccf5f12d40c325f5d2)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)

## [0.2.4](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.3...v0.2.4) (2026-10-19)


### Features

* **deps:** adopt heliaCORE v7.47.0 and heliaRT 2.2.2 ([#554](https://github.com/AmbiqAI/helia-profiler/issues/554)) ([463e039](https://github.com/AmbiqAI/helia-profiler/commit/463e039af3cf25586ce7df72114fceffc7c92aec)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)
* **fixture:** state a fixture build's RunIdentity ([#553](https://github.com/AmbiqAI/helia-profiler/issues/553)) ([60df0c2](https://github.com/AmbiqAI/helia-profiler/commit/60df0c26f4461a4abe7c7a23ca17825826dff88b)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)
* **identity:** canonical engine and kernel names and reasoned unknowns ([#552](https://github.com/AmbiqAI/helia-profiler/issues/552)) ([c462d16](https://github.com/AmbiqAI/helia-profiler/commit/c462d16b973ed0742433404de084ec5fba0c6571)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)
* **session:** freeze a resolved session into a replayable FrozenRun plan ([#543](https://github.com/AmbiqAI/helia-profiler/issues/543)) ([56b2217](https://github.com/AmbiqAI/helia-profiler/commit/56b22170f53ccb7a4ba0cb0de97ad940606ce526)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)


### Bug Fixes

* **aot:** refuse a heliaAOT conversion that returns no code generation context ([#550](https://github.com/AmbiqAI/helia-profiler/issues/550)) ([555ae7b](https://github.com/AmbiqAI/helia-profiler/commit/555ae7bc3bf3755bead8eb2973b022607ddf0104))
* **firmware:** map build paths out of every firmware image ([#551](https://github.com/AmbiqAI/helia-profiler/issues/551)) ([b92a245](https://github.com/AmbiqAI/helia-profiler/commit/b92a245d0c8f06fdc81ce4224d14a5fbf39a3e00)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)

## [0.2.3](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.2...v0.2.3) (2026-10-19)


### Features

* **deps:** adopt heliaRT 2.2.0 and heliaAOT 0.31.0 ([#544](https://github.com/AmbiqAI/helia-profiler/issues/544)) ([6a4d42d](https://github.com/AmbiqAI/helia-profiler/commit/6a4d42d78e7134e1f670c4b1876811c90eeefa15))
* **expect:** compare a run's outputs with reference outputs ([#545](https://github.com/AmbiqAI/helia-profiler/issues/545)) ([41238ee](https://github.com/AmbiqAI/helia-profiler/commit/41238eeb85f9c4fead35b240758cace847d985b4)), closes [#537](https://github.com/AmbiqAI/helia-profiler/issues/537)


### Bug Fixes

* **plan:** check MRAM against the linker's application window and explain link overflows ([#540](https://github.com/AmbiqAI/helia-profiler/issues/540)) ([87e1e8f](https://github.com/AmbiqAI/helia-profiler/commit/87e1e8f813a775f2e6d1965f8784766336b4409b)), closes [#466](https://github.com/AmbiqAI/helia-profiler/issues/466)

## [0.2.2](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.1...v0.2.2) (2026-10-18)


### Bug Fixes

* **fixture:** run models with internal state without a declared carry policy ([#536](https://github.com/AmbiqAI/helia-profiler/issues/536)) ([f0a65d7](https://github.com/AmbiqAI/helia-profiler/commit/f0a65d76bbee18e974316886b10f451ba9efb010)), closes [#482](https://github.com/AmbiqAI/helia-profiler/issues/482)


### Documentation

* describe the heliaRT NSX wrapper as the prebuilt-release adapter ([#535](https://github.com/AmbiqAI/helia-profiler/issues/535)) ([7b8f5fa](https://github.com/AmbiqAI/helia-profiler/commit/7b8f5faf5a0431946c8c6fc4fd6c37544904821a))
* record only tool versions in the generated CLI and configuration reference ([#532](https://github.com/AmbiqAI/helia-profiler/issues/532)) ([feb0c8b](https://github.com/AmbiqAI/helia-profiler/commit/feb0c8b0c415a400e07e2bd880610a09d8ba7937))

## [0.2.1](https://github.com/AmbiqAI/helia-profiler/compare/v0.2.0...v0.2.1) (2026-10-18)


### Bug Fixes

* **firmware:** keep D-cache maintenance off parts without a D-cache ([#531](https://github.com/AmbiqAI/helia-profiler/issues/531)) ([eb8582b](https://github.com/AmbiqAI/helia-profiler/commit/eb8582b061e8c30c940eaa9f9bc0d5fc92338af8)), closes [#464](https://github.com/AmbiqAI/helia-profiler/issues/464)


### Documentation

* keep the package version out of the committed API reference ([#529](https://github.com/AmbiqAI/helia-profiler/issues/529)) ([1f486be](https://github.com/AmbiqAI/helia-profiler/commit/1f486be1680290b0f0077d69b5d19014a782ce90))

## [0.2.0](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.6...v0.2.0) (2026-10-18)


### ⚠ BREAKING CHANGES

* **fixture:** remove the fixed fixture's energy path ([#460](https://github.com/AmbiqAI/helia-profiler/issues/460))

### Features

* add reusable fixed-fixture builds and raw capture ([70b114b](https://github.com/AmbiqAI/helia-profiler/commit/70b114b0efe5ed37bce72ef230c4b075a6fcbf59)), closes [#391](https://github.com/AmbiqAI/helia-profiler/issues/391)
* allow explicit AOT development version overrides ([#515](https://github.com/AmbiqAI/helia-profiler/issues/515)) ([e6f549a](https://github.com/AmbiqAI/helia-profiler/commit/e6f549a98cfda9a0134839e4b0b2c6fe811c435e)), closes [#514](https://github.com/AmbiqAI/helia-profiler/issues/514)
* **aot:** move to ns-cmsis-nn v7.38.0 and admit heliaAOT below 0.26 ([#406](https://github.com/AmbiqAI/helia-profiler/issues/406)) ([e944de4](https://github.com/AmbiqAI/helia-profiler/commit/e944de4cf567afcfc4295c80d460d884740f4977)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393) [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* atomiq110 Ethos-U85 NPU profiling via helia-rt and helia-aot ([ccb9f18](https://github.com/AmbiqAI/helia-profiler/commit/ccb9f18988e3df4e247257f276df9a9ab5c0571f))
* atomiq110 Ethos-U85 NPU profiling via helia-rt and helia-aot ([35714dc](https://github.com/AmbiqAI/helia-profiler/commit/35714dc48df7d917447886938d745e5c1931cb7e))
* **contracts:** [#187](https://github.com/AmbiqAI/helia-profiler/issues/187) Tier 2 — real-toolchain compile gate over warm workspaces ([#225](https://github.com/AmbiqAI/helia-profiler/issues/225)) ([9c17174](https://github.com/AmbiqAI/helia-profiler/commit/9c171740662fc85400f706013e165b33489dd066))
* **deps:** adopt heliaRT 2.1.0 on CORE v7.46.0 and default to heliaAOT 0.30.0 ([#528](https://github.com/AmbiqAI/helia-profiler/issues/528)) ([bec2319](https://github.com/AmbiqAI/helia-profiler/commit/bec2319c414f599f83e3304b0cd12a59e84f3328))
* **deps:** move the baseline to heliaRT 1.21.3, ns-cmsis-nn v7.39.2 and heliaAOT 0.25.0 ([#445](https://github.com/AmbiqAI/helia-profiler/issues/445)) ([7511dff](https://github.com/AmbiqAI/helia-profiler/commit/7511dff2456c5f049f9e0353e3db6db097d1d1b5)), closes [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* **deps:** move the baseline to ns-cmsis-nn v7.38.1 ([#426](https://github.com/AmbiqAI/helia-profiler/issues/426)) ([dd73596](https://github.com/AmbiqAI/helia-profiler/commit/dd735962341f3bf70a6805d0a5fe624626c189de)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393) [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* **deps:** pin nsx-npu module and nsx-ethos-u-driver project in the baseline ([52a8eb8](https://github.com/AmbiqAI/helia-profiler/commit/52a8eb858b80ee2d0587ff738797742a619eb832))
* **engines:** [#246](https://github.com/AmbiqAI/helia-profiler/issues/246) — heliaRT 1.19.0 + helia-aot 0.19.0 for FP16/FP32 on the hpx-declared ns-cmsis-nn v7.31.0 ([#250](https://github.com/AmbiqAI/helia-profiler/issues/250)) ([02671b6](https://github.com/AmbiqAI/helia-profiler/commit/02671b67c9a2fcdc502f9415b3eef5c0833a8b0a))
* **engines:** qualify helia-aot 0.22.0, heliaRT 1.21.0 and ns-cmsis-nn v7.35.0 ([#383](https://github.com/AmbiqAI/helia-profiler/issues/383)) ([8f74f66](https://github.com/AmbiqAI/helia-profiler/commit/8f74f66f8c527459f9e49f2e7a69d8c5d1f91f81)), closes [#380](https://github.com/AmbiqAI/helia-profiler/issues/380)
* **evaluation:** confine an invalid run to the metric families its errors broke ([#300](https://github.com/AmbiqAI/helia-profiler/issues/300)) ([c47cfec](https://github.com/AmbiqAI/helia-profiler/commit/c47cfecb10a6dcb864997e095534e5aa786373da))
* expose operator timing observations without benchmark policy ([34f257f](https://github.com/AmbiqAI/helia-profiler/commit/34f257f96672f5b832706b4dd600790388d6e6f2)), closes [#482](https://github.com/AmbiqAI/helia-profiler/issues/482)
* expose typed fixture footprint and observation metrics ([#394](https://github.com/AmbiqAI/helia-profiler/issues/394)) ([010e9db](https://github.com/AmbiqAI/helia-profiler/commit/010e9dbff8a3017e6f00781818f3b9875ffa0d25)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* **fixture:** add optional bounded RAM diagnostics ([#520](https://github.com/AmbiqAI/helia-profiler/issues/520)) ([87996bf](https://github.com/AmbiqAI/helia-profiler/commit/87996bfd8bf57f36fa4888526fe6b48b9a72d816)), closes [#492](https://github.com/AmbiqAI/helia-profiler/issues/492)
* **fixture:** build fixed fixtures from pinned inputs only ([#424](https://github.com/AmbiqAI/helia-profiler/issues/424)) ([fe9be1b](https://github.com/AmbiqAI/helia-profiler/commit/fe9be1bc46f5e2fc83dbd0e2343704d72b6d0ba4)), closes [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* **fixture:** build fixtures from a typed request (API 1.1) ([#427](https://github.com/AmbiqAI/helia-profiler/issues/427)) ([0b34424](https://github.com/AmbiqAI/helia-profiler/commit/0b344241463a8e4464ff5f85178bd293a3f06998)), closes [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* **fixture:** complete measured-energy capture and configured board power ([72ce5e7](https://github.com/AmbiqAI/helia-profiler/commit/72ce5e7e5b7757ad8a6894cf431b0c6464cfe470)), closes [#505](https://github.com/AmbiqAI/helia-profiler/issues/505)
* **fixture:** expose typed analysis and paired placements ([#475](https://github.com/AmbiqAI/helia-profiler/issues/475)) ([3d7b408](https://github.com/AmbiqAI/helia-profiler/commit/3d7b408d360993ce548427a56b5011e2ba462457)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* **fixture:** expose typed kernel and prepared-runtime identity on FixtureBuild ([#527](https://github.com/AmbiqAI/helia-profiler/issues/527)) ([694cc38](https://github.com/AmbiqAI/helia-profiler/commit/694cc388a4de0466e5bc29d6054349cd5575dc3e)), closes [#526](https://github.com/AmbiqAI/helia-profiler/issues/526)
* **fixture:** heliaRT fixed fixtures on a prepared runtime archive ([#398](https://github.com/AmbiqAI/helia-profiler/issues/398)) ([7c941cb](https://github.com/AmbiqAI/helia-profiler/commit/7c941cb0f2aee533605c649941ae2e3d6472e80f)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* **fixture:** remove the fixed fixture's energy path ([#460](https://github.com/AmbiqAI/helia-profiler/issues/460)) ([2ac7894](https://github.com/AmbiqAI/helia-profiler/commit/2ac7894905adafe01318caca53a833e14ee3150d)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* **fixture:** share firmware blocks, wait for fixture completion and bind per-operator timing ([#395](https://github.com/AmbiqAI/helia-profiler/issues/395)) ([15ecec3](https://github.com/AmbiqAI/helia-profiler/commit/15ecec3e4560e501c8a5e5a674861de6d6bc5f0a)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* **fixture:** support explicit AOT mixed-memory placement ([#519](https://github.com/AmbiqAI/helia-profiler/issues/519)) ([70ae9d3](https://github.com/AmbiqAI/helia-profiler/commit/70ae9d3df78579708be84fedca2b41ab91aca30f)), closes [#492](https://github.com/AmbiqAI/helia-profiler/issues/492)
* **fixture:** typed multi-tensor fixed fixtures with heliaAOT arena scan ([#397](https://github.com/AmbiqAI/helia-profiler/issues/397)) ([4cf71e4](https://github.com/AmbiqAI/helia-profiler/commit/4cf71e4cbc3dcaa371fd4f6cfae46a09f89c89e0)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* **nix:** select and pin the heliaAOT release as a flake input ([#479](https://github.com/AmbiqAI/helia-profiler/issues/479)) ([5768218](https://github.com/AmbiqAI/helia-profiler/commit/5768218feac48e8c2353e63f61606bd80736a121)), closes [#478](https://github.com/AmbiqAI/helia-profiler/issues/478)
* **platform:** characterize the atomiq110 linked-memory map ([7ddfe01](https://github.com/AmbiqAI/helia-profiler/commit/7ddfe01cc04741c69e2e0b6e8eea4c2f65a153c1))
* **provenance:** [#291](https://github.com/AmbiqAI/helia-profiler/issues/291) — record the image each run built and the flags it was built with ([#292](https://github.com/AmbiqAI/helia-profiler/issues/292)) ([7a54500](https://github.com/AmbiqAI/helia-profiler/commit/7a54500c835d9cd20a3d84ab6955f557f107d43d))
* qualify heliaAOT 0.23.0 and heliaRT 1.21.2 on ns-cmsis-nn v7.36.1 ([#396](https://github.com/AmbiqAI/helia-profiler/issues/396)) ([bbd217c](https://github.com/AmbiqAI/helia-profiler/commit/bbd217c8dd033375edfb45245bd7c22fbaf81273)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* qualify heliaRT 1.20.0, heliaAOT 0.20.0 and ns-cmsis-nn v7.32.0 ([#279](https://github.com/AmbiqAI/helia-profiler/issues/279)) ([#280](https://github.com/AmbiqAI/helia-profiler/issues/280)) ([560665d](https://github.com/AmbiqAI/helia-profiler/commit/560665d3d7f5d9c55b9a1bd3a5ed270479a8e70c))
* **report:** record the compiler's selected CORE calls per layer ([#522](https://github.com/AmbiqAI/helia-profiler/issues/522)) ([aa264cb](https://github.com/AmbiqAI/helia-profiler/commit/aa264cb53c397f1c1f6762ee47afc63a45a6879c)), closes [#482](https://github.com/AmbiqAI/helia-profiler/issues/482)
* **results:** add the engine_backend comparability dimension ([5464b1c](https://github.com/AmbiqAI/helia-profiler/commit/5464b1c20cd5747a2eaed76303f299088cbbebb1))
* **results:** add verified offline profile readers and portable exports ([#516](https://github.com/AmbiqAI/helia-profiler/issues/516)) ([160dcc7](https://github.com/AmbiqAI/helia-profiler/commit/160dcc71df4ccd46b63c21e104eae1c0cf36408a)), closes [#513](https://github.com/AmbiqAI/helia-profiler/issues/513)
* **runtimes:** accept heliaAOT 0.30.0 in the supported range ([#523](https://github.com/AmbiqAI/helia-profiler/issues/523)) ([c12c154](https://github.com/AmbiqAI/helia-profiler/commit/c12c154b7a803828925289a2879ce1b278a3ed92)), closes [#482](https://github.com/AmbiqAI/helia-profiler/issues/482)
* **runtimes:** prepare the heliaRT runtime archive from its record ([#453](https://github.com/AmbiqAI/helia-profiler/issues/453)) ([b195e29](https://github.com/AmbiqAI/helia-profiler/commit/b195e29fa179986338d73acbad18e4ee70cab669)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* **runtimes:** prepare the TFLM runtime archive from its record ([#454](https://github.com/AmbiqAI/helia-profiler/issues/454)) ([8fa4781](https://github.com/AmbiqAI/helia-profiler/commit/8fa4781eba9d36f232660ae582d5b0d28e49b837)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* **runtimes:** runtime records and qualification lookup ([#451](https://github.com/AmbiqAI/helia-profiler/issues/451)) ([540ac11](https://github.com/AmbiqAI/helia-profiler/commit/540ac1146d411d6a3c20995e23cbcfb6e42fcabd)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* support plain INT32 fixture inputs ([f76a12f](https://github.com/AmbiqAI/helia-profiler/commit/f76a12f11d2a47c03eaf0bee7eb376192bcb8848)), closes [#472](https://github.com/AmbiqAI/helia-profiler/issues/472)


### Bug Fixes

* align user-facing guidance and model memory output with behavior ([1c2c89c](https://github.com/AmbiqAI/helia-profiler/commit/1c2c89c101db86fba3d74377240b34bce3e4e073))
* **analyze:** label JSON graphs correctly and fix CSV export ([2e4ebc4](https://github.com/AmbiqAI/helia-profiler/commit/2e4ebc410e76d7fe13ec85f808cf79089e143d83))
* **analyze:** label JSON graphs correctly and reject --compare with CSV ([180076e](https://github.com/AmbiqAI/helia-profiler/commit/180076e58feb7f438bfded2b9398e38b7aafa21d))
* **aot:** resolve board targets consistently ([3ac0f4d](https://github.com/AmbiqAI/helia-profiler/commit/3ac0f4d4e6c08a7f513ca506461db67a750f4a34)), closes [#463](https://github.com/AmbiqAI/helia-profiler/issues/463)
* **capture:** attribute a failed clock probe and SWO clock mismatches ([3e37c5b](https://github.com/AmbiqAI/helia-profiler/commit/3e37c5bda17d42bb8667fe788d5a734c61f22bf6))
* **capture:** check every pass's iterations and bound SWO retries ([f56050c](https://github.com/AmbiqAI/helia-profiler/commit/f56050cdfddd86c92411fedfa9ff61f73f90c1ef))
* **capture:** explain mismatched layer identities ([d165553](https://github.com/AmbiqAI/helia-profiler/commit/d1655539b897f2c26566827ecaa6b2dea23000c9))
* **capture:** only whole lines refresh the heartbeat deadline ([35d9a8b](https://github.com/AmbiqAI/helia-profiler/commit/35d9a8b9718c66cd210acf084a349647028b4c74))
* **capture:** preserve layer identity in PMU aggregation ([8f01bb8](https://github.com/AmbiqAI/helia-profiler/commit/8f01bb884edb21c1649fc7b23c48f186e45a5977))
* **capture:** preserve layer identity in PMU aggregation ([45efd7d](https://github.com/AmbiqAI/helia-profiler/commit/45efd7d4410fc8cf0671f6d48d41d44981474988))
* **capture:** reject any empty iteration block ([1d8d568](https://github.com/AmbiqAI/helia-profiler/commit/1d8d5681e23f7be596cf11c24120ecfee9fbbd9c))
* **capture:** reject truncated captures and honour transport timeouts ([8995428](https://github.com/AmbiqAI/helia-profiler/commit/8995428bcf4afffb8deab0a1338251d0b5a19609))
* **capture:** reject truncated captures and honour transport timeouts ([ce50363](https://github.com/AmbiqAI/helia-profiler/commit/ce5036340860b291275d0178aeeb5f68d427ab23))
* **capture:** RTT error hints, GPI read failures, empty source paths ([e62e986](https://github.com/AmbiqAI/helia-profiler/commit/e62e986e596f96d12c50371a275a06b52e1eb7dd))
* **capture:** warn on a zero clock reading ([67d57e9](https://github.com/AmbiqAI/helia-profiler/commit/67d57e9624e24730925e1e8fc9e6e91cc06e1bc2))
* **ci:** [#293](https://github.com/AmbiqAI/helia-profiler/issues/293) — workflow shell must parse on bash 3.2, not just bash 4 ([#294](https://github.com/AmbiqAI/helia-profiler/issues/294)) ([ad6671b](https://github.com/AmbiqAI/helia-profiler/commit/ad6671bc726558dc69c8cf2cdd66b7583308f4b6))
* **ci:** encode validation revision metadata safely ([16c13db](https://github.com/AmbiqAI/helia-profiler/commit/16c13dbac9c379bfed8cb49f6e97ff9d0cf0c270))
* **ci:** exercise baseline ns-cmsis-nn defaults in hardware validation ([713cb49](https://github.com/AmbiqAI/helia-profiler/commit/713cb49e212b8d30f8eaba042ac71241d11f8329))
* **ci:** exercise baseline ns-cmsis-nn defaults in hardware validation ([1760fbc](https://github.com/AmbiqAI/helia-profiler/commit/1760fbc96df59d22f51588d1fe273d8f75c3bd87))
* **ci:** nightly dies on jq missing from the bench runners; overwrite artifacts on re-run ([30661e8](https://github.com/AmbiqAI/helia-profiler/commit/30661e814c0e323724e0276053c1c06e063f1694))
* **ci:** validate job must not need jq; overwrite artifacts on re-run ([09167c1](https://github.com/AmbiqAI/helia-profiler/commit/09167c1b84243fbcf498c1aa037039f64ae5a5a2))
* classify CMSIS-NN overrides as baseline module replacements ([c1fabf9](https://github.com/AmbiqAI/helia-profiler/commit/c1fabf991ec2073d27ef0779956bf9667d646b74))
* classify CMSIS-NN source overrides as module replacements ([a79949b](https://github.com/AmbiqAI/helia-profiler/commit/a79949b599515455654cdc0a900ffbb54cb50114))
* **cli:** render HpxError through the console so the hint prints once ([1aaeda6](https://github.com/AmbiqAI/helia-profiler/commit/1aaeda6c176cb82a45aafa9a87b7a9f658691162))
* **cli:** render HpxError through the console so the hint prints once ([c09e522](https://github.com/AmbiqAI/helia-profiler/commit/c09e522b8931805e470a34255c3db600206d65c2))
* **cli:** replace private choice API and remove Typer cap ([#504](https://github.com/AmbiqAI/helia-profiler/issues/504)) ([04437c0](https://github.com/AmbiqAI/helia-profiler/commit/04437c0b2631e3ee278d452816a8d1d0f004f6a5)), closes [#459](https://github.com/AmbiqAI/helia-profiler/issues/459)
* **compare:** [#223](https://github.com/AmbiqAI/helia-profiler/issues/223) — per-layer memory rows join on the source index, never position ([#227](https://github.com/AmbiqAI/helia-profiler/issues/227)) ([198b31a](https://github.com/AmbiqAI/helia-profiler/commit/198b31a11dbb67217b05aaefd50f98d2c6841de5))
* **compare:** [#243](https://github.com/AmbiqAI/helia-profiler/issues/243) — survive wide CSV rows and emit valid JSON on non-finite metrics ([#244](https://github.com/AmbiqAI/helia-profiler/issues/244)) ([6f1f0b3](https://github.com/AmbiqAI/helia-profiler/commit/6f1f0b36c5554ec82b1a5aae84747aecbcdbfd02))
* **compare:** read published power measurement duration ([#307](https://github.com/AmbiqAI/helia-profiler/issues/307)) ([a9d73ee](https://github.com/AmbiqAI/helia-profiler/commit/a9d73ee91976efb2b79aff3faf47ec29a67001c7))
* **config:** reject falsy non-mapping YAML and map decode errors to ConfigError ([ef219b4](https://github.com/AmbiqAI/helia-profiler/commit/ef219b4a8bfbdaac5a7323a14d481c2225026a2a))
* **config:** resolve --frozen once and validate values at load time ([1e95aee](https://github.com/AmbiqAI/helia-profiler/commit/1e95aee8aec30886f7a5ddb8505727c9fbe4a411))
* **config:** resolve the --frozen alias into build.offline ([7e22875](https://github.com/AmbiqAI/helia-profiler/commit/7e2287599ca400839880694b63a7bbb0921f29e9))
* **config:** restrict arena_location's annotation to writable regions ([58f8167](https://github.com/AmbiqAI/helia-profiler/commit/58f81673166cce3c915a7337e4d4cf961ac4a4f0))
* **config:** validate board, arena and placement values at load time ([7d25e75](https://github.com/AmbiqAI/helia-profiler/commit/7d25e7522497d3782c1c9216ef4b3e628f71ba9e))
* correct board memory and clock consumers ([f71ea4a](https://github.com/AmbiqAI/helia-profiler/commit/f71ea4a955471b20d926cba7bd6a156d2c657ca7))
* delegate internal tensor dtype support to engines ([c233678](https://github.com/AmbiqAI/helia-profiler/commit/c233678fd0b40755e777c77dfd28b62cee4613c2))
* **deps:** resolve provider overrides from module provenance ([1e67ab3](https://github.com/AmbiqAI/helia-profiler/commit/1e67ab35fa84b0550224a5957c46513383b441c6))
* **deps:** resolve provider overrides from module provenance ([30717f4](https://github.com/AmbiqAI/helia-profiler/commit/30717f4e3924ec384b2a8dc4e7307dc9edfa0bc9))
* **deps:** scope exemption to adapter selected provider ([c420439](https://github.com/AmbiqAI/helia-profiler/commit/c42043907115724cc324c2430e0ba5b5107df12d))
* **deps:** treat an empty engine source path as unset ([f4b73c5](https://github.com/AmbiqAI/helia-profiler/commit/f4b73c5cffd1ef9750f15a7d961a0ee983a82a0e))
* **doctor:** keep ATfE lookup inside ATFE_ROOT/bin and fix host-dependent tests ([2a5dbe7](https://github.com/AmbiqAI/helia-profiler/commit/2a5dbe7a0edf0d77020baf21bb50664facbdffb1))
* **doctor:** route hpx doctor through Session and fix toolchain checks ([a65e21d](https://github.com/AmbiqAI/helia-profiler/commit/a65e21d05928e2283c63753cb8897988b10171de))
* **doctor:** route hpx doctor through Session and fix toolchain checks ([e22e3ce](https://github.com/AmbiqAI/helia-profiler/commit/e22e3ce5bc9f7bccef6582b53c7444adcd1c3af8))
* **engines:** accept modern local ns-cmsis-nn checkouts ([37f89ea](https://github.com/AmbiqAI/helia-profiler/commit/37f89ead76fefabdb51afe21dd8d3632ee679779))
* **engines:** accept modern local ns-cmsis-nn checkouts ([8091e6c](https://github.com/AmbiqAI/helia-profiler/commit/8091e6cac9ba790688d633612f77ff07c6eca3c7))
* **engines:** reject falsy non-path cmsis_nn_path values ([52e6750](https://github.com/AmbiqAI/helia-profiler/commit/52e67500e58be792da44daf1b68b655efde556b3))
* **engines:** reject unapplied runtime settings ([82add27](https://github.com/AmbiqAI/helia-profiler/commit/82add274388b0bda62c0830be47dedff7009c374)), closes [#432](https://github.com/AmbiqAI/helia-profiler/issues/432)
* **engines:** reject unused prepared runtime settings ([#506](https://github.com/AmbiqAI/helia-profiler/issues/506)) ([360356c](https://github.com/AmbiqAI/helia-profiler/commit/360356c269ecdf2b214780006e2a98426602c6f2)), closes [#432](https://github.com/AmbiqAI/helia-profiler/issues/432)
* **executorch:** label layer rows with the operator name ([0c99e2b](https://github.com/AmbiqAI/helia-profiler/commit/0c99e2b75402f71521d5b7239af0c018aa64c654))
* **executorch:** label layer rows with the operator name ([#301](https://github.com/AmbiqAI/helia-profiler/issues/301)) ([332e949](https://github.com/AmbiqAI/helia-profiler/commit/332e9491f05f472cbf507f48f54b21b2beccf1b5))
* **executorch:** validate cached sources without offline sync ([c20b12e](https://github.com/AmbiqAI/helia-profiler/commit/c20b12e56b3cdddcd839d21a20a1d77ff97aa4e6))
* **executorch:** validate cached sources without offline sync ([fddb68f](https://github.com/AmbiqAI/helia-profiler/commit/fddb68f645660be9885f8f08e111b21b22f61d9b))
* **firmware:** bound the clock probe and reject a frozen DWT ([24e7a39](https://github.com/AmbiqAI/helia-profiler/commit/24e7a39e5bbc58a872872ce93648597aecf02060))
* **firmware:** clamp negative PMU counter-name counts ([7474314](https://github.com/AmbiqAI/helia-profiler/commit/7474314330fa38583f99be470374daf031a1cff0)), closes [#469](https://github.com/AmbiqAI/helia-profiler/issues/469)
* **firmware:** gate NPU profiler accessors behind has_ethos_u to preserve power fingerprints ([b0070a6](https://github.com/AmbiqAI/helia-profiler/commit/b0070a64286e2af27dbe1fe4f4ed63366ae8e6a3))
* **firmware:** keep app-local RTT sources writable ([b400cba](https://github.com/AmbiqAI/helia-profiler/commit/b400cbac597e0b092c61eb8be3169bed159c1b1e)), closes [#444](https://github.com/AmbiqAI/helia-profiler/issues/444)
* **firmware:** keep atomiq110 RTT buffers in non-cached TCM ([e633acd](https://github.com/AmbiqAI/helia-profiler/commit/e633acd649d6b78ecae23abee3e627f0088950db))
* **firmware:** measure the core clock against STIMER ([fe4687e](https://github.com/AmbiqAI/helia-profiler/commit/fe4687ef8c09092eb65b67eade5451ba7290b591))
* **firmware:** measure the core clock against STIMER ([8a6a8de](https://github.com/AmbiqAI/helia-profiler/commit/8a6a8de052da86b8a09a8702e6229141563501a1))
* **firmware:** report layer overflow and read PMU overflow before reset ([2657d58](https://github.com/AmbiqAI/helia-profiler/commit/2657d580d374b7e416c754afd437434af4a25bdd))
* **firmware:** report layer overflow and read PMU overflow before reset ([01f2601](https://github.com/AmbiqAI/helia-profiler/commit/01f26012d21d9a5fc5a7e4ecb9d33305b921a360))
* **firmware:** report per-layer NPU dispatch and pin the NPU capture contract ([84ccaf6](https://github.com/AmbiqAI/helia-profiler/commit/84ccaf63cbf03acb1af868397e8832b831cf206f))
* **fixture:** check the measured core clock before binding operator timing ([#429](https://github.com/AmbiqAI/helia-profiler/issues/429)) ([da8ef19](https://github.com/AmbiqAI/helia-profiler/commit/da8ef193cc163cf34d6c4d66aaf3088124b7c493)), closes [#428](https://github.com/AmbiqAI/helia-profiler/issues/428)
* **fixture:** raise the flat-image cap to 2 MiB and check it at build ([#399](https://github.com/AmbiqAI/helia-profiler/issues/399)) ([fab4cdd](https://github.com/AmbiqAI/helia-profiler/commit/fab4cddd528dd14c2b333d349e0b6ef45ea83079)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393)
* **fixtures:** separate IO support from qualification evidence ([#511](https://github.com/AmbiqAI/helia-profiler/issues/511)) ([d662b11](https://github.com/AmbiqAI/helia-profiler/commit/d662b110e710a6cd6a48c124ed53850d794372d8)), closes [#482](https://github.com/AmbiqAI/helia-profiler/issues/482)
* **fixture:** support canonical targets and bounded capture extents ([#518](https://github.com/AmbiqAI/helia-profiler/issues/518)) ([782bf16](https://github.com/AmbiqAI/helia-profiler/commit/782bf169a1115b2bd431b90c63999120819ba9f8)), closes [#492](https://github.com/AmbiqAI/helia-profiler/issues/492)
* **fixture:** support engine-owned state and PSRAM weights ([#481](https://github.com/AmbiqAI/helia-profiler/issues/481)) ([2a0931a](https://github.com/AmbiqAI/helia-profiler/commit/2a0931ab778d483b152469f76c42d82c5fde277c)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* harden power peaks and gate timing against capture faults ([b352279](https://github.com/AmbiqAI/helia-profiler/commit/b3522793942ab9e1dbcd5ab57af012158708dc88))
* **helia-aot:** read memory config from config_path in preflight and prepare ([e7f281c](https://github.com/AmbiqAI/helia-profiler/commit/e7f281c1dd46b72265ae7d2ce564e4ac33b6fe79))
* **helia-aot:** read memory config from config_path in preflight and prepare ([884c859](https://github.com/AmbiqAI/helia-profiler/commit/884c8592c4d093775238e6c8f9a92303286f8be5))
* **helia-aot:** reject non-mapping and non-UTF-8 config_path documents ([4d30f5d](https://github.com/AmbiqAI/helia-profiler/commit/4d30f5d016f4ed2253e21094381639bc7d31621d))
* **helia-rt:** confine cache key and harden stale-cache replacement ([67a8b30](https://github.com/AmbiqAI/helia-profiler/commit/67a8b300d3bb8aba2e3e55ab63103aa9e02ab527))
* **helia-rt:** contain and atomically install downloaded distributions ([ac7b48e](https://github.com/AmbiqAI/helia-profiler/commit/ac7b48e3de2d4c5cc98f2392afd93845e6398101))
* **helia-rt:** contain and atomically install downloaded distributions ([5cdf97b](https://github.com/AmbiqAI/helia-profiler/commit/5cdf97bd9be773c6a5fbc2f1626cb70f2cdeb297))
* **helia-rt:** honour the variant on the registry path ([c6f168c](https://github.com/AmbiqAI/helia-profiler/commit/c6f168c3273e945c9a9d8eaabd18e76c875ce74a))
* **helia-rt:** make the heliaRT cache key injective ([4a7912e](https://github.com/AmbiqAI/helia-profiler/commit/4a7912e19ce0fdb2d93ce5d2610294e873bbd508))
* **helia-rt:** pass the variant to the registry module build ([c81a606](https://github.com/AmbiqAI/helia-profiler/commit/c81a60632f00fa112d7b2f4e29b64409b032cac4))
* **helia-rt:** refuse an Ethos-U build from a source tree without the NSX flag ([#378](https://github.com/AmbiqAI/helia-profiler/issues/378)) ([313af4d](https://github.com/AmbiqAI/helia-profiler/commit/313af4d419565eb8e8b322e34b94094cf7a1d485)), closes [#314](https://github.com/AmbiqAI/helia-profiler/issues/314)
* **helia-rt:** refuse ineffective configuration requests ([334fa82](https://github.com/AmbiqAI/helia-profiler/commit/334fa82fb4de594c1ad1d5f415d9448a0fa75a0e))
* **helia-rt:** reject the release variant ([b26e70c](https://github.com/AmbiqAI/helia-profiler/commit/b26e70cdc2ef7797323ca83cc40fca5002d32598))
* **io:** enforce explicit encodings and Unicode round trips ([#507](https://github.com/AmbiqAI/helia-profiler/issues/507)) ([dbfcd8b](https://github.com/AmbiqAI/helia-profiler/commit/dbfcd8b5ae113dc1f8f0bd67654261df635cb9b3)), closes [#405](https://github.com/AmbiqAI/helia-profiler/issues/405)
* **modelcost:** correct TRANSPOSE_CONV and FULLY_CONNECTED MAC counts ([ed178a3](https://github.com/AmbiqAI/helia-profiler/commit/ed178a349b140b36b603a3f169cc136290fc950a))
* **modelcost:** correct TRANSPOSE_CONV and FULLY_CONNECTED MAC counts ([9832a9a](https://github.com/AmbiqAI/helia-profiler/commit/9832a9a8c5f83afeabe99f69838f5f919ad461f7))
* **modelcost:** reject TFLite offsets outside the buffer ([#375](https://github.com/AmbiqAI/helia-profiler/issues/375)) ([6226a4d](https://github.com/AmbiqAI/helia-profiler/commit/6226a4d5d2474ccd9be6832bed9bcef7a9b60d54)), closes [#239](https://github.com/AmbiqAI/helia-profiler/issues/239)
* **modelcost:** share op-cost tables between the TFLite and AOT analysers ([4f200c8](https://github.com/AmbiqAI/helia-profiler/commit/4f200c87fd340f7338f760ac5d4275c5ef218229))
* **modelcost:** share op-cost tables between the TFLite and AOT analysers ([b588c16](https://github.com/AmbiqAI/helia-profiler/commit/b588c168cd1a53238f8c3574e4d74d5b9673ba00))
* **nix:** automate J-Link setup and document USB access ([6468049](https://github.com/AmbiqAI/helia-profiler/commit/6468049e6eb55df573fb2e71ce69d3ecbee5955c))
* **nix:** simplify J-Link setup and document contributor and USB access ([f9e0747](https://github.com/AmbiqAI/helia-profiler/commit/f9e0747c014173e0af1090f2f15197473c3c9809))
* **npu:** address Copilot review — plan NPU tables, EXT_WR counter, NPU wire scope ([b4b65ba](https://github.com/AmbiqAI/helia-profiler/commit/b4b65baf2b33467936ee14a2b326b708ca236f33))
* **npu:** address review — tolerate_power_ack, TCM placement, custom-soc npu, overflow tracking, wire catalogue ([6c6e9c3](https://github.com/AmbiqAI/helia-profiler/commit/6c6e9c3eba5a9dfbda8066f18cad329ebbec6ee7))
* **npu:** gate ethos_npu on resolved counter groups, merge multi-pass parsing ([affc960](https://github.com/AmbiqAI/helia-profiler/commit/affc9607aa7e4b6e3b39b84d2b63c0272ee08ba7))
* **npu:** keep PMU tables out of the power binary, reject FPGA power capture ([273a2f4](https://github.com/AmbiqAI/helia-profiler/commit/273a2f445edd6c31580a666501ac95db429be7d8))
* omit rejected PMU counters and pair cache hit-rate inputs ([e7bf3b7](https://github.com/AmbiqAI/helia-profiler/commit/e7bf3b75f7654d0f0e387a766c947aae283d4d79))
* **pipeline:** keep workspace cleanup best effort ([bae148f](https://github.com/AmbiqAI/helia-profiler/commit/bae148f8c0c5a7d981af5e6ca1ed25d5ecc78d27))
* **pipeline:** serialize shared workspaces across complete runs ([be5c475](https://github.com/AmbiqAI/helia-profiler/commit/be5c47501339ffb8cebc098c5c0c94c07310941b))
* **pipeline:** serialize shared workspaces across complete runs ([6dbfd0b](https://github.com/AmbiqAI/helia-profiler/commit/6dbfd0bad74dc01a2415dd1ea8c48039e746c537))
* **plan:** book 40-byte ExecuTorch layer records after the OperatorEvent ABI change ([385ec08](https://github.com/AmbiqAI/helia-profiler/commit/385ec08e10ba64778a31a56940460faceb6450fc))
* **platform:** inherit placement bases for based_on SoCs, strict is_fpga parsing ([b1b670e](https://github.com/AmbiqAI/helia-profiler/commit/b1b670efbe2c1ac8da89f4e2ef6a0df1d37e654f))
* **power:** account for reference stalls in internal collection ([24cb048](https://github.com/AmbiqAI/helia-profiler/commit/24cb048bf5090029e86394779032708ebe7d5a40)), closes [#302](https://github.com/AmbiqAI/helia-profiler/issues/302)
* **power:** bound the READY wait and stop the capture on a failed hook ([#372](https://github.com/AmbiqAI/helia-profiler/issues/372)) ([6bc1578](https://github.com/AmbiqAI/helia-profiler/commit/6bc157812ae7c3a77376e1864717fc7a7da2a388)), closes [#302](https://github.com/AmbiqAI/helia-profiler/issues/302)
* **power:** close the capture timeline follow-ups for warm-up, stalls, hints and lock-step ([#447](https://github.com/AmbiqAI/helia-profiler/issues/447)) ([7a6cea1](https://github.com/AmbiqAI/helia-profiler/commit/7a6cea1913897e5e979b8468a5c7e14b3c0775a0)), closes [#302](https://github.com/AmbiqAI/helia-profiler/issues/302) [#370](https://github.com/AmbiqAI/helia-profiler/issues/370) [#373](https://github.com/AmbiqAI/helia-profiler/issues/373)
* **power:** correct gated packet duration and timebase diagnostics ([#304](https://github.com/AmbiqAI/helia-profiler/issues/304)) ([2f11532](https://github.com/AmbiqAI/helia-profiler/commit/2f11532a829dfa45dc3cb7d973f46aeb74aeb4da))
* **power:** count failed GPI reads instead of swallowing them ([9206c4c](https://github.com/AmbiqAI/helia-profiler/commit/9206c4cf5f63b594b5730cd9d8a813bb79d4a784))
* **power:** diagnose unsupported Joulescope environments ([#503](https://github.com/AmbiqAI/helia-profiler/issues/503)) ([8cf7888](https://github.com/AmbiqAI/helia-profiler/commit/8cf7888dba59a187071a73a3d9762f066b028aa9)), closes [#228](https://github.com/AmbiqAI/helia-profiler/issues/228)
* **power:** log pre-record diagnostics on UART/USB terminal collection ([0b1015c](https://github.com/AmbiqAI/helia-profiler/commit/0b1015c6c5fdead002fb1e2edd74a6fc5eb1e43a))
* **power:** log pre-record diagnostics on UART/USB terminal collection ([fe0a63d](https://github.com/AmbiqAI/helia-profiler/commit/fe0a63d09c3546264b6a5dd0be68cf2f294b963c))
* **power:** offer the registered drivers on the CLI and drop the ondevice stub ([da6ec08](https://github.com/AmbiqAI/helia-profiler/commit/da6ec0840f1bc00774ef84dd1413209f0160ba00))
* **power:** offer the registered drivers on the CLI and drop the ondevice stub ([3516648](https://github.com/AmbiqAI/helia-profiler/commit/351664843d8dd04715a2b31cfaae7372736eaefc))
* **power:** rank gated windows against the plan and restore the supply after an interrupted power cycle ([#448](https://github.com/AmbiqAI/helia-profiler/issues/448)) ([38e7ed2](https://github.com/AmbiqAI/helia-profiler/commit/38e7ed2ca484ec4c90c126ff5004757c0b3a1fee)), closes [#302](https://github.com/AmbiqAI/helia-profiler/issues/302)
* **power:** size the gated fall wait from the planned window ([#367](https://github.com/AmbiqAI/helia-profiler/issues/367)) ([640b50c](https://github.com/AmbiqAI/helia-profiler/commit/640b50cc575f4ff605849757c771869961045040)), closes [#302](https://github.com/AmbiqAI/helia-profiler/issues/302)
* **power:** time the gate and INA228 accumulation apart from the window ([#368](https://github.com/AmbiqAI/helia-profiler/issues/368)) ([2c43466](https://github.com/AmbiqAI/helia-profiler/commit/2c434665885c14f58021b0c225b606fc476be69e)), closes [#299](https://github.com/AmbiqAI/helia-profiler/issues/299)
* preserve immutable fixture dependency evidence ([#477](https://github.com/AmbiqAI/helia-profiler/issues/477)) ([2c8d1c3](https://github.com/AmbiqAI/helia-profiler/commit/2c8d1c3e441ee4f65dd16801a0e360adb16923dc)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* preserve UART output received during reset ([7059a27](https://github.com/AmbiqAI/helia-profiler/commit/7059a271d1921e1b35137814c15d7d82e39643c2))
* preserve UART output received during reset ([c877480](https://github.com/AmbiqAI/helia-profiler/commit/c87748089127732f3221f7c229e97d13b5f96b27))
* **provenance:** export configured compile commands ([#321](https://github.com/AmbiqAI/helia-profiler/issues/321)) ([fd2d510](https://github.com/AmbiqAI/helia-profiler/commit/fd2d510502af0b4a622b48b451ad83f969d8b230))
* read and write text files as UTF-8 on every platform ([9581d74](https://github.com/AmbiqAI/helia-profiler/commit/9581d747d944da6f951ba9fc393bf056098dedbd))
* record CMSIS-NN selectors under the provider module they replace ([e606598](https://github.com/AmbiqAI/helia-profiler/commit/e60659846b8e281bb69b91fdf900e5815b779bde))
* report actionable UART truncation diagnostics ([f4ee55a](https://github.com/AmbiqAI/helia-profiler/commit/f4ee55a3cee801be654be985d8d16d6205be98a0))
* report actionable UART truncation diagnostics ([6a0d075](https://github.com/AmbiqAI/helia-profiler/commit/6a0d075e8f97d44c45b4711884c8a9322e826760))
* report compiled AOT graph costs ([#510](https://github.com/AmbiqAI/helia-profiler/issues/510)) ([69a04fc](https://github.com/AmbiqAI/helia-profiler/commit/69a04fc0a0b8e7a32f3c5e19061b4e084c08b7de)), closes [#224](https://github.com/AmbiqAI/helia-profiler/issues/224)
* **report:** [#240](https://github.com/AmbiqAI/helia-profiler/issues/240) — TOPS scales by the window's own inference count [CRITICAL] ([#242](https://github.com/AmbiqAI/helia-profiler/issues/242)) ([919c24f](https://github.com/AmbiqAI/helia-profiler/commit/919c24fc91781d2f827c8b4dbb5f3a1ab8fc2b01))
* **report:** bump run-summary schema for the MAC fix ([9db6531](https://github.com/AmbiqAI/helia-profiler/commit/9db6531d0874dd64c772b031d011310f41dabe9b))
* **report:** bump run-summary schema version ([90c76b7](https://github.com/AmbiqAI/helia-profiler/commit/90c76b75f4171f46663527fd0d78ca1dc55baebb))
* **report:** keep reports and compare consistent on real bundles ([2d0096c](https://github.com/AmbiqAI/helia-profiler/commit/2d0096c02eb75646c9b57773a1ed33fd8d148b88))
* **report:** keep reports and compare consistent on real bundles ([6087885](https://github.com/AmbiqAI/helia-profiler/commit/60878853b54c466126295fdeb4fbf75656fe9998))
* **report:** key Model Explorer overlays by the CSV's layer attribution ([419a438](https://github.com/AmbiqAI/helia-profiler/commit/419a43863952e92089f47e1de759d08fea2d9627))
* **report:** key Model Explorer overlays by the CSV's layer attribution ([e2ed10c](https://github.com/AmbiqAI/helia-profiler/commit/e2ed10c9043f37ed69084ae5e86bd063b7855e01))
* **report:** publish null instead of zero MACs/OPS/TOPS for opaque ethos-u models ([c5547f8](https://github.com/AmbiqAI/helia-profiler/commit/c5547f807fbf778db6286bb3be2b4b48fc58a8b2))
* **report:** replace stale Model Explorer overlays and document omissions ([204c704](https://github.com/AmbiqAI/helia-profiler/commit/204c7047692e1874112644a6329d9ce648a89509))
* **report:** use next run-summary schema version ([80d9c2f](https://github.com/AmbiqAI/helia-profiler/commit/80d9c2f9cac6afdc8d1bae9a895f97d1e7e74133))
* **report:** withhold on-device energy per inference on power errors ([0f24e37](https://github.com/AmbiqAI/helia-profiler/commit/0f24e373524797350c02b7130904d1e9be9f9632))
* restore AOT inputs in repeated clean windows ([#318](https://github.com/AmbiqAI/helia-profiler/issues/318)) ([f176e39](https://github.com/AmbiqAI/helia-profiler/commit/f176e392a91e4d88c1d8b774f2902917967d2bb3))
* **results:** append measured_clock_hz to FirmwareMeta ([d598e19](https://github.com/AmbiqAI/helia-profiler/commit/d598e19de9f9d1728ec741cb57596bae25e67935))
* **runtimes:** qualify only registered targets and derive the fixture target from the platform ([#452](https://github.com/AmbiqAI/helia-profiler/issues/452)) ([8504787](https://github.com/AmbiqAI/helia-profiler/commit/85047871c1702bcf8d3377a9611674dfc761b33f)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* separate fixture support and runtime identity evidence ([427ac20](https://github.com/AmbiqAI/helia-profiler/commit/427ac20a05e785fbf9bf2cb19bd4f973891ab0a7)), closes [#483](https://github.com/AmbiqAI/helia-profiler/issues/483)
* **session:** keep yaml_path and record it from from_yaml ([cf972c7](https://github.com/AmbiqAI/helia-profiler/commit/cf972c71ec57e66c8c2b2c91870ad57b25ac5d7a))
* **stages:** address review on the flash helper and power rebuild reset ([7cdf049](https://github.com/AmbiqAI/helia-profiler/commit/7cdf0491ef599a40aef3ac2bd7d8f5c28e11298a))
* **stages:** fail fast on ethos_u backend when the model cannot be analyzed ([8302f77](https://github.com/AmbiqAI/helia-profiler/commit/8302f772ec61aea8963c6508eee12ee3a87eb66e))
* **tests:** guard Optional hint before substring assertions ([ec60068](https://github.com/AmbiqAI/helia-profiler/commit/ec600682ce6bbdb6972ae4b4ad40c4b333f0d766))
* **tests:** guard Optional hint before substring assertions ([d4c6db4](https://github.com/AmbiqAI/helia-profiler/commit/d4c6db4b26e895f45f611f9d66c5365633253640))
* **tests:** narrow ClockDomain.speed() results before reading .mhz ([38f43b2](https://github.com/AmbiqAI/helia-profiler/commit/38f43b2fbded088fce616f9dfebe34c562349ec4))
* **tests:** write simulator sources as UTF-8 ([276aec9](https://github.com/AmbiqAI/helia-profiler/commit/276aec957eef4d4ec10b93bdf336df6a4a179d46))
* **tests:** write simulator sources as UTF-8 ([f47bd7f](https://github.com/AmbiqAI/helia-profiler/commit/f47bd7f33560cd4d576b04f06ba18ce126422a36))
* **tests:** write simulator sources as UTF-8 ([5711a86](https://github.com/AmbiqAI/helia-profiler/commit/5711a86ef972dff3ea1bcac64c9eff3e6ee608ee))
* **transport:** discover USB CDC ports from pyserial on every host ([ed460a5](https://github.com/AmbiqAI/helia-profiler/commit/ed460a533b6c47e4aef89eb2b6a30412a2f16658))
* **transport:** discover USB CDC ports from pyserial on every host ([023d71c](https://github.com/AmbiqAI/helia-profiler/commit/023d71c0111618c85f61aea9e25d06533f8cb00f))
* **transport:** give RTT startup and PSRAM errors their per-code hints ([5c2eda4](https://github.com/AmbiqAI/helia-profiler/commit/5c2eda4c77f87e55e0b63413d1ea83f873fc9e93))
* **transport:** report foreign-marker CDC devices and document host-wide fallback ([7853975](https://github.com/AmbiqAI/helia-profiler/commit/785397594fd9a9e55c828c950a43331ac8c68f09))
* untrack tools/run_ap3_sweep.py — bench script swept in by [#232](https://github.com/AmbiqAI/helia-profiler/issues/232) ([#233](https://github.com/AmbiqAI/helia-profiler/issues/233)) ([c7a908a](https://github.com/AmbiqAI/helia-profiler/commit/c7a908a060f538f9c6a7323f14507fd9ed364bd9))
* UTF-8 text I/O everywhere and a single validation bundle schema constant ([a79c8b5](https://github.com/AmbiqAI/helia-profiler/commit/a79c8b56f2e1aba2000e8c7a77bb6d8afa0e63ae))
* **validation:** derive accepted bundle schema versions from the writer constant ([de5e0bd](https://github.com/AmbiqAI/helia-profiler/commit/de5e0bddf514b4d433b9f2e2dbb7f16700873ae0))
* **validation:** keep the default toolchain axis inside the NSX board module's declaration ([a5688a6](https://github.com/AmbiqAI/helia-profiler/commit/a5688a6bb6dc7c788a8dbb862e448d9f559d0d6f))
* **validation:** keep the default toolchain axis inside the NSX board module's declaration ([e586e89](https://github.com/AmbiqAI/helia-profiler/commit/e586e898d394fd61126e6b86db07508c082b3cd6)), closes [#310](https://github.com/AmbiqAI/helia-profiler/issues/310)
* **validity:** reject a run whose firmware reports a different model than HPX sent ([#282](https://github.com/AmbiqAI/helia-profiler/issues/282)) ([f6c3e7c](https://github.com/AmbiqAI/helia-profiler/commit/f6c3e7c9cf7176cd3944e9d296330ca04b547156))


### Dependencies

* qualify neuralspotx 0.8.1 (apollo4l descriptors fixed) ([4556efb](https://github.com/AmbiqAI/helia-profiler/commit/4556efba7c99fe15289016c48d955423b01b6ca8))


### Reverts

* land the docs skeleton on the docs-migration branch instead of main ([#325](https://github.com/AmbiqAI/helia-profiler/issues/325)) ([#329](https://github.com/AmbiqAI/helia-profiler/issues/329)) ([e80c7ae](https://github.com/AmbiqAI/helia-profiler/commit/e80c7aebb7a921bce1d5399cc9e83b2d0e6b2965))


### Documentation

* adopt shared mobile navigation and compact terminals ([#431](https://github.com/AmbiqAI/helia-profiler/issues/431)) ([41a2a3d](https://github.com/AmbiqAI/helia-profiler/commit/41a2a3d4edce8a4d5e0fe8199708a4e65b78a5b9))
* **agents:** comments only when the why is non-obvious ([d5a2571](https://github.com/AmbiqAI/helia-profiler/commit/d5a25712f380a6b57bb455bc99144a82e2769069))
* **agents:** list regeneration triggers as examples, not exhaustively ([2fce41c](https://github.com/AmbiqAI/helia-profiler/commit/2fce41ceecae8471435adabb6f85768001b3e131))
* **agents:** trim AGENTS.md to what agents cannot derive from the code ([e7759e0](https://github.com/AmbiqAI/helia-profiler/commit/e7759e0a8bdb242b10a4b77f0188c0dd71a44821))
* Astro site skeleton, section navigation and publishing workflow ([#325](https://github.com/AmbiqAI/helia-profiler/issues/325)) ([#328](https://github.com/AmbiqAI/helia-profiler/issues/328)) ([117150c](https://github.com/AmbiqAI/helia-profiler/commit/117150c41fa5b428db03c5fef75e4ff4eb57b4c7))
* **boards:** classify Atomiq110 FPGA support as experimental ([0faaa17](https://github.com/AmbiqAI/helia-profiler/commit/0faaa1771ab3a5f477e2fd447d672126facc0bed))
* bring comments in line with the comment policy ([1fac571](https://github.com/AmbiqAI/helia-profiler/commit/1fac57101f86012efb84bdbdf1d1fffe93932638))
* clarify compared power measurement scopes ([#319](https://github.com/AmbiqAI/helia-profiler/issues/319)) ([a822b51](https://github.com/AmbiqAI/helia-profiler/commit/a822b51c0c19421fbc1c33bb9dc14ed92e0db52c))
* correct the ethos_npu counter group and NPU quickstart guidance ([2c2754a](https://github.com/AmbiqAI/helia-profiler/commit/2c2754a1640135e5b382a8e91285e6556402e97d))
* describe CMSIS-NN selectors as module replacements in baseline doc ([3580616](https://github.com/AmbiqAI/helia-profiler/commit/35806168e52a60044a76e87b929d2618b7860932))
* describe only the registered power drivers ([5b0c032](https://github.com/AmbiqAI/helia-profiler/commit/5b0c032ba812d681add1439a27da3bb45596d5fb))
* drop phase/step planning labels and dated bench anecdotes from comments ([296c640](https://github.com/AmbiqAI/helia-profiler/commit/296c640ae7380d1223a4e44bda62e2001a53c6af))
* drop planning labels and review history from comments ([d08a75c](https://github.com/AmbiqAI/helia-profiler/commit/d08a75c39a60a217308f063c6b4357b338f78819))
* drop remaining review-history wording from comments ([aea8387](https://github.com/AmbiqAI/helia-profiler/commit/aea838702f8ebfe69d4b9eb36a54c33455fc08d5))
* **examples:** label Atomiq110 profiling examples experimental ([ddc19db](https://github.com/AmbiqAI/helia-profiler/commit/ddc19db0230587b509695d11bf23b85126589b57))
* finish profiling and runtime configuration guidance ([#509](https://github.com/AmbiqAI/helia-profiler/issues/509)) ([ace9476](https://github.com/AmbiqAI/helia-profiler/commit/ace947675189c3016c160ae7f34358db9cfd1c7c)), closes [#432](https://github.com/AmbiqAI/helia-profiler/issues/432)
* **firmware:** account for the boot clock probe's STIMER init ([e1c809d](https://github.com/AmbiqAI/helia-profiler/commit/e1c809dafd322e172cb36cca64c870fc34f52a16))
* **firmware:** drop bench evidence from the power_only printf comment ([5adbbaa](https://github.com/AmbiqAI/helia-profiler/commit/5adbbaa93aa8f3e2aab073b581c4d773a218cab2))
* **firmware:** drop the remaining dated hardware findings from templates ([242bc80](https://github.com/AmbiqAI/helia-profiler/commit/242bc807504ea698b06f11bde5f99b37fab5a552))
* **firmware:** point engine template selection at _main_template ([a192e23](https://github.com/AmbiqAI/helia-profiler/commit/a192e23abd8371a9c4adcd13787285ead9ee1caf))
* **firmware:** reattach comments the import cleanup displaced ([e2a1206](https://github.com/AmbiqAI/helia-profiler/commit/e2a1206ceb30308b90106288d60664fd4dd2be97))
* **maintainers:** say when the generated reference needs regenerating ([aca72ca](https://github.com/AmbiqAI/helia-profiler/commit/aca72caca80cc1022edf0ef62a4a127d6b21d792))
* **maintainers:** write the gated power capture timeline contract ([#446](https://github.com/AmbiqAI/helia-profiler/issues/446)) ([ecbe9f8](https://github.com/AmbiqAI/helia-profiler/commit/ecbe9f88f1c243b921b663f507b08abb75aeea8f)), closes [#384](https://github.com/AmbiqAI/helia-profiler/issues/384) [#302](https://github.com/AmbiqAI/helia-profiler/issues/302)
* mend the boards bullet split by the apollo4l note ([9e2709b](https://github.com/AmbiqAI/helia-profiler/commit/9e2709b438a378885c0495ca39c8680f4818f930))
* migrate profiler documentation and refresh profiling workflows ([ad8b71a](https://github.com/AmbiqAI/helia-profiler/commit/ad8b71a1da02c80be59146ccc2f850f87e4b45cf))
* **output:** advertise run-summary v10 in the field table and history ([3652bf1](https://github.com/AmbiqAI/helia-profiler/commit/3652bf18ce0c670ec26ce2c5847ee950101b4ab7))
* polish landing demo and setup guidance ([d2b3b15](https://github.com/AmbiqAI/helia-profiler/commit/d2b3b151f3faab28572bb32353f813f23796618c))
* **power:** correct clean_window and read_state docstrings ([88b328c](https://github.com/AmbiqAI/helia-profiler/commit/88b328c38097405637b5851c9b0b98f9a8b9f20a))
* **power:** restore the [#107](https://github.com/AmbiqAI/helia-profiler/issues/107) comment edit lost in the main merge ([4d185c1](https://github.com/AmbiqAI/helia-profiler/commit/4d185c13d82d2eaf163899fd2a7332c853f7652d))
* reference authoritative runtime records ([28b2f00](https://github.com/AmbiqAI/helia-profiler/commit/28b2f0076cd313f4878dfe6b4fc95b292c2d4d5f)), closes [#450](https://github.com/AmbiqAI/helia-profiler/issues/450)
* regenerate committed docs data for this source tree ([5913c49](https://github.com/AmbiqAI/helia-profiler/commit/5913c49c21270ae87095f8ca0ebbc2f079a8535f))
* regenerate the API reference for this source tree ([1a0fd03](https://github.com/AmbiqAI/helia-profiler/commit/1a0fd03ab84a931424f65efa51b8db5aef95fd08))
* regenerate the CLI reference for the doctor --config help ([82b1b48](https://github.com/AmbiqAI/helia-profiler/commit/82b1b482469bb0617ee2a0f57916ab6b1d59aa17))
* regenerate the CLI reference for the power driver choices ([e83fd25](https://github.com/AmbiqAI/helia-profiler/commit/e83fd25300e5d2db25137b948b5dc525af5f9f80))
* regenerate the Python API reference ([2ef5643](https://github.com/AmbiqAI/helia-profiler/commit/2ef5643b42d4600414203905c168f7ca0509c645))
* regenerate the reference ([cb893c5](https://github.com/AmbiqAI/helia-profiler/commit/cb893c5a1c0f64e659c831d9e1307354ec087ca1))
* regenerate the reference ([cbdb593](https://github.com/AmbiqAI/helia-profiler/commit/cbdb59360acab5cf5005cab5514d5e141cca15d2))
* regenerate the reference ([45ce8ed](https://github.com/AmbiqAI/helia-profiler/commit/45ce8ed16380f8ac1dd586a5d0fd0b133c2a9e4a))
* regenerate the reference ([d958d5c](https://github.com/AmbiqAI/helia-profiler/commit/d958d5cd8ba660b1690993f8238ae54ee35aca99))
* regenerate the reference after merging main ([8962273](https://github.com/AmbiqAI/helia-profiler/commit/8962273e9713798ef855c64c72810b1204946f51))
* regenerate the reference after merging main ([7c679ab](https://github.com/AmbiqAI/helia-profiler/commit/7c679abfd836116f7591dad0323846b04309c546))
* regenerate the reference after merging main ([c435f9a](https://github.com/AmbiqAI/helia-profiler/commit/c435f9a0ee75a69225aada53b361290d453e253a))
* regenerate the reference after merging main ([5c6e155](https://github.com/AmbiqAI/helia-profiler/commit/5c6e15592e43e7f16fef3d41da31349403b2e013))
* regenerate the reference after merging main ([776d76b](https://github.com/AmbiqAI/helia-profiler/commit/776d76b3230bd8e79dcbe41355d7a51073c7e5ae))
* regenerate the reference after merging main ([1e1dfcb](https://github.com/AmbiqAI/helia-profiler/commit/1e1dfcbfca1993956fd35be2b5c9887c93671320))
* regenerate the reference after merging main ([7276637](https://github.com/AmbiqAI/helia-profiler/commit/7276637475e9d17b241f6a9409ca2bb36aa3ec5d))
* regenerate the reference after merging main ([b94ed24](https://github.com/AmbiqAI/helia-profiler/commit/b94ed24673fe8a0841903e88e2abf9426be3c555))
* regenerate the reference after merging main ([85b3f99](https://github.com/AmbiqAI/helia-profiler/commit/85b3f99a3fbf69fec2a9bc330c8fce6e58de7289))
* regenerate the reference after merging main ([7d8295c](https://github.com/AmbiqAI/helia-profiler/commit/7d8295cdc51c8202bf80cf96552675f3f44783e3))
* regenerate the reference after merging main ([3273db3](https://github.com/AmbiqAI/helia-profiler/commit/3273db3d25b697145e67478336bdbfc0f4be6e40))
* regenerate the reference and wire-protocol page ([8497358](https://github.com/AmbiqAI/helia-profiler/commit/84973580735b456074c402b7a8602c641af2eac6))
* regenerate the reference for measured_clock_hz ([c78eaed](https://github.com/AmbiqAI/helia-profiler/commit/c78eaed771f389e1907f69f95cb02e94e674017e))
* regenerate the reference for ModelAnalysis.to_dict ([c2748be](https://github.com/AmbiqAI/helia-profiler/commit/c2748bebfd8d74940fa8490cde8c7a2ff6af5985))
* regenerate the reference for the PipelineError docstring ([3791548](https://github.com/AmbiqAI/helia-profiler/commit/379154877457d5be3ad740c7a141b6f83ca3271e))
* regenerate the reference for the shifted validity.py lines ([f0ed0e5](https://github.com/AmbiqAI/helia-profiler/commit/f0ed0e5710932b176720a79c05e0fd61d6cec681))
* remove remaining extraction history and stale pointers ([7465718](https://github.com/AmbiqAI/helia-profiler/commit/7465718a6b2b9d05a91d47db5350bba5148b15f0))
* restamp the generated reference ([4620d62](https://github.com/AmbiqAI/helia-profiler/commit/4620d6286f85a1f49cf500c7d33a0667f04235c5))
* restamp the generated reference ([ec00366](https://github.com/AmbiqAI/helia-profiler/commit/ec0036685de6d4f52802dae1343a9886143f1c96))
* restamp the generated reference ([6d0ea9d](https://github.com/AmbiqAI/helia-profiler/commit/6d0ea9dc1997f0ad50fd3e2695895cf126823187))
* restamp the generated reference ([d47e12e](https://github.com/AmbiqAI/helia-profiler/commit/d47e12e243e15f6823de15179a69b8800254394c))
* restamp the generated reference ([4ddd62c](https://github.com/AmbiqAI/helia-profiler/commit/4ddd62cb337cfb886683383db97c4d7a556d45e1))
* restamp the generated reference ([d19c885](https://github.com/AmbiqAI/helia-profiler/commit/d19c885bbe850792cd7a05eedfb5ba26f2b4d369))
* restamp the generated reference ([012aaf2](https://github.com/AmbiqAI/helia-profiler/commit/012aaf2ea6a1598516aecdd91dbb11ee03b08857))
* restamp the generated reference ([feec167](https://github.com/AmbiqAI/helia-profiler/commit/feec167e1b9a2b2851607cbe5aee17bc8066ced0))
* restamp the generated reference ([06d501e](https://github.com/AmbiqAI/helia-profiler/commit/06d501eed776dac9063b8c7d0b7ce248c26a4ce5))
* restamp the generated reference ([235796c](https://github.com/AmbiqAI/helia-profiler/commit/235796cac11091c80d55632a8cc454975d1d0bcf))
* restamp the generated reference ([aed6c34](https://github.com/AmbiqAI/helia-profiler/commit/aed6c3474b59a7e3eb02f487dacbd4a1e36461cd))
* restamp the generated reference ([2f18ded](https://github.com/AmbiqAI/helia-profiler/commit/2f18dede4cb369e757aed6cbb2652170aef99fff))
* restamp the generated reference ([c185e25](https://github.com/AmbiqAI/helia-profiler/commit/c185e25354ff652f923a754415ad86b2236ff178))
* restamp the generated reference ([f095310](https://github.com/AmbiqAI/helia-profiler/commit/f095310ea44907f4aefc3ae6f96dca6dd3b59a00))
* restamp the generated reference ([463ca8f](https://github.com/AmbiqAI/helia-profiler/commit/463ca8f1f17444724048867436073157517743d8))
* restamp the generated reference ([98c2300](https://github.com/AmbiqAI/helia-profiler/commit/98c2300aa57a6b9aff8d7ee8e64e50e5692c9c0a))
* restamp the generated reference ([d52be7c](https://github.com/AmbiqAI/helia-profiler/commit/d52be7c013f4364fe35fcc8a32e0afc856c42d70))
* restamp the generated reference ([4bfb645](https://github.com/AmbiqAI/helia-profiler/commit/4bfb645262f0a281e22b70004f184e98d1b27dcf))
* restamp the generated reference ([d0a61de](https://github.com/AmbiqAI/helia-profiler/commit/d0a61de819c23b39f608236e993089ec80bad260))
* restamp the generated reference ([99f53a2](https://github.com/AmbiqAI/helia-profiler/commit/99f53a2845f7dee3fd8266a833c116ba19af88a8))
* restamp the generated reference ([5347844](https://github.com/AmbiqAI/helia-profiler/commit/5347844a7b95ddd1d20bf6ea38b5add2f063b1a6))
* restamp the generated reference ([6b1c947](https://github.com/AmbiqAI/helia-profiler/commit/6b1c9478da359f31c7f67493f9e59ea8b3b84be3))
* restamp the generated reference ([6ef7bb9](https://github.com/AmbiqAI/helia-profiler/commit/6ef7bb9eb3b9cecf0902575989836c4ec2dbdac4))
* restamp the generated reference ([da322f6](https://github.com/AmbiqAI/helia-profiler/commit/da322f6cb46ec2278383af1aa8238ed836381b0d))
* restamp the generated reference ([359b381](https://github.com/AmbiqAI/helia-profiler/commit/359b381c7ac8de16e0d709a0bdef6b7ed14832a0))
* restamp the generated reference ([ced19a3](https://github.com/AmbiqAI/helia-profiler/commit/ced19a3fa0796f804f3141e9844623565e6b04fa))
* restamp the generated reference ([ada5cb0](https://github.com/AmbiqAI/helia-profiler/commit/ada5cb01af4e340dc8cb26c6fb53de09860d2c8c))
* restamp the generated reference ([da4e3f3](https://github.com/AmbiqAI/helia-profiler/commit/da4e3f36e75b6dd7079557117d770f9ba9226576))
* restamp the generated reference ([33fcda9](https://github.com/AmbiqAI/helia-profiler/commit/33fcda979030432610b561cbea59eaf0cd05dfec))
* restamp the generated reference ([09418eb](https://github.com/AmbiqAI/helia-profiler/commit/09418eb10f0e7af6f5a1c446b382fb2c3f916d7a))
* restamp the generated reference ([3f90860](https://github.com/AmbiqAI/helia-profiler/commit/3f908607dc7c735389dd649047f7bde3c62c646a))
* restamp the generated reference ([e983707](https://github.com/AmbiqAI/helia-profiler/commit/e98370714e8f698652c8da9bebd74b1fc83e6a9c))
* restamp the generated reference after merging main ([c5f9377](https://github.com/AmbiqAI/helia-profiler/commit/c5f937735f859050909cf3c38632c052f13a2a8e))
* restamp the generated reference after merging main ([3c2b7cb](https://github.com/AmbiqAI/helia-profiler/commit/3c2b7cb77ee87bc95a81b546232388654510bb7c))
* restamp the generated reference after merging main ([dcf9e4e](https://github.com/AmbiqAI/helia-profiler/commit/dcf9e4ec72d3ef33ae32da655570e20b568a1f54))
* restamp the generated reference with this branch's source tree ([adc875c](https://github.com/AmbiqAI/helia-profiler/commit/adc875cc617a81df70b0482b4e4b0bf0a05d6f3a))
* restate remaining history narration as present-tense invariants ([c976bdc](https://github.com/AmbiqAI/helia-profiler/commit/c976bdc7948e8aaed950c62d20bcf43b3e8046bd))
* select official blue Ambiq footer logo ([#468](https://github.com/AmbiqAI/helia-profiler/issues/468)) ([2535109](https://github.com/AmbiqAI/helia-profiler/commit/2535109c9e716607157324021e9c97c2bbd25261))
* **setup:** explain short compiler-cache temporary paths ([#476](https://github.com/AmbiqAI/helia-profiler/issues/476)) ([c58c6e6](https://github.com/AmbiqAI/helia-profiler/commit/c58c6e6f9e12620aaf6b72c604ca503eb8bf8c54)), closes [#449](https://github.com/AmbiqAI/helia-profiler/issues/449)
* simplify hero diagram captions ([dcad6bd](https://github.com/AmbiqAI/helia-profiler/commit/dcad6bd8bc9ff3b36b4ef039ca3ce9aa8e9d324e))
* **site:** stop stamping the source tree into committed reference ([f9be573](https://github.com/AmbiqAI/helia-profiler/commit/f9be57336cd1009414fe628814aa3aee90c82776))
* **site:** stop stamping the source tree into committed reference ([6c1d0d8](https://github.com/AmbiqAI/helia-profiler/commit/6c1d0d86c92ad977b34471953667caa5fc6b2535))
* state the adapter-path evidence for heliaAOT main on v7.38.0 ([#425](https://github.com/AmbiqAI/helia-profiler/issues/425)) ([8f2b300](https://github.com/AmbiqAI/helia-profiler/commit/8f2b300daf78c4b139ea1de5ea7517d0e0bdcb3a)), closes [#393](https://github.com/AmbiqAI/helia-profiler/issues/393) [#403](https://github.com/AmbiqAI/helia-profiler/issues/403)
* use official product marks in the landing hero ([#471](https://github.com/AmbiqAI/helia-profiler/issues/471)) ([5a3ebbd](https://github.com/AmbiqAI/helia-profiler/commit/5a3ebbd67aca5caacb953379c4181fdfdc255cb5))
* **wire:** USB CDC now holds the clean-window budget ([617d4a7](https://github.com/AmbiqAI/helia-profiler/commit/617d4a7834243c4f309e414c7be3fd8b0b911c76))

## [0.1.6](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.5...v0.1.6) (2026-08-19)


### Reporting changes for existing users

* **`binary.bss` no longer includes the linker's `.heap` reservation
  ([#24](https://github.com/AmbiqAI/helia-profiler/issues/24),
  [#131](https://github.com/AmbiqAI/helia-profiler/issues/131)).** GCC
  toolchains reserve the heap as a NOBITS section inside `.bss`, so previous
  releases over-reported static RAM usage by the heap size. `bss` now reads
  lower and the reservation appears as its own `reserved` line; the totals are
  unchanged. Size expectations pinned against 0.1.5 reports need updating.
* **`clean_window_probe: busy_loop` power runs now complete
  ([#125](https://github.com/AmbiqAI/helia-profiler/issues/125),
  [#136](https://github.com/AmbiqAI/helia-profiler/issues/136)).** The
  diagnostic probe could never finish an external capture on the default
  `firmware: dedicated` — the host expected an N-inference window against a
  single calibrated spin and rejected every run. The window-duration check
  also uses honest bands now (10% for a counted window, 25% for a predicted
  one, replacing a bound that was ±50% in practice), so a mis-sized window is
  flagged where it previously passed.
* **`hpx compare` refuses power deltas between different clean-window probes
  ([#137](https://github.com/AmbiqAI/helia-profiler/issues/137)).** A
  `busy_loop` window measures a calibrated CPU spin, not the model, so an
  infer-vs-busy_loop pair now reports
  `metric.power_power_clean_window_probe_mismatch` instead of a phantom
  regression. Baselines recorded before 0.1.6 carry no probe dimension and are
  skipped, so existing comparisons do not flip to failing.


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
