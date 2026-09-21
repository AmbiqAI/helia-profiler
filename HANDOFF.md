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
  (force pushes are blocked locally), then `npm run check:port` and
  `check:redirects` are re-run. `port-legacy-docs.mjs` SKIP/PRESERVE,
  `redirects.json` and the `astro.config.mjs` sidebar are append-only.
- Check chain, all in `docs.yml`: `build, check, check:links, check:output,
  check:search, check:redirects, check:reference, check:port, check:guard`.
  `check:output` enforces 250,000 B HTML and 40,000 B gzip per page.
- Diagrams use the `BlockDiagram`/`Block` mirror in
  `astro-site/src/components/` (helia-ui#154; swap for the package export when
  the owner cuts alpha.17). Every diagram page repeats its hierarchy as a
  nested Markdown list so the rendition carries it (helia-ui#155).
- Every external URL is fetched before it is cited; `check:links` skips
  external hosts. Silicon and instrument numbers are quoted from a source of
  record with a link, or cut.

## State (2026-09-21)

Merged into `docs-migration`: Phase 1 (#325), Phase 2 (#330), content port
(#334), helia-ui alpha.16 pin (#336), Getting started (#339), Set up a run
(#340, PR #350).

Merged as well: #341 Measure (PR #351) and #342 Read results (PR #352).

In flight:
- #343 Concepts: PR #353 from `343-guide-concepts` (worktree `hpx-343`),
  seven pages around one block diagram each; review round two.
- #344 Examples: PR #354 from `344-examples` (worktree `hpx-344`), twelve
  pages on the nine-heading template with nine captured bundles under
  `examples/results/`; review round one.
- #346 Reference polish: branch `346-reference-polish` (worktree `hpx-346`),
  compatibility table, landing cards, authored intros, wire-protocol header.
- #337 Home: PR awaiting owner copy sign-off.

Not started: #345 Examples power (two pages can ship without bench time),
#322 cutover (retire docs/, mkdocs, gates, Pages deploy `if: false`; also
rehype-mermaid and playwright, since no page carries mermaid now).

## Decisions

- One engine route table per page instead of flowcharts; no mermaid remains.
- `summary.total_cycles` is the instrumented per-layer sum; the pages tell
  readers to publish `latency.device_clean_infer_avg_cycles`.
- The JS110 column of the Joulescope table carries no figures: no product page
  of record exists.
- Reviewer findings are applied by the session owner directly (no coder
  agents, per the owner).

## Gotchas

- Rebased branches cannot be force-pushed; push under a new name or merge.
- `sed` with `#` in the replacement collides with the delimiter; use Python.
- Astro preview daemons persist; `npx astro preview stop` before a new one.
- Pending GitHub checks have an empty `conclusion`; test for `""`.
- `pre-commit` rejects bare TODOs; `STALE-CLAIMS.md`, `SCREENSHOTS/` are
  gitignored scratch.
