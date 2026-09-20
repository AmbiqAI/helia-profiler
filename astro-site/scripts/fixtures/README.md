# Deploy guard fixtures

Hand-written build-info payloads for `scripts/deploy-guard.test.mjs`. They are
not generated from a build: the point is to pin the orderings the guard has to
get right, including ones a real build would rarely produce.

- `live-0.2.0.json` stands in for what is already published.
- `candidate-*.json` are the artifacts asking to replace it.
- `live-unparseable.txt` is a live response that is not JSON at all.
