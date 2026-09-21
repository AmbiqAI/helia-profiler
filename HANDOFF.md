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
  check:search, check:redirects, check:reference, check:port, check:guard`.
  `check:output` enforces 250,000 B HTML and 40,000 B gzip per page;
  `check:guard` runs `scripts/*.test.mjs`, including the examples template
  and config-path tests.
- Diagrams use the `BlockDiagram`/`Block` mirror in
  `astro-site/src/components/` (helia-ui#154; swap for the package export when
  the owner cuts alpha.17). Every diagram page repeats its hierarchy as a
  nested Markdown list (helia-ui#155).
- Every external URL is fetched before it is cited; `check:links` skips
  external hosts. Numbers come from a shipped bundle or a source of record
  with a link, or the page says "not yet validated".
- Evidence bundles: `examples/results/hardware-validation-2026-09-16/`,
  nine unmodified bundles from nightly run 35078330526 (hpx 0.1.6, commit
  0714893); the whitespace hooks exclude that directory so the manifests
  keep verifying. Their `run_metadata.json` carries the runner's paths and
  the probe serial; the owner has not yet ruled on that.

## State (2026-09-21, end of day)

Merged into `docs-migration`: Phase 1 (#325), Phase 2 (#330), content port
(#334), alpha.16 pin (#336), Getting started (#339), Set up a run (#340),
Measure (#341), Read results (#342), Concepts (#343), Examples (#344), the
host-only half of the power examples (#345, PR #356), Reference polish
(#346, PR #355).

In flight:
- #337 Home: PR awaiting owner copy sign-off.

- #322 cutover: branch `322-cutover` (worktree `hpx-322`): docs/, mkdocs.yml,
  the docs dependency group, deploy-pages.yml, the port scripts and the two
  legacy generators are gone; the wire generator writes the MDX page; the
  mermaid renderer and playwright are gone; the deploy job holds the Pages
  permissions and the guard condition again. The live site switches when the
  stack (#332) lands on main, which is the owner's call.
- helia-ui alpha.18 pin: PR #361 (worktree `hpx-alpha18`), drops the
  BlockDiagram mirror; the sidecar line-join defect is helia-ui#171.

Not started: the `load_run_summary` export for the API reference (draft
issue `issues/draft-run-summary-api-export.md`); the bench captures for the
not-yet-validated example pages (draft `issues/draft-power-bench-captures.md`).

## Decisions

- Flowcharts became decision tables or block diagrams; no mermaid remains.
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
