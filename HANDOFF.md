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
- Phase 2, #330, branch `330-python-api-reference` off `docs-migration`:
  the Python API reference. Landed on `docs-migration` at `8420154`.
- helia-ui alpha.16 pinned on `docs-migration` at `5594891` (#336).
- Phase 3f, #335, branch `335-home-design`, rebased onto `docs-migration`
  (`5594891`). Reviewed once, fixes applied. Pushed, no PR; the owner reviews
  the copy first.

Branch `335-home-design` (#335), head `430d587`:
- Home is rebuilt from helia-ui parts: `Hero` (contrast) with `StatCard`s in
  `aside` and an `AsciiTerminal` in `media`, then five `Band`s alternating
  muted / plain / tinted / plain / muted. Pipeline band is eight `Card`s with
  `CardHeader step="S01".."S08"`, the grouping `docs/architecture/index.md:83`
  and `docs/architecture/pipeline.md:106` already use. The branch-point
  "where to start" cards, version chip and status wording are unchanged.
- Home calls the eight labels steps, not stages. A stage here is one of the 18
  `*Stage()` objects at `src/helia_profiler/profiler.py:47-64`.
- Board and engine identity is generated, not typed. `scripts/build-catalog.mjs`
  runs `scripts/dump-catalog.py` over `src/helia_profiler` and writes
  `src/data/catalog.json` (committed, `generatedFrom.sourceTree`);
  `src/lib/home-catalog.mjs` turns it into the lines Home renders, and
  `check-output.mjs` asserts the built page names every id and shows both
  counts. Negative-tested: adding a phantom board to the catalog fails the
  check on both assertions.
- The extractor parses with `ast`, never imports. `.github/workflows/docs.yml`
  installs griffe and nothing else on purpose, so a site build must not need
  the profiler's runtime environment. Do not "simplify" it to an import.
- Two local parts, both upstream candidates:
  `src/components/ProductLogo.astro` (theme-selected wordmark keyed on
  `data-theme`; alpha.16 still has no theme-selected image part, and the MkDocs
  `#only-light` / `#only-dark` fragments do nothing in Astro) and
  `src/lib/home-catalog.mjs`.
- The wordmark is served through `astro:assets` from `src/assets/`, not out of
  `public/`: 102,126 B of PNG became 13,654 B of webp for a 240 px slot.
- `src/content.config.ts` extends `docsSchema` with `heliaFrontmatterSchema`,
  which is what lets Home set `helia: { pageTitle: false }` so the hero owns
  the only `h1`.
- Bytes for Home, branch point `5594891` then head, both at alpha.16:
  see the measurement recorded on the PR. At the head: HTML 59,102 B against a
  250 KB budget, external JS 5,489 B, inline script 13,046 B of which 6,743 B
  is the one `helia-ascii-terminal` block that `AsciiTerminal copy` opts into.
  That block was 4,869 B at alpha.15; alpha.16 grew it with the #150 fix.
- Working notes not committed: `SOURCES.md` (every Home sentence mapped to its
  docs page and line, plus the rendition loss table) and `SCREENSHOTS/` (1280
  and 375 CSS px, light and dark, from `astro preview` of the built artifact
  via Playwright).
- Open, for the owner: sign-off on the copy, and one upstream helia-ui issue
  for what Home loses in its Markdown rendition. The precise list is the last
  table in `SOURCES.md`; the headline is that `CardList items`, `StatCard
  label`, `SectionHeader` titles and `Callout title` never reach `index.md`,
  so no board or engine id reaches an agent through the hardware band.

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
- pyref gap: the model carries no parameters for any of the 85 names, because
  it reads them from the docstring's `Args:` section and no published symbol
  has one. `check:output` prints "0 parameter assertions" so the gap stays
  visible. Readers get parameter names, types and defaults from the verbatim
  signature, which is asserted in llms-full.txt and in the per-module `.md`.
  Adding `Args:` to a published docstring is what would fill the tables.
- Source links are committed with the `__DOCS_SOURCE_REF__` placeholder and
  resolved at build time by `src/integrations/source-ref.mjs` to
  `build-info.json`'s `sourceRef`: the workflow's `source_ref`, else the
  release tag, else the branch that was checked out. That integration must stay
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
- An MDX `{/* */}` comment is reproduced verbatim in the page's Markdown
  rendition, which is the artifact an agent reads. Page-level notes belong in
  YAML comments in the frontmatter, which the rendition strips. Home's source
  notes were moved there for that reason.
- `AsciiTerminal` is inert until `animated`, `copy` or `replay` is set; any one
  of them emits a 4.9 KB inline script. Ask for it deliberately and attribute
  the bytes. Home asks for `copy` only.
- `Band tone="contrast"` and `Hero variant="contrast"` are pinned dark in both
  themes, so a theme-selected logo cannot sit inside one. Home's wordmark sits
  above the hero on the page ground.
- Shared stash across worktrees: WIP commits, never a bare stash.
- Scratchpad read-only checkouts (may vanish): `hpx-main-audit` (a822b51),
  `helia-ui-audit` (alpha.14), `helia-rt-audit`.
