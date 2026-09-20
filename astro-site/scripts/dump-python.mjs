#!/usr/bin/env node
/*
 * Produce the two inputs the Python reference is generated from: the griffe
 * dump of the package in this checkout, and the API tier map the reference is
 * scoped and badged with.
 *
 * griffe is pinned to the release helia-ui's pyref reader was written against.
 * The dump is not a versioned format, so an unpinned griffe would change the
 * tree under us with no signal. `uv run --with` keeps it out of the project's
 * own dependency set, which the package itself does not need.
 *
 * Both outputs land in .generated/, which is not committed: the dump embeds
 * absolute source paths from the machine that produced it.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const GRIFFE_VERSION = '1.7.3';
export const PACKAGE = 'helia_profiler';
export const GENERATED = path.join(site, '.generated');

const uv = (args) => {
  try {
    return execFileSync('uv', args, {
      cwd: repo,
      encoding: 'utf8',
      maxBuffer: 1 << 28,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  } catch (error) {
    const detail = error.stderr?.toString().trim() || error.message;
    throw new Error(`uv ${args.slice(0, 3).join(' ')} failed:\n${detail}`);
  }
};

const withGriffe = ['run', '--with', `griffe==${GRIFFE_VERSION}`];

const version = uv([...withGriffe, 'griffe', '--version']).trim();
if (!version.includes(GRIFFE_VERSION)) {
  throw new Error(`Expected griffe ${GRIFFE_VERSION}, got "${version}".`);
}
console.log(`griffe: ${version}`);

/* One parser, not `auto`: `auto` parses neither style on this package, and a
 * mixed tree renders empty parameter tables rather than failing. The source is
 * held to Google style by tests/test_docstring_style.py. */
const dump = uv([
  ...withGriffe,
  'griffe',
  'dump',
  PACKAGE,
  '--docstyle',
  'google',
  '-f',
]);

fs.mkdirSync(GENERATED, { recursive: true });
fs.writeFileSync(path.join(GENERATED, 'griffe.json'), dump, 'utf8');
console.log(`dump: ${dump.length} bytes to .generated/griffe.json`);

/* The tier map is read out of the installed package rather than restated
 * here, so adding an export to __all__ without tiering it is an error in the
 * package's own test suite, not a silent omission from the reference. */
const tiers = JSON.parse(
  uv([
    'run',
    'python',
    '-c',
    `import ${PACKAGE}, json; print(json.dumps({"all": list(${PACKAGE}.__all__), "stability": ${PACKAGE}.__api_stability__}))`,
  ]),
);

const counts = {};
for (const tier of Object.values(tiers.stability)) counts[tier] = (counts[tier] ?? 0) + 1;
const untiered = tiers.all.filter((name) => !tiers.stability[name]);
if (untiered.length > 0) {
  throw new Error(`${PACKAGE}.__all__ names without a stability tier: ${untiered.join(', ')}`);
}

fs.writeFileSync(
  path.join(GENERATED, 'api-tiers.json'),
  `${JSON.stringify(tiers, null, 2)}\n`,
  'utf8',
);
console.log(`tiers: ${tiers.all.length} in __all__, ${JSON.stringify(counts)}`);
