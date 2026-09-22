# Current checkpoint — DRAFT / NOT READY TO MERGE

PR #332 remains a draft. The current website changes are a preservation
checkpoint, not approval to merge or publish. Refs #320, #322, #365.

The Home has been revised for product comprehension, engine/memory comparisons,
plain-language benefits, a single quickstart, responsive spacing and neutral
light/dark surfaces. Main documentation corrections distinguish clean timing
from layer totals, explain actual configuration controls, and clarify benchmark
size metrics. Generated reference sources and captured result bundles remain
unchanged. The previous handoff below is historical where it conflicts with
this checkpoint.

## Shared UI reconstruction requirement

The checked-in package/lock still point to `@ambiqai/helia-ui` alpha.19.
The reviewed local preview instead used unpublished shared UI commit
`c6194ba3f5f46d1d1cdc48567b3c09ef7033f2f2`, packed as a tarball with SHA-256
`d135b11b317c469c66a2213acd11d5e7dec16c493d17967e6324ecc89342d034`.
A clean `npm ci` therefore does **not** reproduce the reviewed appearance or
new component options. Do not treat the existing dependency pin as final.

CORE owns the shared changes and preserved a self-contained Git bundle through
`44d424b7348793fd54460b55f0318f7c2630a34e`, including the exact preview commit.
To reconstruct: obtain that recovery bundle from the maintainer, clone it,
check out the exact preview commit, run `npm pack`, and install the resulting
package in `astro-site` with `npm install --no-save --package-lock=false`.
No machine-specific dependency or unreleased version is committed here.
An authorized shared publication and a reproducible package/lock update remain
required before merge. This checkpoint does not authorize that publication.

## Review and checks

Independent Home content and usability reviews were completed; demonstrated
findings were fixed and affected content was rechecked. Owner inspection covered
1218, 764 and 390px in both themes, including menu focus and the workflow,
quickstart and automation sequence. The main-docs pass was bounded navigation
and content correction, not exhaustive verification of every technical claim.

Local build, output/discoverability, links, search, redirects, and 38 script
checks passed with the local shared package. The final Home has one H1 and
retains the skip-link target. Existing power examples still lack qualified
captured power bundles; no new hardware validation was performed.

The checkpoint commit uses GitHub's supported `[skip ci]` marker because both
CI and documentation workflows trigger on draft pull requests. No fresh remote
CI success is claimed. Required checks must run on the final reproducible head
before merge; the marker does not suppress unrelated GitHub review services.

## Resume

1. Obtain Adam's final visual/content feedback; no user acceptance is recorded.
2. Resolve shared UI publication/dependency reconstruction and update the lock.
3. Recheck the exact resulting source and run required local/remote checks once
   authorized. Keep PR #332 draft until it is actually ready.
4. Obtain Adam's explicit merge approval. Merging deploys the documentation;
   post-deploy checks from the historical handoff remain required.

---

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

## State (2026-09-21, hand-off)

Every sub-PR is merged into `docs-migration` (#333 to #365). Home was
rebuilt twice today on the owner's direction: PR #364 replaced the ported
landing with a documentation overview (owner-approved mockup), PR #365 put
a health check, a profile and a compare on both entry-point cards (CLI and
the `Session` API) and the icon tiles and chips on the support cards.
#337 is closed as superseded. `main` is merged in (4f2afae). PR #332
carries the branch to `main` as one squash, CI is green at e1181b0d, and
merging it deploys the Astro site. The owner merges; it needs an admin
squash because `main` requires a review.

To serve locally: `cd astro-site && npm ci && npm run dev -- --port 4321`,
then http://localhost:4321/helia-profiler/. Home is `astro-site/src/content/
docs/index.mdx`; the registry counts on it are typed as text and held to
`src/data/catalog.json` and `src/data/schema.json` by `scripts/check-output.mjs`.

Open for the owner, none blocking the merge:
- Home wording: the boards card names the Atomiq110 FPGA carrier because
  the registry count includes it; the apollo330mP EVB sits under the
  "Apollo3, Apollo4 and Apollo5" sentence though the registry tags it AP5.
- The shipped bundles' `run_metadata.json` (runner paths, probe serial),
  kept unmodified so the manifests verify; see PR #354.
- Two issues to file once approved (drafts below). Post-deploy
  verification on #322 after #332 lands: version in the shell, search,
  redirects, 404, `llms.txt`, `llms-full.txt`, JSON endpoints.

Issue draft A, "Examples: capture the power bundles the bench-dependent
example pages need" (child of #320, #345): one `apollo510_evb` session with
a Joulescope JS220 or JS320 running `examples/quickstart/hpx_aot_power.yml`
(and the same with `helia-rt`), `hpx_full_sweep.yml` (cpu, memory, mve,
model_explorer, detailed), and an INA228 run if a carrier is on the bench;
ship each bundle unmodified under `examples/results/<date>/<case>/` with a
run record so `load_result_manifest(verify=True)` passes; update the four
"not yet validated" pages and write the three remaining #345 pages from
them. Acceptance: manifests verify, every number traces to a bundle,
`examples-template.test.mjs` and the chain green, index table updated.

Issue draft B, "Export the run-summary loader and section models"
(child of #320): add `load_run_summary`, `RunSummary`, `MemorySection`,
`BinarySection`, `LatencySection`, `PowerSection` to `helia_profiler.__all__`
with stability badges, add a `results.run_summary` group to
`astro-site/src/data/api-groups.json`, regenerate, and link Parsing outputs
and Re-analyse saved results to the pages. Acceptance: the import works and
the export test covers it, the generated pages exist, `check:reference` green.

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
