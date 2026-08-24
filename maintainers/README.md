# Maintainer Documentation

Material for people releasing heliaPROFILER or running its hardware CI, kept
out of `docs/` so it is not part of the published documentation site.

| File | What it covers |
| --- | --- |
| [`releasing.md`](releasing.md) | Release Please flow, version markers, and the trusted-publishing PyPI workflow. |
| [`hardware-ci.md`](hardware-ci.md) | Running `hpx validate` on the self-hosted GitHub Actions runner and what it uploads. |
| [`validation-bundle-compare.md`](validation-bundle-compare.md) | Internals of `hpx compare --validation`: manifest contract, case matching, eligibility, and outputs. |
| [`compile-gate.md`](compile-gate.md) | The #187 Tier 1 rendered-firmware compile gate: stub tree, maintenance rule, GNU-only scope, known-bug ledger. |

Everything user-facing belongs on the site instead. `docs/` is published in
full — `exclude_docs` in `mkdocs.yml` is honoured by mkdocs but ignored by
zensical, which is what the Pages workflow actually runs, so a page placed in
`docs/` is a published page.

Contributor-facing architecture and repo rules live in
[`../AGENTS.md`](../AGENTS.md).
