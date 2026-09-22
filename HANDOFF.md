# HANDOFF: heliaPROFILER docs migration (Astro + helia-ui)

Goal: replace the MkDocs/Zensical site with Astro + Starlight on helia-ui,
revamp every section, generate CLI, configuration, issue-code, PMU and API
reference, and ship agent-readable outputs (llms.txt, per-page Markdown).
Epic: AmbiqAI/helia-profiler#320. The plan and the Phase 0 decisions are
comments on #320.

## How the stack works

- `docs-migration` is the integration branch; draft PR #332 carries it to
  `main` as one squash once every sub-PR has merged. Sub-PRs target
  `docs-migration`, get an adversarial review, green CI, then squash-merge.
- A sub-PR branch is rebased by merging `origin/docs-migration` into it
  (force pushes are blocked locally). A PR that is CONFLICTING gets no
  workflow runs at all; merge the base first. After a rebase re-run
  `check:redirects`. `redirects.json` and the sidebar are append-only.
- New worktree: `npm ci` and `npm run reference:dump` before any check.
- `uv lock` strips the `# x-release-please-version` marker from `uv.lock`;
  re-add it on the helia-profiler version line before committing.
- Check chain, all in `docs.yml`: `build, check, check:links, check:output,
  check:search, check:redirects, check:reference, check:guard`.
  `check:output` enforces 250,000 B HTML and 40,000 B gzip per page;
  `check:guard` runs `scripts/*.test.mjs`, including the examples template
  and config-path tests.
- Diagrams use `@ambiqai/helia-ui/astro/{BlockDiagram,Block}` (alpha.19); the
  local mirror is gone. Every diagram page repeats its hierarchy as a nested
  Markdown list; the alpha.19 rendition sidecar also carries it into the
  Markdown twins.
- Every external URL is fetched before it is cited; `check:links` skips
  external hosts. Numbers come from a shipped bundle or a source of record
  with a link, or the page says "not yet validated".
- Evidence bundles: `examples/results/hardware-validation-2026-09-16/`,
  nine unmodified bundles from nightly run 35078330526 (hpx 0.1.6, commit
  0714893); the whitespace hooks exclude that directory so the manifests
  keep verifying. Their `run_metadata.json` carries the runner's paths and
  the probe serial; the owner has not yet ruled on that.

## State (2026-09-21, close)

Every sub-PR is merged into `docs-migration` (#333 to #362): skeleton, the
generated references, the content port, Getting started, Set up a run,
Measure, Read results, Concepts, Examples, the host-only power pages,
Reference polish, the helia-ui alpha.19 pin and the cutover. `main` is
merged in (4f2afae). PR #332 carries the branch to `main` as one squash and
is marked ready; merging it deploys the Astro site (the owner's call).

Open, owner decisions:
- Home was rebuilt on branch `335-home-rebuild` as a documentation overview
  (owner-approved mockup, 2026-09-21): version line, intro, quickstart
  tabs, CLI and Python cards, supports cards, five-stage block diagram,
  eight measurement rows, four section links. It supersedes #337, which
  closes once the rebuild merges.
- The shipped bundles' `run_metadata.json` (runner paths, probe serial),
  kept unmodified so the manifests verify; see PR #354.
- Two issue drafts in the session scratchpad: bench captures for the
  not-yet-validated example pages and the remaining #345 pages; exporting
  `load_run_summary` and the `RunSummary` sections for the API reference.
- Post-deploy verification on #322 after #332 lands.

## Decisions

- Flowcharts became decision tables or block diagrams; no mermaid remains.
- No commit hashes or commits-since-tag counts anywhere on the site; the
  version line shows the released version only and `check-output.mjs`
  enforces it. Registry counts on Home are typed as text (the Markdown
  rendition drops JSX expressions) and held to `src/data/catalog.json`.
- `summary.total_cycles` is the instrumented per-layer sum; pages publish
  `latency.device_clean_infer_avg_cycles`.
- The JS110 column of the Joulescope table carries no figures: no product
  page of record exists.
- The compatibility table's vocabulary is hardware-validated, compile-only,
  experimental, unknown; no CI job compiles firmware without a board, so no
  cell is compile-only.
- Reviewer findings are applied by the session owner directly (no coder
  agents, per the owner).

## Gotchas

- `sed` with `#` in the replacement collides with the delimiter; use Python.
- Astro preview daemons persist; `npx astro preview stop` before a new one.
- Pending GitHub checks have an empty `conclusion`; test for `""`.
- `pre-commit` rejects bare TODOs; `SCREENSHOTS/` and the stale-claims
  scratch files are gitignored.
