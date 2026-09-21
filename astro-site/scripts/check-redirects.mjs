/*
 * Every route the MkDocs site publishes has to keep resolving after cutover.
 *
 * src/data/legacy-routes.json is the fixture of what is published today,
 * generated from a real Zensical build (see scripts/extract-legacy-routes.mjs).
 * src/data/redirects.json answers it in three ways, and a route that fits none
 * of them fails this check:
 *
 *   served    the same path is a real page in this site, so a redirect there
 *             would collide with the page Astro builds
 *   redirects the path forwards somewhere else
 *   deferred  a subset of the redirect keys whose real target is not built
 *             yet, so it forwards to its section landing page for now
 *
 * The deferred list is empty since the cutover and the docs workflow sets
 * DOCS_REQUIRE_NO_DEFERRED=1, so a route that forwards to a section landing
 * page instead of its own target fails the build.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name) =>
  JSON.parse(fs.readFileSync(path.join(site, 'src/data', name), 'utf8'));

const fixture = read('legacy-routes.json');
const { served, deferred, redirects } = read('redirects.json');
const base = '/helia-profiler/';
const failures = [];

const mapped = new Set(Object.keys(redirects));
const servedSet = new Set(served);

for (const route of fixture.routes) {
  const isMapped = mapped.has(route);
  if (isMapped && servedSet.has(route)) {
    failures.push(`${route}: listed as served and redirected.`);
  } else if (!isMapped && !servedSet.has(route)) {
    failures.push(`${route}: neither redirected nor served by a page.`);
  }
}

const legacy = new Set(fixture.routes);
for (const route of mapped) {
  if (!legacy.has(route)) failures.push(`${route}: redirected but not a published route.`);
}
for (const route of servedSet) {
  if (!legacy.has(route)) failures.push(`${route}: listed as served but not a published route.`);
}
for (const route of deferred) {
  if (!mapped.has(route)) failures.push(`${route}: deferred without a redirect target.`);
}
if (process.env.DOCS_REQUIRE_NO_DEFERRED && deferred.length > 0) {
  failures.push(
    `${deferred.length} route${deferred.length === 1 ? '' : 's'} still forward to a section ` +
      'landing page instead of their own target.',
  );
}
for (const [route, target] of Object.entries(redirects)) {
  if (!target.startsWith(base)) failures.push(`${route}: target ${target} is outside ${base}.`);
  if (!target.endsWith('/') && !target.includes('#')) {
    failures.push(`${route}: target ${target} is not a directory route.`);
  }
}

if (failures.length > 0) {
  console.error('Redirect coverage failed:\n');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `Covered ${fixture.routes.length} published routes: ${mapped.size} redirected ` +
    `(${deferred.length} deferred to a section landing page), ${servedSet.size} served by a page.`,
);
