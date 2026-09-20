#!/usr/bin/env node
/*
 * Produce the two inputs the Python reference is generated from: the griffe
 * dump of the package in this checkout, and the API tier map the site uses to
 * scope and badge it.
 *
 * griffe is pinned to the release helia-ui's pyref reader was written against
 * (GRIFFE_VERSION in scripts/lib/pyref-extract.mjs); the dump is not a
 * versioned format, so an unpinned griffe would silently change the tree.
 * `uv run --with` keeps it out of the project's own dependency set.
 */
import { execFileSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(root, '..');

export const GRIFFE_VERSION = '1.7.3';

const uv = (args) =>
  execFileSync('uv', args, { cwd: repo, encoding: 'utf8', maxBuffer: 1 << 28 });

const version = uv([
  'run',
  '--with',
  `griffe==${GRIFFE_VERSION}`,
  'griffe',
  '--version',
]).trim();
console.log(`griffe: ${version}`);

const dump = uv([
  'run',
  '--with',
  `griffe==${GRIFFE_VERSION}`,
  'griffe',
  'dump',
  'helia_profiler',
  '--docstyle',
  'google',
  '-f',
]);
await mkdir(join(root, 'spike'), { recursive: true });
await writeFile(join(root, 'spike/griffe.json'), dump, 'utf8');
console.log(`dump: ${dump.length} bytes to spike/griffe.json`);

const tiers = uv([
  'run',
  'python',
  '-c',
  'import helia_profiler, json; print(json.dumps({"all": list(helia_profiler.__all__), "stability": helia_profiler.__api_stability__}))',
]);
await mkdir(join(root, 'src/data'), { recursive: true });
await writeFile(
  join(root, 'src/data/api-tiers.json'),
  `${JSON.stringify(JSON.parse(tiers), null, 2)}\n`,
  'utf8',
);
const parsed = JSON.parse(tiers);
const counts = {};
for (const tier of Object.values(parsed.stability))
  counts[tier] = (counts[tier] ?? 0) + 1;
console.log(
  `tiers: ${parsed.all.length} in __all__, ${JSON.stringify(counts)}`,
);
