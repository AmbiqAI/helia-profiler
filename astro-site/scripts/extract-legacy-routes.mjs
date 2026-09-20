/*
 * Rebuilds src/data/legacy-routes.json from a Zensical build of the MkDocs
 * site. The published routes are the contract the redirect map has to satisfy,
 * and only a real build knows them: mkdocs.yml nav entries do not account for
 * index pages or for anything the builder emits on its own.
 *
 * Run from the repository root:
 *   uv sync --locked --group docs && uv run --group docs zensical build
 *   npm --prefix astro-site run legacy-routes:extract
 *
 * Only needed when mkdocs.yml changes. The fixture outlives cutover.
 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(site, '..');
const built = path.join(root, 'site');

const navigation = path.join(root, 'mkdocs.yml');

if (!fs.existsSync(built)) {
  throw new Error(
    `No Zensical build at ${built}. Run "uv run --group docs zensical build" first.`,
  );
}

/* A build left over from before the nav changed would record routes that were
 * never published, and the mismatch is invisible in the output. */
if (fs.statSync(built).mtimeMs < fs.statSync(navigation).mtimeMs) {
  throw new Error(
    `${built} is older than ${navigation}. Rebuild the site before extracting routes.`,
  );
}

const walk = (directory) =>
  fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(entryPath) : [entryPath];
  });

const routes = [
  ...new Set(
    walk(built)
      .filter((file) => path.basename(file) === 'index.html')
      .map((file) => {
        const relative = path.relative(built, path.dirname(file));
        return relative === '' ? '/' : `/${relative.split(path.sep).join('/')}/`;
      }),
  ),
].sort();

/* The digest of the file that decides the routes, not of the commit that
 * happened to be checked out: the fixture is stale when the nav changes, and
 * a commit says nothing about whether it did. */
const sourceSha256 = crypto
  .createHash('sha256')
  .update(fs.readFileSync(navigation))
  .digest('hex');

const fixture = {
  source: 'mkdocs.yml',
  sourceSha256,
  command:
    'uv sync --locked --group docs && uv run --group docs zensical build && npm --prefix astro-site run legacy-routes:extract',
  routes,
};

const target = path.join(site, 'src/data/legacy-routes.json');
fs.writeFileSync(target, `${JSON.stringify(fixture, null, 2)}\n`);
console.log(`Recorded ${routes.length} legacy routes in ${path.relative(root, target)}.`);
