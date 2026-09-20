# HANDOFF: heliaPROFILER docs migration (Astro + helia-ui)

Goal: replace the MkDocs/Zensical site with Astro + Starlight on helia-ui, revamp
content, generate CLI/config/API reference, ship agent-readable outputs.
Epic: AmbiqAI/helia-profiler#320. Plan: draft 4, posted as a comment on #320;
Phase 0 outcome and decisions are the comment after it.

## State (2026-09-20)

Done:
- Phase 0 spikes #323 (branch `323-spike-scaffold-pyref`, PR #326) and #324
  (branch `324-spike-cli-config`, PR #327). Both stay draft and close unmerged.
- Phase 1, #325, landed on `docs-migration` as d4eec96 (reverted from `main` by
  #329). The migration reaches `main` as one stacked PR, #332.
- Phase 2, #330, this branch `330-python-api-reference` off `docs-migration`:
  the Python API reference. No PR opened yet; owner reviews first.

This branch (#330):
- helia-ui pinned to `v0.1.0-alpha.15` (`51aaae91cf7942eb0778be48e4b4c9a1b16772c3`),
  lockfile regenerated on linux/amd64 node:24. alpha.15 moves the Callout
  recipe into the global `recipes.css` and drops `role` for `aria-label`;
  this site overrides neither, so nothing here changed.
- The last 11 NumPy docstring sections (7 files) are Google style, held there
  by `tests/test_docstring_style.py`.
- `astro-site/scripts/{dump-python,scope-dump,build-reference}.mjs` produce
  14 curated pages, `reference.json`, per-module JSON and Markdown, and both
  llms files, from a griffe 1.7.3 dump scoped by `__api_stability__`.
- Checks: `check:reference` (committed artifacts carry no absolute path, and
  the committed pages match a fresh regeneration into a scratch directory),
  `check:output` now also runs `check-reference-output.mjs`.

Verified on this branch (all local, all green):
- `npm ci` from the Linux lockfile.
- `npm run build`, `check`, `check:links`, `check:output`, `check:search`,
  `check:redirects`, `check:reference`, `check:guard`.
- 61 legacy routes covered: 57 redirected (49 still deferred to a section
  landing page, down 8), 4 already served by a page of the same path.
- 85 published names render as 84 symbols and one module page across 14 pages,
  each symbol once and badged; the 13 implementation names appear as no symbol,
  member, sidebar entry or llms-full.txt section.
- Largest reference page: `/reference/api/helia_profiler/evaluation/` at
  211,801 B HTML and 14,582 B gzip, against a 250 KB / 40 KB budget.
- `uv run --no-project --with griffe==1.7.3 griffe dump helia_profiler
  --search src --docstyle google -f` produces the same dump as the project env,
  which is what makes the CI job need griffe and nothing else. (Without
  `--docstyle google -f` the dump is a different size and a different shape;
  the flags are not cosmetic.)
- A docs-only commit leaves every generated file untouched: the artifacts
  committed at `dea74c5` were generated at `d83d145` and `check:reference`
  still reports them current.

Not verifiable before merge, named as such in #325:
- the first post-merge push run (artifact, deploy job skipped),
- a `workflow_dispatch` run with `source_ref` set to a tag,
- `deploy-pages.yml` still producing the live site.
Record the run links on #325 when they exist.

## Decisions that shaped this branch

- Astro-only. No React, no Tailwind. The measurement that settled it is in the
  Phase 0 comment on #320.
- The deploy job ships in its final shape but cannot publish: the three Pages
  steps are `if: false`, and the job holds neither `pages: write`/`id-token:
  write` nor the `github-pages` environment. All four come back in #322.
- Ordering is decided from the live `build-info.json`, not from the trigger.
  A refusal is a skip, so an out-of-order build does not page anyone.
- The guard separates "nothing is live" from "cannot tell". 404, 410 and a 200
  that is not build-info mean nothing comparable is live, so it proceeds; a 5xx
  or a connection failure is retried three times with backoff and then refuses,
  because the live site may be newer than the build asking to replace it.
- `publish.yml`'s docs job waits on `publish-pypi`, so the site never announces
  a version whose PyPI publish failed.
- The 404 lives at `src/pages/404.astro`, not in the docs collection. The
  discoverability integration walks that collection, and a 404 inside it earns
  an llms.txt line and a `/404/index.md` rendition for a route the site does
  not serve. Its canonical is set to its own URL for the same reason.
- `redirects.json` answers a legacy route in one of three ways: `served` (a
  page already lives at that path, so a redirect there would collide with it),
  `redirects` (forwards elsewhere), `deferred` (a subset of the redirect keys
  whose real target is not built yet). `deferred` must be empty at cutover.
  #325 named two of the three; `served` exists because Astro cannot redirect a
  path it also builds a page at.
- The version display is a local component (`src/components/DocsVersion.astro`)
  on Home, because helia-ui's shell has no version slot. The upstream ask stays
  on the helia-ui list.
- Pagefind is asserted against the generated index, not through a query: the
  query API is a browser module. `check:search` asserts the term is in the
  vocabulary and in the Home fragment.

## Next

1. Owner reviews this branch, then a PR into `docs-migration` (label
   `agent-generated`). The docs workflow only runs on a pull request, so the
   "no board, no secrets" run link on #330 cannot exist until the PR is open.
2. #331: CLI, configuration and issue-code reference. It collides with this
   branch in `package.json`, `astro.config.mjs` (one Reference sidebar array),
   `src/data/redirects.json` and `docs.yml`, so it rebases on this rather than
   running beside it.
3. Draft the remaining child issues (plan s5 items 4-17) as their phase arrives.

## Gotchas

- Counts: `__all__` is 98 (45/40/13), 61 legacy routes, 12 top-level CLI entries
  / 14 leaves, ProfileConfig + 14 nested models / 106 fields.
- `src/data/legacy-routes.json` is regenerated only when `mkdocs.yml` changes:
  `uv sync --locked --group docs && uv run --group docs zensical build` then
  `npm --prefix astro-site run legacy-routes:extract`. It outlives cutover. The
  fixture records the SHA-256 of `mkdocs.yml`, and the extractor refuses a
  `site/` older than `mkdocs.yml`. Nothing compares the digest yet; a check
  that does would fail any PR that edits the nav without regenerating.
- `check:redirects` takes `DOCS_REQUIRE_NO_DEFERRED=1` to turn a non-empty
  deferred list into a failure. #322 turns it on for good.
- `build-info.json` is generated in `prebuild` and in `precheck`, so `npm run
  check` works on a clean checkout. Both write `src/data/` and `public/`, so
  running `check` after `build` leaves a newer `buildTime` in `src/data/` than
  in `dist/`; nothing compares the two.
- `deploy-pages.yml` concurrency group `pages` vs `docs.yml`'s `github-pages`:
  reconcile at cutover (#322).
- `publish.yml` has workflow-level `contents: read`; the new `docs` job declares
  its own higher ceiling, which is what a called workflow inherits.
- `astro-site/.generated/` is not committed: the griffe dump embeds the
  absolute source paths of the machine that produced it. Everything downstream
  of it is committed and checked by `check:reference`.
- The reference pages are named after the module they document, and a symbol's
  id is its canonical import path (`helia_profiler.ProfileConfig`) whatever
  page it is grouped onto. `src/data/api-groups.json` is the grouping; editing
  it and running `npm run prepare:docs` is how pages are split or merged.
- pyref writes no per-module Markdown, so `build-reference.mjs` cuts
  `llms-full.txt` into one `.md` per page beside each module's `.json`
  (`dist/reference/api/helia_profiler/config.md`). It cannot go at
  `<route>/index.md`: Starlight writes its own rendition there, and that one
  drops the `Ref*` props (helia-ui#122).
- `helia_profiler.examples` is a submodule that `__all__` publishes, so it is a
  page with no symbols rather than an 85th symbol. 85 names = 84 symbols + 1
  module page.
- Provenance is the git tree of `src/helia_profiler`
  (`git rev-parse HEAD:src/helia_profiler`), never a commit and never a ref. A
  commit sha rewrites 44 of 47 generated files on any change under src/ and
  does not survive the squash merges this repository uses. `check:reference`
  refuses a committed artifact that carries any other 40-hex hash or any ref.
- Source links are committed with the `__DOCS_SOURCE_REF__` placeholder and
  resolved at build time by `src/integrations/source-ref.mjs`, to the release
  tag when built from a tag and otherwise `main`. That integration must stay
  last in `astro.config.mjs`: Starlight and helia-ui write their Markdown
  renditions and llms exports in `astro:build:done` too, and hooks run in
  declaration order. The dev server never reaches that hook, so the same
  integration also registers a Vite transform over `.mdx`, which is what makes
  a source link followable in `astro dev`. A running dev server needs a
  restart to pick either of them up.
- Regenerating needs a clean `src/helia_profiler`, since griffe reads the
  working tree while provenance names the tree of HEAD. `DOCS_ALLOW_DIRTY_SOURCE=1`
  skips that guard for local preview; CI regenerates from the commit and
  compares, so the escape hatch cannot reach a merge.
- The generated pages and artifacts are committed by decision, not by
  accident: the stale gate is what caught a dropped field during review, and a
  nav or page change is reviewable in the diff. Tree-sha provenance is what
  makes that affordable, because the files no longer churn per commit.
  1.4 MB under `public/reference/api/`, `reference.json` 538 KB of it.
- The 404 lives at `src/pages/404.astro` and Starlight's own `/404` is turned
  off with its `disable404Route` option. Both routes at one path is a warning
  in Astro 7 and a hard error later. Moving the page into the docs collection
  instead would put it in `llms.txt` and give it an `/404/index.md`, and
  alpha.15 still has no per-page exclusion hook (helia-ui#122).
- `public/favicon.ico` is a 16/32 px PNG-in-ICO built from
  `heliaprofiler-icon.png`. It is served at `/helia-profiler/favicon.ico`; a
  browser asking for the origin root `/favicon.ico` is asking a different site,
  which this repository does not own.
- helia-ui does not run griffe, it reads the griffe 1.7.3 dump schema. Pin it.
- typer vendors click: `isinstance(cmd, click.Group)` on top-level `click` is
  always False.
- Shared stash across worktrees: WIP commits, never a bare stash.
- Scratchpad read-only checkouts (may vanish): `hpx-main-audit` (a822b51),
  `helia-ui-audit` (alpha.14), `helia-rt-audit`.
