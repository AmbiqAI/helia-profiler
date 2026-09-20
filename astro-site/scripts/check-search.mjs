/*
 * Asserts that the artifact carries a usable Pagefind index.
 *
 * Pagefind's query API is a browser module: it fetches its wasm relative to
 * its own URL and needs a DOM to run in, so a real query would mean shipping a
 * headless browser into this job. The index files answer the same question
 * without one. A fragment is gzip with a twelve byte marker in front of the
 * JSON; the word index is gzip over a binary posting list whose vocabulary is
 * stored as plain text.
 *
 * So: the term has to be in the vocabulary, which is what makes it queryable,
 * and it has to be in the indexed content of the page the query should return.
 * A page that Pagefind skipped fails the first; a page whose body never made
 * it into the index fails the second.
 */
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(site, 'dist');
const pagefind = path.join(dist, 'pagefind');

/* Lower case and already its own stem, so what is in the vocabulary is what
 * a reader types. It appears on Home and nowhere else in the skeleton. */
const TERM = 'executorch';
const HOME = '/';
const MARKER = 'pagefind_dcd';

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
  return condition;
};

if (!fs.existsSync(pagefind)) {
  throw new Error(`No Pagefind index at ${pagefind}. Run npm run build first.`);
}

const entry = JSON.parse(fs.readFileSync(path.join(pagefind, 'pagefind-entry.json'), 'utf8'));
check(Boolean(entry.version), 'pagefind-entry.json declares no version.');

const inflate = (file) => {
  const raw = zlib.gunzipSync(fs.readFileSync(file));
  const text = raw.toString('latin1');
  return text.startsWith(MARKER) ? text.slice(MARKER.length) : text;
};

const fragments = fs
  .readdirSync(path.join(pagefind, 'fragment'))
  .map((name) => JSON.parse(inflate(path.join(pagefind, 'fragment', name))));

const languages = Object.entries(entry.languages ?? {});
check(languages.length > 0, 'pagefind-entry.json declares no languages.');

let vocabulary = '';
for (const [language, meta] of languages) {
  check(
    meta.page_count === fragments.length,
    `Language ${language} claims ${meta.page_count} pages, ${fragments.length} fragments exist.`,
  );
  const indexes = fs
    .readdirSync(path.join(pagefind, 'index'))
    .filter((name) => name.startsWith(`${language}_`));
  if (!check(indexes.length > 0, `Language ${language} has no index shard.`)) continue;
  for (const name of indexes) vocabulary += inflate(path.join(pagefind, 'index', name));
}

check(
  vocabulary.includes(TERM),
  `"${TERM}" is not in the Pagefind vocabulary, so no query can return it.`,
);

const home = fragments.find((fragment) => fragment.url === HOME);
if (check(Boolean(home), `No Pagefind fragment for the Home route ${HOME}.`)) {
  check(
    home.content.toLowerCase().includes(TERM),
    `The Home fragment does not contain "${TERM}".`,
  );
}

if (failures.length > 0) {
  console.error('Search assertions failed:\n');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `Pagefind ${entry.version} indexed ${fragments.length} pages; "${TERM}" is in the vocabulary ` +
    `and in the Home fragment (${home.url}).`,
);
