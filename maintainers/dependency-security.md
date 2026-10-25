# Dependency advisory follow-up

[Issue #602](https://github.com/AmbiqAI/helia-profiler/issues/602) tracks the
Python and documentation dependency remediation. Python floors live in
`pyproject.toml` and `tests/test_security_advisories.py`. The docs lock guards
live in `astro-site/scripts/security-advisories.test.mjs`, run by
`npm run check:guard`. Check every nested lock occurrence when updating them.

## Cache advisory: proposed HPX disposition

Recommendation: **not affected by the client `max-stale` advisory path in the
currently tested HPX documentation integration**. This is a consumer-specific assessment, not a claim that the
library is fixed or that the advisory has been withdrawn. Closing #602 or
dismissing the Dependabot alert requires the owner's explicit disposition.

### Corrected interpretation

[GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp) describes
shared-cache reuse of cookied responses through a client `max-stale` directive.
The example reproduces in http-cache-semantics 4.2.0 and 4.3.0: the library
reports a zero freshness lifetime for a cookied shared response, but permits
reuse when the client allows stale content.

Our earlier assessment called this reproduction a demonstrated vulnerability.
That conclusion was too strong. [RFC 9111 section 7.3](https://www.rfc-editor.org/rfc/rfc9111.html#section-7.3)
does not make `Set-Cookie` a caching prohibition; freshness is distinct from
shared-cache privacy. The maintainer [disputes the advisory](https://github.com/kornelski/http-cache-semantics/issues/56#issuecomment-5975759591),
and the [advisory-database dispute](https://github.com/github/advisory-database/issues/10139)
remains separate from HPX's exposure assessment.

A distinct directive-handling bug does reproduce: with a stale shared response,
`max-stale` bypasses `no-cache`, `proxy-revalidate` and `s-maxage` revalidation
requirements. See [RFC 9111 section 4.2.4](https://www.rfc-editor.org/rfc/rfc9111.html#section-4.2.4)
and [upstream PR #63](https://github.com/kornelski/http-cache-semantics/pull/63).
The owner verified these cases in both published versions, with stale public
reuse and `must-revalidate` controls. `private` and `no-store` responses are
non-storable; callers must honor that result before storing them. The open
upstream proposal is not an adopted HPX fix.

### Tested integration boundary

The pinned Astro import is in `dist/assets/build/remote.js`. Its remote-image
load and revalidation helpers construct their own requests, then use
`storable()` and `timeToLive()` to calculate image expiry. They do not consult
client-controlled `max-stale` through the shared-response reuse APIs. HPX's
resolved Astro configuration has explicit static output and no server adapter;
the docs workflow publishes the built static artifact.

`astro-site/scripts/cache-boundary.test.mjs`, included in `npm run check:guard`,
checks the resolved configuration and actual installed Astro helpers with
synthetic fetch responses, without network access:

- The tested private, no-store, no-cache and stale revalidation-restricted
  headers expire immediately when no stale extensions are present.
- Requests do not carry user authorization, cookies or client cache directives;
  revalidation sends the expected conditional request header.
- Calling a shared-response reuse API from those helpers fails the test and
  requires reassessment.
- Fresh public images retain their useful lifetime, and a successful conditional
  response signals reuse of the cached image bytes and retains the validator.

The test imports Astro internals intentionally: a package-layout change must
prompt review of the boundary rather than silently skipping the checks. It
checks these identified paths, not every possible use of the dependency. Image
expiry is not a claim that private image bytes are never written to disk.

This is not an assessment of general HTTP cache compliance. Independent probes
found that adding `stale-if-error` or `stale-while-revalidate` can give restricted
images a positive lifetime, including after a 304 response. Astro's build image
pipeline treats that expiry as a cache hit and also reuses stale bytes if
revalidation throws. Those behaviors are outside the client `max-stale` path;
these tests do not certify them as safe for authenticated or private content.
The configuration assertion runs before integration hooks; the successful
static build supplies the separate publication check.

### Closeout and reassessment

The dependency and lock remain unchanged. No version-only alert suppression,
cache monkey-patch, vendored fork or advisory dismissal is part of this change.
The npm audit alert can remain visible until the owner approves a disposition.

Reassess before adding SSR, authenticated remote-image fetching, a shared HTTP
cache, or new consumers of this package, and when upgrading Astro's image
pipeline. A future upstream adoption must be checked against the directive
cases and normal public reuse, not just against the cookie example or an audit
version range. Operational certificate rollout remains tracked separately in
[#587](https://github.com/AmbiqAI/helia-profiler/issues/587).
