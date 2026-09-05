# Changelog

All notable changes to heliaPROFILER are documented in this file.

This project follows [Semantic Versioning](https://semver.org/) and uses
[Release Please](https://github.com/googleapis/release-please) to prepare
release pull requests from Conventional Commits.

## [0.2.0](https://github.com/AmbiqAI/helia-profiler/compare/v0.1.6...v0.2.0) (2026-09-05)


### ⚠ BREAKING CHANGES

* **power:** #142/#181 — observer check authoritative, est*count demoted to drift diagnostic ([#195](https://github.com/AmbiqAI/helia-profiler/issues/195))
* **memory:** summary.json schema_version 2 -> 3. The memory_plan block is now a pure DECISION RECORD — free/overflow/has_overflow are gone from its serialization (the model's properties remain for the plan_memory stage's plan-time capacity check). The plan only counts what hpx placed, so its free was overstated and its overflow could not fire on real exhaustion — the #133 pathology.
* **config:** config classes now hold enum members for these four fields; code constructing ProfilingConfig/PowerConfig directly with invalid strings fails at construction (valid strings coerce). YAML users are unaffected.
* **power:** ProfileResult.power.metadata is a typed PowerMetadata, no longer a dict. String-keyed reads become attribute access (metadata.measurement_scope); the flat dict view is metadata.to_metadata_dict().
* **platform:** a `target.custom_socs` entry that declares neither `based_on:` nor `app_flash_load_addr:` no longer inherits its family's app flash load address -- it resolves to no address at all, and the J-Link fallback flash refuses to run rather than programming at a guessed offset. The values that stop being handed out are 0x00410000 (ap5), 0x00018000 (ap4) and 0x0000c000 (ap3). Add either key to such an entry to restore an address: `based_on: <characterised part>` inherits one, `app_flash_load_addr: 0x…` states one. Entries that already name a `based_on` are unaffected. The same applies to the programmatic path: `build_platform_registry(socs={SocDef(...)})` has no `based_on` mechanism, so a hand-built `SocDef` must now pass `app_flash_load_addr=` explicitly.

### Features

* adopt ty type checking in CI — 212 diagnostics cleared, 8 real bugs fixed ([#199](https://github.com/AmbiqAI/helia-profiler/issues/199)) ([#200](https://github.com/AmbiqAI/helia-profiler/issues/200)) ([fd9dcc8](https://github.com/AmbiqAI/helia-profiler/commit/fd9dcc81226f21ac18d91a5ffcf6062d8b444b7b))
* **compare:** [#193](https://github.com/AmbiqAI/helia-profiler/issues/193) — ENGINE_VERSION comparability dimension + baseline-doc drift guard ([#207](https://github.com/AmbiqAI/helia-profiler/issues/207)) ([fcb1481](https://github.com/AmbiqAI/helia-profiler/commit/fcb1481000c1bacaee2459e24bb377888defa154))
* **compare:** [#206](https://github.com/AmbiqAI/helia-profiler/issues/206) — per-region memory rows gated on link family ([#213](https://github.com/AmbiqAI/helia-profiler/issues/213)) ([3274591](https://github.com/AmbiqAI/helia-profiler/commit/3274591874a68213a908ed3b2cb9b84eeaf48a0b))
* **compare:** power comparability keys on the MEASURED binary — firmware code fingerprint ([#138](https://github.com/AmbiqAI/helia-profiler/issues/138), [#115](https://github.com/AmbiqAI/helia-profiler/issues/115)) ([#173](https://github.com/AmbiqAI/helia-profiler/issues/173)) ([a9a5011](https://github.com/AmbiqAI/helia-profiler/commit/a9a50113b89d776c422114561d2d5878dafee347))
* **compatibility:** promote heliaRT 1.17.0 ([#89](https://github.com/AmbiqAI/helia-profiler/issues/89)) ([a6a9efa](https://github.com/AmbiqAI/helia-profiler/commit/a6a9efaf7e1e8fe85a01470efdb1a28141211a1d))
* **console:** [#197](https://github.com/AmbiqAI/helia-profiler/issues/197) — validity footer + opt-in fail-on-invalid exit policy ([#208](https://github.com/AmbiqAI/helia-profiler/issues/208)) ([4c4c228](https://github.com/AmbiqAI/helia-profiler/commit/4c4c228fe57373d7d46ebf268de142102f6b9b20))
* **contracts:** [#187](https://github.com/AmbiqAI/helia-profiler/issues/187) Tier 1 — stub-header compile gate for rendered firmware ([#188](https://github.com/AmbiqAI/helia-profiler/issues/188)) ([066bbf0](https://github.com/AmbiqAI/helia-profiler/commit/066bbf092860f04dbdd937cfb2eda19b63ba7079))
* **contracts:** [#187](https://github.com/AmbiqAI/helia-profiler/issues/187) Tier 2 — real-toolchain compile gate over warm workspaces ([#225](https://github.com/AmbiqAI/helia-profiler/issues/225)) ([9c17174](https://github.com/AmbiqAI/helia-profiler/commit/9c171740662fc85400f706013e165b33489dd066))
* **engines:** [#246](https://github.com/AmbiqAI/helia-profiler/issues/246) — heliaRT 1.19.0 + helia-aot 0.19.0 for FP16/FP32 on the hpx-declared ns-cmsis-nn v7.31.0 ([#250](https://github.com/AmbiqAI/helia-profiler/issues/250)) ([02671b6](https://github.com/AmbiqAI/helia-profiler/commit/02671b67c9a2fcdc502f9415b3eef5c0833a8b0a))
* **engines:** auto-clone nsx-executorch from the compatibility baseline ([0f480ae](https://github.com/AmbiqAI/helia-profiler/commit/0f480aea6446677c5a82faff5c13e5e17b2b9a3d))
* **engines:** auto-clone nsx-executorch from the compatibility baseline ([44e2b21](https://github.com/AmbiqAI/helia-profiler/commit/44e2b2120b88d115d9227e85ce36cba2bdcfef6b)), closes [#160](https://github.com/AmbiqAI/helia-profiler/issues/160)
* **memory:** [#133](https://github.com/AmbiqAI/helia-profiler/issues/133) Phase 1 — measured section inventory + verified linked-memory map ([#176](https://github.com/AmbiqAI/helia-profiler/issues/176)) ([449ea41](https://github.com/AmbiqAI/helia-profiler/commit/449ea4174c4a5b5f8b67a65ec6dd4bf639f9aa1f))
* **memory:** [#133](https://github.com/AmbiqAI/helia-profiler/issues/133) Phase 2 — measured memory_regions owns region truth ([#177](https://github.com/AmbiqAI/helia-profiler/issues/177)) ([74b5d0b](https://github.com/AmbiqAI/helia-profiler/commit/74b5d0b4ea0cefb1a9f4e354a7dea222f51b6168))
* **memory:** [#133](https://github.com/AmbiqAI/helia-profiler/issues/133) Phase 3 — symbol attribution, planned hpx consumers, reconciliation ([#179](https://github.com/AmbiqAI/helia-profiler/issues/179)) ([15b09af](https://github.com/AmbiqAI/helia-profiler/commit/15b09af38d8f888191395118f8fe3448e3d7b259))
* **platform:** let custom SoCs declare their own app flash load address ([#149](https://github.com/AmbiqAI/helia-profiler/issues/149)) ([#153](https://github.com/AmbiqAI/helia-profiler/issues/153)) ([efae917](https://github.com/AmbiqAI/helia-profiler/commit/efae917d7afa0b7d58704a4d13b6d76e61503351))
* **power:** [#142](https://github.com/AmbiqAI/helia-profiler/issues/142)/[#181](https://github.com/AmbiqAI/helia-profiler/issues/181) — observer check authoritative, est*count demoted to drift diagnostic ([#195](https://github.com/AmbiqAI/helia-profiler/issues/195)) ([994a046](https://github.com/AmbiqAI/helia-profiler/commit/994a046f2a9adef8dc69b00261d0a24b65fb3f3d))
* **preflight:** reject int8 Softmax scales the target cannot prepare ([#57](https://github.com/AmbiqAI/helia-profiler/issues/57)) ([#143](https://github.com/AmbiqAI/helia-profiler/issues/143)) ([66d76c6](https://github.com/AmbiqAI/helia-profiler/commit/66d76c676641011ca8ab8c60c8d0d48ab983444a))
* **results:** [#202](https://github.com/AmbiqAI/helia-profiler/issues/202) Part A — RunSummary typed model owns the summary.json schema ([#205](https://github.com/AmbiqAI/helia-profiler/issues/205)) ([7c3f3f5](https://github.com/AmbiqAI/helia-profiler/commit/7c3f3f5e2cc0505c04b222d1a392c4ba90ce09c2))
* **validation:** source MLPerf Tiny ExecuTorch fixtures from INT8 .pt2 via helia-torch ([58c8dc0](https://github.com/AmbiqAI/helia-profiler/commit/58c8dc064a72c4f7544d63bc95a8a5d04241e7f7))
* **validation:** source MLPerf Tiny ExecuTorch fixtures from INT8 .pt2 via helia-torch ([9980f22](https://github.com/AmbiqAI/helia-profiler/commit/9980f22216fed3fdfbc4a485bdab6c6926c95f23))


### Bug Fixes

* backlog sweep — deterministic binary resolution, records modeling, [#110](https://github.com/AmbiqAI/helia-profiler/issues/110) stimer attribution ([#180](https://github.com/AmbiqAI/helia-profiler/issues/180)) ([3fb6791](https://github.com/AmbiqAI/helia-profiler/commit/3fb6791952a502de1d9c044c3995b7d55e184baa))
* **cache:** overridable cache root — read-only $HOME crashed every CI case ([1ce6e05](https://github.com/AmbiqAI/helia-profiler/commit/1ce6e0589ac5a72effa5b273143460d924d1ae99))
* **capture:** window budgets hold as a floor and cap; busy-loop windows announce their target ([#170](https://github.com/AmbiqAI/helia-profiler/issues/170)) ([#171](https://github.com/AmbiqAI/helia-profiler/issues/171)) ([01ae7b3](https://github.com/AmbiqAI/helia-profiler/commit/01ae7b319eb5ff1eb813f247b64238a9539dff12))
* **ci:** pin nsx-executorch with the bare-metal RNG-seeding fix ([55da71f](https://github.com/AmbiqAI/helia-profiler/commit/55da71f67943e1d35487358c18e140056a34458e))
* **ci:** re-pin nsx-executorch to the wrapper-only ATfE build fix ([96bd399](https://github.com/AmbiqAI/helia-profiler/commit/96bd3998d2957640ddc8fcfb5c966fadbf67b440))
* **compare:** [#223](https://github.com/AmbiqAI/helia-profiler/issues/223) — per-layer memory rows join on the source index, never position ([#227](https://github.com/AmbiqAI/helia-profiler/issues/227)) ([198b31a](https://github.com/AmbiqAI/helia-profiler/commit/198b31a11dbb67217b05aaefd50f98d2c6841de5))
* **compare:** [#243](https://github.com/AmbiqAI/helia-profiler/issues/243) — survive wide CSV rows and emit valid JSON on non-finite metrics ([#244](https://github.com/AmbiqAI/helia-profiler/issues/244)) ([6f1f0b3](https://github.com/AmbiqAI/helia-profiler/commit/6f1f0b36c5554ec82b1a5aae84747aecbcdbfd02))
* **compat:** requalify nsx-executorch baseline to the ATfE build fix ([3618e75](https://github.com/AmbiqAI/helia-profiler/commit/3618e75d15977c1fc09c5e2729d77df5b69fd9ca))
* **compat:** requalify nsx-executorch to the complete ATfE build fix ([292ea71](https://github.com/AmbiqAI/helia-profiler/commit/292ea7121cd45d7b691e186a67625ee2516a5082))
* **compat:** requalify nsx-executorch to the review-scoped ATfE build fix ([c3a10b4](https://github.com/AmbiqAI/helia-profiler/commit/c3a10b47ae3255d76c954ec5ed3e91cb69ed9e36))
* **console:** [#208](https://github.com/AmbiqAI/helia-profiler/issues/208) retro-review — pin the store link, finish the header count ([#210](https://github.com/AmbiqAI/helia-profiler/issues/210)) ([6e781ce](https://github.com/AmbiqAI/helia-profiler/commit/6e781ce5f9f088f40e24ee1e6bd76e7da9e5704d))
* **console:** police lines render even when every measured region is zero ([#178](https://github.com/AmbiqAI/helia-profiler/issues/178)) ([4057c79](https://github.com/AmbiqAI/helia-profiler/commit/4057c793850686f856c15d9bab27facf63e7565b))
* **engines:** force-sync the auto-clone cache so local edits never build ([d4f62a5](https://github.com/AmbiqAI/helia-profiler/commit/d4f62a513c7fafb2fa97b53d7e6fc72d12d8a21f))
* **engines:** report cause when git fails without stderr; drop stale branch-head claim in docs ([f525d7c](https://github.com/AmbiqAI/helia-profiler/commit/f525d7ce3f06146894f1f57f40f10a0a1c6f0c91))
* **firmware:** fixed+STIMER profile builds announce a measured est_ms ([#164](https://github.com/AmbiqAI/helia-profiler/issues/164)) ([#169](https://github.com/AmbiqAI/helia-profiler/issues/169)) ([229dced](https://github.com/AmbiqAI/helia-profiler/commit/229dced9b120f330f9ac4275b77e676b090bb0c9))
* **jlink:** surface JLinkExe stdout in flash/reset failure hints ([e1304b5](https://github.com/AmbiqAI/helia-profiler/commit/e1304b5fde43ab04fb2d558408d9f0550b6ff9c0))
* **main:** repair the [#203](https://github.com/AmbiqAI/helia-profiler/issues/203) x [#209](https://github.com/AmbiqAI/helia-profiler/issues/209)/[#210](https://github.com/AmbiqAI/helia-profiler/issues/210) semantic crossings ([9317049](https://github.com/AmbiqAI/helia-profiler/commit/93170493177b798204b3b3e275e0da3ac0f4210b))
* **power:** device-clock gate edges via GPI streaming — root-cause fix for the nightly power-window flags ([f750d95](https://github.com/AmbiqAI/helia-profiler/commit/f750d95a99cbb8ecc9ecb0b1ef0457f06aac1734))
* **power:** publish internal-mode observation through the publisher ([c24727d](https://github.com/AmbiqAI/helia-profiler/commit/c24727dd54397fa4960f6638639efb30f2fb899f))
* **power:** time gate edges on the instrument clock via GPI streaming ([fe1c4aa](https://github.com/AmbiqAI/helia-profiler/commit/fe1c4aa4adfac5c127b761c9cd45cb44716233c7))
* **probe:** verify a flash landed at the requested address ([#150](https://github.com/AmbiqAI/helia-profiler/issues/150)) ([#152](https://github.com/AmbiqAI/helia-profiler/issues/152)) ([439ab0c](https://github.com/AmbiqAI/helia-profiler/commit/439ab0c11aa14898eff48a70aa28beca95f0c7e8))
* **psram:** gate the PSRAM host upload on engine capability, and refuse AOT PSRAM that renders no PSRAM code ([#219](https://github.com/AmbiqAI/helia-profiler/issues/219)) ([#220](https://github.com/AmbiqAI/helia-profiler/issues/220)) ([42087ef](https://github.com/AmbiqAI/helia-profiler/commit/42087efac742feb2a4982379d377b81e08b4b1d2))
* **report:** [#218](https://github.com/AmbiqAI/helia-profiler/issues/218) — join per-layer MACs on the original op index, never position ([#222](https://github.com/AmbiqAI/helia-profiler/issues/222)) ([1b60629](https://github.com/AmbiqAI/helia-profiler/commit/1b60629b73725517f28259daf23cf63108d564d7))
* **report:** [#240](https://github.com/AmbiqAI/helia-profiler/issues/240) — TOPS scales by the window's own inference count [CRITICAL] ([#242](https://github.com/AmbiqAI/helia-profiler/issues/242)) ([919c24f](https://github.com/AmbiqAI/helia-profiler/commit/919c24fc91781d2f827c8b4dbb5f3a1ab8fc2b01))
* **review:** invalidate the frozen-sync stamp on any build-stage BuildError ([c214fb2](https://github.com/AmbiqAI/helia-profiler/commit/c214fb270d40b27319c89adb974be9ff9559f347))
* **review:** invalidate the frozen-sync stamp on configure failure too ([aa5c5ea](https://github.com/AmbiqAI/helia-profiler/commit/aa5c5ea73f2ac3fd7f0530007f192bffa30ab3e6))
* **review:** preserve existing RTT conf when vendor conf is absent; valid JSON in stamp test ([0b95680](https://github.com/AmbiqAI/helia-profiler/commit/0b9568080df612033f5614282f37a23c15ee4dff))
* **review:** restore --pmu-counters error precedence over --nsx-module ([f32796e](https://github.com/AmbiqAI/helia-profiler/commit/f32796ea1d688a9f523ce77bbaa133381dd9f4c6))
* **review:** restore RTT-specific no-record hint on the shared chunk collector ([44f04c1](https://github.com/AmbiqAI/helia-profiler/commit/44f04c1484fd8bd725a8d6cb52ace9a9e687b603))
* **review:** say 'summed arena size' not 'runtime workspace'; drop stale frozen-wire claim ([2b3db6e](https://github.com/AmbiqAI/helia-profiler/commit/2b3db6e3e6f558dc2c3797ce2a0bc67556f2bd36))
* **review:** scan power-terminal frames in the byte buffer, pair END with last START ([23fd479](https://github.com/AmbiqAI/helia-profiler/commit/23fd47977fa8f37620177494cbebf76e94346620))
* **types:** [#201](https://github.com/AmbiqAI/helia-profiler/issues/201) — ty gates the tests tree; 420 diagnostics cleared ([#209](https://github.com/AmbiqAI/helia-profiler/issues/209)) ([1bc36a1](https://github.com/AmbiqAI/helia-profiler/commit/1bc36a1dd93b5063e19e1b39c703a2952ab431c7))
* **types:** clear the 44 ty diagnostics that turned main's lint gate red ([#221](https://github.com/AmbiqAI/helia-profiler/issues/221)) ([08b2579](https://github.com/AmbiqAI/helia-profiler/commit/08b2579c5c0421709a22e3a0325f06c42b7a4c85))
* untrack tools/run_ap3_sweep.py — bench script swept in by [#232](https://github.com/AmbiqAI/helia-profiler/issues/232) ([#233](https://github.com/AmbiqAI/helia-profiler/issues/233)) ([c7a908a](https://github.com/AmbiqAI/helia-profiler/commit/c7a908a060f538f9c6a7323f14507fd9ed364bd9))
* Windows-safe AOT file IO + compile gate reads c_define from registry ([#190](https://github.com/AmbiqAI/helia-profiler/issues/190)) ([0cc28d5](https://github.com/AmbiqAI/helia-profiler/commit/0cc28d573e2e2ac96c86828d0875893d7b8aebb2))
* **wire:** sum all three ExecuTorch arenas in HPX_ARENA_SIZE ([#165](https://github.com/AmbiqAI/helia-profiler/issues/165)) ([5e35580](https://github.com/AmbiqAI/helia-profiler/commit/5e35580a596a8dcf1353123e183c1dbd5e32aca4))
* **wire:** sum all three ExecuTorch arenas in HPX_ARENA_SIZE ([#165](https://github.com/AmbiqAI/helia-profiler/issues/165)) ([12416c6](https://github.com/AmbiqAI/helia-profiler/commit/12416c6b4e47497a4cf2e3947f1d22ac3e25c896))


### Performance Improvements

* 4.5x faster repeated validation runs, 2.2x faster cold matrix ([9cacc60](https://github.com/AmbiqAI/helia-profiler/commit/9cacc607d9f56033a870c34a1a4f6f1a150cf7e0))
* cut per-run host-side firmware build overhead (write-if-changed + frozen-sync stamp) ([9d25a72](https://github.com/AmbiqAI/helia-profiler/commit/9d25a72833db408e8c5e3372ae5c5110da4b559f))
* **dependencies:** fingerprint dependency identity only, not render inputs ([26f634f](https://github.com/AmbiqAI/helia-profiler/commit/26f634f444b800c46ae476a8c7f5110814b3eda0))
* **deps:** skip frozen sync re-verification behind a lock-digest stamp ([040ff8e](https://github.com/AmbiqAI/helia-profiler/commit/040ff8e0b4f6add070602e19c05ba6ff348cb83f))
* **firmware:** write generated sources only when content changes ([aa6345a](https://github.com/AmbiqAI/helia-profiler/commit/aa6345a0528e4122d1075c1e2366b2e6b0c3a9f9))
* **flash:** deploy profile firmware via the direct J-Link recipe path ([61700a7](https://github.com/AmbiqAI/helia-profiler/commit/61700a7357e95c143ddd06cb09ddcd7fdf179bd0))
* **validation:** build cases in the shared incremental workspace cache ([c43bc4f](https://github.com/AmbiqAI/helia-profiler/commit/c43bc4f396629d23b4e081f571ddda0db58a240a))


### Documentation

* correct the heliaRT 1.17.0 promotion record ([#191](https://github.com/AmbiqAI/helia-profiler/issues/191) review follow-up) ([#192](https://github.com/AmbiqAI/helia-profiler/issues/192)) ([3279e49](https://github.com/AmbiqAI/helia-profiler/commit/3279e49940c9052731c86e1e2d31708ce733e106))
* make the maintainer-only boundary real, publish hpx validate ([f4808fb](https://github.com/AmbiqAI/helia-profiler/commit/f4808fbdeffdcf008a494944863b18b8fef24d0c))
* mend the boards bullet split by the apollo4l note ([9e2709b](https://github.com/AmbiqAI/helia-profiler/commit/9e2709b438a378885c0495ca39c8680f4818f930))
* retire stale specs and plans, publish the Tier-1 comparison ([792c0a4](https://github.com/AmbiqAI/helia-profiler/commit/792c0a4848749703c1a66813110159905ba03fd7))
* retire stale specs, fix the maintainer-only boundary, unpin brittle notebook tests ([b3a2a3b](https://github.com/AmbiqAI/helia-profiler/commit/b3a2a3b074b54049298f450d0e682c55b207d012))
* second-pass accuracy cleanup of docs and tests ([8543fa2](https://github.com/AmbiqAI/helia-profiler/commit/8543fa2d3f93f86875e0fa478440e6f0d4f2c060))
* second-pass accuracy cleanup of docs and tests ([8f31db7](https://github.com/AmbiqAI/helia-profiler/commit/8f31db7b30f05a4613859ba5c1cfe3b83921d21b))


### Code Refactoring

* **config:** closed config vocabularies become StrEnums ([#162](https://github.com/AmbiqAI/helia-profiler/issues/162) Phase 3) ([#167](https://github.com/AmbiqAI/helia-profiler/issues/167)) ([b6c07fb](https://github.com/AmbiqAI/helia-profiler/commit/b6c07fbac5606592290f10a352c2ef178a9a2044))
* **power:** typed PowerMetadata replaces the metadata dict ([#154](https://github.com/AmbiqAI/helia-profiler/issues/154) Phase 2) ([#157](https://github.com/AmbiqAI/helia-profiler/issues/157)) ([1d09456](https://github.com/AmbiqAI/helia-profiler/commit/1d0945679b26167fec8b7a7a85e80e55388118c8))

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
