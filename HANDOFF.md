# heliaPROFILER documentation migration

## Goal
Finish issue #320 and draft PR #332, then migrate heliaEDGE. Obtain owner review before publishing the profiler site. Existing PR is a draft and has not been merged.

## Current state
Working checkout: .claude/worktrees/agent-a5c6d2808dbc33deb, branch docs-migration. Reused the older Home checkout; retained its untracked screenshots/source notes. Primary checkout and unrelated branches untouched.

Merged origin/main 4cf71e4c into the migration as d550a26e. Resolved documentation conflicts by carrying current capture, engine and power contracts into the Astro pages while preserving removal of superseded MkDocs pages. Released helia-ui alpha.20 replaces the unreproducible alpha.19/local-package checkpoint. Regenerated API, CLI, configuration, issue and catalog artifacts from current source.

Updated authored docs for heliaRT 1.21.2, heliaAOT [0.23,0.24), rejected release variant, strict capture completeness, INA228 accumulation versus GPIO gate timing, and run-summary schema v8. Historical captured examples retain their original versions and values.

## Validation
Build, Astro type check, links/assets/anchors, Pagefind search, semantic outputs, redirects and 38 script tests passed during this pass. Headless Chromium checked seven routes at 390/1032/1440 widths in light/dark themes: no page errors or horizontal document overflow. Home desktop light/mobile dark screenshots inspected. No hardware runs performed.

Preview: http://127.0.0.1:8764/helia-profiler/ . Do not control existing user tabs or desktop windows. Preview runs through the task terminal.

## Delivery
.github/workflows/docs.yml validates the artifact on PRs and deploys relevant main pushes, release calls and manual dispatch. Release docs wait for package publishing; ordering guard prevents an older artifact replacing newer docs. HTTPS Git rewrite makes the public shared dependency installable without SSH credentials. User has not approved merging this migration in this session.

## Remaining
Final committed reference drift checks, CLI source checks and the full site validation chain passed. Astro reports zero errors, warnings or hints. Local commits d550a26e and c5326af1 are not pushed. Obtain owner visual/content acceptance. Push/refresh draft PR with an accurate description when approved; final remote checks and explicit merge approval precede cutover. Verify public build-info, search, redirects, 404 and machine-readable reference after deployment. Then start heliaEDGE with its own approved issue.

## Constraints and gotchas
- Preserve actual hardware evidence boundaries. Power example pages without captured bundles stay labelled unvalidated.
- Python reference uses pinned griffe 1.7.3; generated source provenance is the source-tree hash. Regenerate with npm run prepare:docs and commit before check:reference.
- Existing generated reference retains complete per-module Markdown sidecars plus JSON; validate advertised renditions and agent exports.
- Existing evidence bundles stay immutable so manifests verify. No new power measurements are claimed.
- No desktop/browser takeover. Use CLI and independent headless checks.

## Landing-page refresh
All landing edits remain local. The owner replaced the type-specimen direction with the shared helia-ui Hero and Button layout for cohesion with AOT, CORE and NSX. Rounded dark hero, neon green #9bea55 accents, outlined engine links, alternating white/gray sections, varied card layouts and small setup navigation cards.

ProfilerHero.astro supplies four animated slides: Layers, MVE/NPU, Compare and Energy. Content stays in index.mdx for semantic exports. Off-black panel, compact diagrams, 7.5-second progression, name selection and circle pause/resume. Reduced motion and offscreen suspension supported. Energy trace has two dense activity bursts separated by idle. NPU scope explicitly experimental Atomiq110 FPGA; MVE/counter support is target-dependent.

ProfilerDemo.astro supplies one theme-aware terminal with six steps: Check, Probes, LiteRT, AOT, Compare and Power. Verified CLI words and flags: hpx doctor; hpx probes list; hpx profile --config kws.yml --engine tflm/helia-aot --output-dir; hpx compare; hpx profile --power. Never substitute invented --aot or hpx diff. Commands type at 34ms/character; results reveal at 240ms/line; reading holds are 2.6/2.6/5/5/7/5.5 seconds. Equal-height tabs use progress circles and pause/resume, keyboard navigation and reduced-motion support. Copy buttons removed because examples are abbreviated. Screen min-height 330px; whole frame about 441px at 1029px viewport.

Hero and terminal now use the owner-supplied heartKIT AFIB arr-2-eff-sm INT8 profiling example (2026-09-28): 38.69 to 12.14ms, 3,714,336 to 1,164,960 cycles, 28.3 to 14.7 KB activation RAM, 61.4 to 25.7 KB model storage, 89.7 to 40.4 KB total. Approved gains: 3.2x faster, -69% cycles, -48% arena, -58% storage, -55% total. Apollo510 EVB, Cortex-M55 Helium, 96MHz low-power mode, ATfE22.1.0, no NPU; heliaAOT0.23/ns-cmsis-nn7.36 versus Ambiq TFLM/Arm CMSIS-NN. User is the source of these measurements; no rerun performed. Public copy calls it a profiling example with a short footnote and method disclosure. No energy measurements, customer names, internal paths, harness sizes or correctness claims published. Keep actual CLI excerpts separate from the supplied measurement tables because compare does not print this clean-median/total-memory table. Power remains an optional next step with no fabricated figures.

Comparison section heading is “Put your next idea to the test.” with “Compare your options” eyebrow. Final copy review checked timing, power, comparison and Model Explorer contracts. Hero identifies hpx as a CLI toolkit; copy explains decisions and next steps. Clean timing remains separate from instrumented layer timing, power refers to the monitored supply, captured overlays differ from host-only structural analysis. Avoid development-facing notes in customer copy.

Latest card polish: shared stopwatch, microchip and bolt icons at the upper right of the measurement cards. Larger timing card reuses TimingScopes.astro with a subtle green clean-inference bar and separate instrumented-layer boxes. Caption explicitly says these are separate passes. No invented measurements. Styles are scoped in ProfilerLanding.astro.

Validation: latest build, semantic output/reference assertions and links passed (121 HTML pages). Headless Chromium verified three icons and no overflow at 390/1029 widths in light/dark themes; /tmp/hpx-icons.png inspected. Earlier checks cover four hero slides, six terminal panels, typing/pause/reduced motion, and 390/1029/1440 layouts. No hardware runs. Preview remains http://127.0.0.1:8764/helia-profiler/ and serves dist, so rebuild after changes.

Main local files: index.mdx, ProfilerHero.astro, ProfilerDemo.astro, ProfilerLanding.astro, ProfilerArrow.astro, TimingScopes.astro, scripts/check-output.mjs and this handoff. Next: owner visual acceptance, then approved PR publication. Do not begin heliaEDGE yet.

## Release-readiness review
Merged origin/main 7c941cb0 locally, preserving landing edits. Broad rendered audit covered all 83 content routes at desktop and phone widths; found overflowing CLI epilog examples, fixed by using shared CodeBlock. Tightened onboarding, engine-choice claims, timing/graph interpretation, compatibility evidence and power-guide claims. Removed unsupported transport-agreement benchmark and corrected INA228 range boundary (source uses <=). Examples now use the shared searchable/filterable ReferenceBrowser with source-owned data and semantic export. Added React integration for this shared island. Six Playwright tests pass, including all 83 content routes at 390px light and 1029px dark, filters/empty state/reset, no-JS example navigation, keyboard tabs and live Pagefind search. Build, Astro diagnostics (zero errors/warnings/hints), links (121 HTML pages), output/Markdown, search, redirects and 38 script tests pass. Representative light/dark desktop/mobile screenshots inspected. Generated artifacts refreshed after the main merge; committed-reference gate passed after local checkpoint 2368c3c8: 47 Python reference files and 39 CLI reference files match fresh generation. Nothing published.

Additional content corrections: heliaAOT 0.23.0 supports Python >=3.11 (published metadata), optional uv extras are documented, UART cabling clarified, and historical results keep original schema versions. Baseline history moved into a disclosure after the active qualification contract. Shared React ReferenceBrowser replaces the wide examples index; descriptions and filter facets share examples.json and export to Markdown. Docs CI now installs Chromium and runs npm test. maintainers/documentation.md covers direct docs-only publishing and post-deploy checks. Build emits upstream Astro/Vite module-directive and empty-i18n warnings; rendered assets and interactions pass. No new hardware measurements.

## Follow-up docs polish

Completed locally: install OS tabs use native Starlight syncKey; shared AsciiTerminal combines commands and captured output with one-shot playback, command-only copy and replay. Setup terminals honor reduced motion. Host prerequisites, board/probe connections and optional power are separated. Engine guide recommends heliaAOT for supported models, preserving heliaRT as the CLI default; full adapter contracts moved to reference/engine-configuration and linked from navigation.

Verified: build, zero Astro diagnostics, links across 122 HTML pages, 84 semantic/search routes, redirects, reference freshness and 38 script tests. Browser checks cover all 84 routes at 390px light and 1029px dark, search/filters/no-JS navigation, synchronized OS tabs and terminal copy/replay. Representative screenshots inspected. No hardware run or remote publication.

Measured-example update: local landing build and semantic exports pass; all eight browser checks passed, Astro diagnostics clean. Inspected hero and comparison screenshots, adjusted footnote spacing and full-width result table. Public model link verified. Measurements were supplied by the owner, not remeasured in this task.

ExecuTorch promotion removed from Home at owner request, including engine pill and setup copy/count. Guide retains documentation with explicit experimental status. Home search smoke term changed to heliaRT.

Final PR polish completed: replace the landing timing schematic with an owner-supplied latency highlight and a three-step explanation of baseline timing, operator ranking and targeted comparisons. Expand counter/power cards with actionable interpretation. User authorized updating PR332. Final build, zero Astro diagnostics, eight browser tests, 38 guards, references, semantic exports, links, search and redirects pass. Revised card screenshot inspected. Publishing docs-migration to PR332 with refreshed scope and validation; keep draft pending final owner review and remote CI.

Hero motion polish: removed the shared upward entrance and stagger. Layers use only growing bars, Energy only the tracing waveform; MVE/NPU and Compare diagrams fade in without translation. Pause and reduced-motion overrides retained.
