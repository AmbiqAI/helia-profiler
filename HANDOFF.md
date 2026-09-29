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
Run final committed reference drift checks and review the final local diff; obtain owner visual/content acceptance. Push/refresh draft PR with an accurate description when approved; final remote checks and explicit merge approval precede cutover. Verify public build-info, search, redirects, 404 and machine-readable reference after deployment. Then start heliaEDGE with its own approved issue.

## Constraints and gotchas
- Preserve actual hardware evidence boundaries. Power example pages without captured bundles stay labelled unvalidated.
- Python reference uses pinned griffe 1.7.3; generated source provenance is the source-tree hash. Regenerate with npm run prepare:docs and commit before check:reference.
- Existing generated reference retains complete per-module Markdown sidecars plus JSON; validate advertised renditions and agent exports.
- Existing evidence bundles stay immutable so manifests verify. No new power measurements are claimed.
- No desktop/browser takeover. Use CLI and independent headless checks.
