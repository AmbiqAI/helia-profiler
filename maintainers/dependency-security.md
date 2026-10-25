# Dependency advisory follow-up

[Issue #602](https://github.com/AmbiqAI/helia-profiler/issues/602) tracks the
Python and documentation dependency remediation. Python floors live in
`pyproject.toml` and `tests/test_security_advisories.py`. The docs lock guards
live in `astro-site/scripts/security-advisories.test.mjs`, run by
`npm run check:guard`. Check every nested lock occurrence when updating them.

## Open cache-policy advisory

[GHSA-ch52-4w7c-c8xp](https://github.com/advisories/GHSA-ch52-4w7c-c8xp) describes
shared-cache reuse of responses whose lifetime was zeroed for security reasons.
A synthetic response with `Set-Cookie` and `Cache-Control: max-age=3600`
produces `maxAge() === 0`. An ordinary reuse request is rejected, but a request
with `Cache-Control: max-stale=86400` is accepted by
`satisfiesWithoutRevalidation()`. The owner reproduced this in both the locked
http-cache-semantics 4.2.0 and upstream 4.3.0. A newer package version or a clean
audit result alone is therefore not evidence that this behavior is fixed.

The dependency remains unchanged in this update. Do not dismiss the advisory
or close #602 until it has an explicit disposition.

### HPX use and limits

In the pinned Astro package, the import is in `dist/assets/build/remote.js`.
The remote-image build and revalidation helpers construct their own requests,
then use `storable()` and `timeToLive()` to choose an image expiry. They do not
pass an incoming client's `max-stale` header to `satisfiesWithoutRevalidation()`
or `evaluateRequest()`. HPX publishes a static Astro site without a server
adapter. The observed shared-response reuse path is therefore not reached by
this identified integration; this is not a claim that the dependency is fixed
or safe in other consumers.

Reassess this boundary if enabling SSR, authenticated remote-image fetching,
or a shared HTTP cache. Before selecting an upstream fix, rerun the synthetic
cookie/max-stale case and a normal public cache-hit control, inspect the actual
Astro call site, and record the result in #602. Any residual-risk acceptance is
an owner decision, separate from merging the other dependency fixes.
