import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import {
  GZIP_BUDGET,
  HTML_BUDGET,
  measure,
  overBudget,
} from "./lib/page-budget.mjs";

test("a small page is within budget", () => {
  assert.deepEqual(overBudget(measure("<html><body>hello</body></html>")), []);
});

test("a page over the HTML budget is reported", () => {
  const reasons = overBudget({ bytes: HTML_BUDGET + 1, gzip: 10 });
  assert.equal(reasons.length, 1);
  assert.match(reasons[0], /HTML/);
});

test("a page over the gzip budget is reported even when the HTML fits", () => {
  const reasons = overBudget({ bytes: 1000, gzip: GZIP_BUDGET + 1 });
  assert.equal(reasons.length, 1);
  assert.match(reasons[0], /gzip/);
});

test("incompressible content over the gzip budget is measured, not assumed", () => {
  /* Random bytes do not compress, so 60,000 of them gzip to more than the budget. */
  const noise = crypto.randomBytes(60_000).toString("base64");
  assert.ok(overBudget(measure(noise)).some((reason) => /gzip/.test(reason)));
});
