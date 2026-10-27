# Maintainer Documentation

Material for contributors, release owners and hardware CI operators, kept
out of `astro-site/` so it is not part of the published documentation site.

| File | What it covers |
| --- | --- |
| [`documentation.md`](documentation.md) | Building, checking and publishing the docs site and generated reference. |
| [`cli-choice-compatibility.md`](cli-choice-compatibility.md) | Public CLI choices, enum handling and supported parser behavior. |
| [`architecture.md`](architecture.md) | Profile and retained-run owners, shared stages, verification boundaries and focused tests. |
| [`fixed-fixture.md`](fixed-fixture.md) | Fixed-input run API, station setup, runtime preparation and retained evidence. |
| [`fixture-energy.md`](fixture-energy.md) | Fixed-fixture power capture and energy contracts. |
| [`dependency-security.md`](dependency-security.md) | Dependency advisory guards, the approved docs cache-policy disposition and reassessment triggers. |
| [`certificate-trust.md`](certificate-trust.md) | Certificate incident remediation, environment checks and rollout responsibilities. |
| [`releasing.md`](releasing.md) | Release Please flow, version markers, and the trusted-publishing PyPI workflow. |
| [`hardware-ci.md`](hardware-ci.md) | Running `hpx validate` on the self-hosted GitHub Actions runner and what it uploads. |
| [`validation-bundle-compare.md`](validation-bundle-compare.md) | Internals of `hpx compare --validation`: manifest contract, case matching, eligibility, and outputs. |
| [`internal-power-deadline.md`](internal-power-deadline.md) | Internal power collection deadlines and reference-clock stall accounting. |
| [`power-environment-diagnostics.md`](power-environment-diagnostics.md) | Shared device-compatibility rules and host-only environment diagnostics. |
| [`capture-timeline.md`](capture-timeline.md) | The gated power capture timeline contract: steps, host wait bounds, published intervals, error precedence, hint claims, and constant provenance. The acceptance spec for bench validation. |
| [`compile-gate.md`](compile-gate.md) | The #187 Tier 1 rendered-firmware compile gate: stub tree, maintenance rule, GNU-only scope, known-bug ledger. |

Everything user-facing belongs on the site instead: a page under
`astro-site/src/content/docs/` is a published page.

Start with [architecture.md](architecture.md) for implementation ownership and
[`../AGENTS.md`](../AGENTS.md) for repository rules.
