# Changelog

All notable changes to heliaPROFILER are documented in this file.

This project follows [Semantic Versioning](https://semver.org/) and uses
[Release Please](https://github.com/googleapis/release-please) to prepare
release pull requests from Conventional Commits.

## [0.1.2](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.1...v0.1.2) (2026-08-05)


### Features

* add deterministic NSX dependency workspaces ([78aab42](https://github.com/AmbiqAI/helia-profiler/commit/78aab42aab439e3e976b9bb0744ecf58e0e5b0d1))
* add hpx doctor support bundle diagnostics ([a6d37c6](https://github.com/AmbiqAI/helia-profiler/commit/a6d37c6bfb81ca1ea6a2cc4bece9172a16a14589))
* add portable Nix environment ([85267da](https://github.com/AmbiqAI/helia-profiler/commit/85267da7106174fb6cbd0d0434d744f1b32050c7))
* add typed HPX compatibility baseline ([2e4d46b](https://github.com/AmbiqAI/helia-profiler/commit/2e4d46b8db4b35ce9227fb35d1d89fbb7f2aadf5))
* automate licensed J-Link download ([3686519](https://github.com/AmbiqAI/helia-profiler/commit/3686519e125a3f5b1c7ddce12fb0c8672e6d15e1))
* expand validation resource artifacts ([e979d1a](https://github.com/AmbiqAI/helia-profiler/commit/e979d1a8ad8ab1f1e94dfad90f91afe077f93d64))
* expand validation resource artifacts ([2811f2c](https://github.com/AmbiqAI/helia-profiler/commit/2811f2c9609b0b3635851519a3c8487edc7a7f2a))
* **psram:** expose clock and timing diagnostics ([983f784](https://github.com/AmbiqAI/helia-profiler/commit/983f7847d09faa041c98b2f631e0d9b7d34eaca6))
* support ARM64 Linux and macOS in Nix flake ([617e610](https://github.com/AmbiqAI/helia-profiler/commit/617e6102ab7f007ccbdd9b4f961f9439119701fb))
* support Python 3.13 and 3.14 ([46258ef](https://github.com/AmbiqAI/helia-profiler/commit/46258ef4eef0843b0e25ee14190bd801a154d91d))
* **validation:** add rich decision summary ([88ea423](https://github.com/AmbiqAI/helia-profiler/commit/88ea4232b0bd5642d717e3068d2794546110b6ff))
* **validation:** allow ns-cmsis-nn refs in hardware CI ([b17236d](https://github.com/AmbiqAI/helia-profiler/commit/b17236d4fdb673e0493dcec45fc8efa32ac873f7))
* **validation:** allow ns-cmsis-nn refs in hardware CI ([9759524](https://github.com/AmbiqAI/helia-profiler/commit/9759524bf64fedd00bae69e17d998790bd191bff))
* **validation:** capture runtime provenance ([43380f0](https://github.com/AmbiqAI/helia-profiler/commit/43380f0e345ab0ced65af615d5742ddaaec67968))
* **validation:** capture runtime provenance ([977eb04](https://github.com/AmbiqAI/helia-profiler/commit/977eb04c78b463c52123cd840065e6a8caa461d6))
* **validation:** include TFLM CMSIS-NN in complete suite ([30097d8](https://github.com/AmbiqAI/helia-profiler/commit/30097d8c830538ea85d9286ec437b84b2939ee19))
* **validation:** include TFLM CMSIS-NN in complete suite ([9092c8d](https://github.com/AmbiqAI/helia-profiler/commit/9092c8d1a674a1f8e2b2b2f9fff7a804c5579f00))
* **validation:** record hardware run origin ([a1dcc0c](https://github.com/AmbiqAI/helia-profiler/commit/a1dcc0ca51b59f4ddd3a50f8596efd86a6fde6fe))
* **validation:** support custom model comparisons ([0ac42b3](https://github.com/AmbiqAI/helia-profiler/commit/0ac42b3794253473ccc95b5edc940006cfde131f))


### Bug Fixes

* always emit validation resources ([68ec635](https://github.com/AmbiqAI/helia-profiler/commit/68ec635e8d666637e55a0f0241b1418dee678254))
* **aot:** make placement logging defensive ([bd40fc9](https://github.com/AmbiqAI/helia-profiler/commit/bd40fc9e9315568990f0395ed8824ca3b76b27b7))
* **aot:** validate memory config shapes ([ec23bac](https://github.com/AmbiqAI/helia-profiler/commit/ec23bac1d0afc4b32c15b972418ee20f1dff393d))
* detect J-Link commander on Windows in doctor checks ([cc9cb15](https://github.com/AmbiqAI/helia-profiler/commit/cc9cb156540a44874c2015b9b19d51ab7a754a34))
* keep NSX lock build glue writable under Nix ([acbe940](https://github.com/AmbiqAI/helia-profiler/commit/acbe94079df84ca000837a60c0b0d925d7089433))
* make NSX workspaces writable under Nix ([a0c28de](https://github.com/AmbiqAI/helia-profiler/commit/a0c28de00dac7f816e2b7a02daf581f105e1c781))
* make preserved NSX CMake tree writable ([e3b09df](https://github.com/AmbiqAI/helia-profiler/commit/e3b09df8ec1145173067e63274194717923d6369))
* promote HPX qualified compatibility baseline ([be9fc4b](https://github.com/AmbiqAI/helia-profiler/commit/be9fc4b34adc3942ce0192121c4dbd289191fa42))
* publish validation power artifacts ([6621feb](https://github.com/AmbiqAI/helia-profiler/commit/6621feb0eb70b0fa6b3eb46f96bd13207160bf3d))
* publish validation power artifacts ([c5c51a4](https://github.com/AmbiqAI/helia-profiler/commit/c5c51a428260cceef53539df4ebf7aa249e86d56))
* repair cached NSX CMake permissions before render ([5ac2f76](https://github.com/AmbiqAI/helia-profiler/commit/5ac2f76043ef3035f1111bd476f7af51cca5f9e2))
* repair NSX packaged tree permissions on Linux ([ed51488](https://github.com/AmbiqAI/helia-profiler/commit/ed5148897f836a80f0de0eca6d8af8d71588e851))
* reuse parsed validation summary ([1e64d4d](https://github.com/AmbiqAI/helia-profiler/commit/1e64d4dd43776ae4958a83419b17bc1afb192274))
* update HPX compatibility baseline to neuralSPOT-X 0.7.10 ([fdc029f](https://github.com/AmbiqAI/helia-profiler/commit/fdc029f501c76a030b5da8961b49787cb2216fbd))
* use llvm-size for ATFE binary probes ([8800cf9](https://github.com/AmbiqAI/helia-profiler/commit/8800cf9905fef573b11bb46a005f5c1869334ac1))
* **validation:** keep Apollo330 power disabled ([fd68263](https://github.com/AmbiqAI/helia-profiler/commit/fd6826315121a95672a20272f44d2f6a4dc46f97))
* **validation:** keep Apollo330 power disabled ([93f2b63](https://github.com/AmbiqAI/helia-profiler/commit/93f2b63627e9431f9db18ae42baf4e67b3018d63))
* **validation:** pin engine module revisions in NSX registry ([337bf42](https://github.com/AmbiqAI/helia-profiler/commit/337bf4200c7a9b4af6c3725f03e28a8758ffd204))
* **validation:** pin engine module revisions in NSX registry ([4c8c253](https://github.com/AmbiqAI/helia-profiler/commit/4c8c25382d6a90aa251c47f8add0ae24ecd493f2))
* **validation:** pin source revisions to commits ([e3044f2](https://github.com/AmbiqAI/helia-profiler/commit/e3044f2e728705e2654000b8dfed79fdca09a8ab))
* **validation:** pin source revisions to commits ([80c969b](https://github.com/AmbiqAI/helia-profiler/commit/80c969bdee32261e1df1bd02bc22da917ca4a53d))
* **validation:** preserve default source resolution ([c53e062](https://github.com/AmbiqAI/helia-profiler/commit/c53e0626722ae05f4dcc5c1ddc4020dd60e46d7f))
* **validation:** preserve default source resolution ([e38a616](https://github.com/AmbiqAI/helia-profiler/commit/e38a61697b5d5a9dcb3e6313a09a3d3a9732cc77))
* **validation:** propagate ns-cmsis-nn commit ([189d011](https://github.com/AmbiqAI/helia-profiler/commit/189d011be2d5ecfe446fe7d9da6b727e7ca12885))
* **validation:** propagate ns-cmsis-nn commit ([f0d77e8](https://github.com/AmbiqAI/helia-profiler/commit/f0d77e8f224948a4ab7134dfe576920ec96ea3b0))
* **validation:** restore Apollo330 hardware coverage ([da7c6aa](https://github.com/AmbiqAI/helia-profiler/commit/da7c6aa02be9d3f6124755fbc51206f38d5b0281))
* **validation:** restore Apollo330 hardware coverage ([3fbc9d6](https://github.com/AmbiqAI/helia-profiler/commit/3fbc9d6686ffbb59409e7ecf9e0f08defd0b8f11))
* **validation:** restore custom model indentation ([96d782e](https://github.com/AmbiqAI/helia-profiler/commit/96d782e47eae7d21c975904eb045759ece7c94a1))
* **validation:** support schema 4 bundles ([78f164e](https://github.com/AmbiqAI/helia-profiler/commit/78f164e79dd6107edc54422fc6c3216efb910389))


### Documentation

* add generated quick install guide ([bd7070b](https://github.com/AmbiqAI/helia-profiler/commit/bd7070b82fe78e64cef9c2fb5467301a855d1731))
* refresh public feature documentation ([0250b14](https://github.com/AmbiqAI/helia-profiler/commit/0250b1459559b013263d561df5857f0009e7ebf2))
* refresh public feature documentation ([0c6cb38](https://github.com/AmbiqAI/helia-profiler/commit/0c6cb382beb0ee562e4297aa46b1eb97b5f97cec))
* simplify Nix setup ([797d87b](https://github.com/AmbiqAI/helia-profiler/commit/797d87b921b2964ecc696eb72ea03b12170b1616))
* Windows install guidance ([333095d](https://github.com/AmbiqAI/helia-profiler/commit/333095d3d5fccc70c24806ad4b4f0871840c3dd2))

## 0.1.1 (2026-07-19)


### Features

* **release:** add automated PyPI publishing ([0fb627b](https://github.com/AmbiqAI/helia-profiler/commit/0fb627b3d4f70e8ba35d7fb766125630b4cb4767))

## [Unreleased]
