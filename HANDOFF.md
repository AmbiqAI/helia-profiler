# HANDOFF: heliaPROFILER docs migration (Astro + helia-ui)

Goal: replace the MkDocs/Zensical site with Astro + Starlight on helia-ui, revamp
content, generate CLI/config/API reference, ship agent-readable outputs.
Epic: AmbiqAI/helia-profiler#320. Plan: draft 4, posted as a comment on #320;
Phase 0 outcome and decisions are the comment after it.

## State (2026-09-20)

Done:
- Phase 0 spikes #323 (branch `323-spike-scaffold-pyref`, PR #326) and #324
  (branch `324-spike-cli-config`, PR #327). Both stay draft and close unmerged.
- Phase 1, this branch `325-docs-skeleton` off `origin/main` fd2d510: the Astro
  skeleton, five sections, the docs workflow, redirects, 404, provenance and the
  site checks. Not yet reviewed, no PR opened.

Verified on this branch (all local, all green):
- `npm ci` from the Linux lockfile; helia-ui `v0.1.0-alpha.14` resolves to
  `95eb9f1d2dc461533ed2c9477d6d52d0ab4969e5`.
- `npm run build`, `check`, `check:links`, `check:output`, `check:search`,
  `check:redirects`, `check:guard`.
- 61 legacy routes covered: 57 redirected (all deferred to a section landing
  page), 4 already served by a page of the same path.

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

1. Owner reviews this branch, then a PR (label `agent-generated`).
2. After merge: record the three post-merge run links on #325.
3. Phase 2 reference generation (#320 plan s4): API manifest + pyref, CLI and
   config extractors in `tools/docs/`, issue codes port. `docs.yml` will need
   Python and `griffe==1.7.3` in the build job.
4. Draft the remaining child issues (plan s5 items 4-17) as their phase arrives.

## Gotchas

- Counts: `__all__` is 98 (45/40/13), 61 legacy routes, 12 top-level CLI entries
  / 14 leaves, ProfileConfig + 14 nested models / 106 fields.
- `src/data/legacy-routes.json` is regenerated only when `mkdocs.yml` changes:
  `uv sync --locked --group docs && uv run --group docs zensical build` then
  `npm --prefix astro-site run legacy-routes:extract`. It outlives cutover.
- `build-info.json` is generated in `prebuild`. A clean checkout that runs
  `astro check` before a build fails on the missing import.
- `deploy-pages.yml` concurrency group `pages` vs `docs.yml`'s `github-pages`:
  reconcile at cutover (#322).
- `publish.yml` has workflow-level `contents: read`; the new `docs` job declares
  its own higher ceiling, which is what a called workflow inherits.
- Do not commit `griffe.json` in Phase 2; it embeds absolute source paths.
- helia-ui does not run griffe, it reads the griffe 1.7.3 dump schema. Pin it.
- typer vendors click: `isinstance(cmd, click.Group)` on top-level `click` is
  always False.
- Shared stash across worktrees: WIP commits, never a bare stash.
- Scratchpad read-only checkouts (may vanish): `hpx-main-audit` (a822b51),
  `helia-ui-audit` (alpha.14), `helia-rt-audit`.
