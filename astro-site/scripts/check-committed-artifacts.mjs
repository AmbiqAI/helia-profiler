#!/usr/bin/env node
/*
 * No committed artifact may carry an absolute filesystem path, a commit sha,
 * or a git ref.
 *
 * The griffe dump records the absolute path of every source file on the
 * machine that produced it, so anything derived from it leaks that machine's
 * layout unless pyref's --source-root strips it. The dump itself is not
 * committed; this is the assertion that nothing downstream of it is either.
 *
 * A ref in a committed file is wrong whatever it names: a branch would send
 * every build's source links to that branch, and a commit sha would rewrite
 * every generated file on every change under src/ and would not survive the
 * squash merges this repository uses. The ref is substituted at build time,
 * so what is committed is the placeholder.
 *
 * Two kinds of 40-hex hash are allowed and no third. The git tree of the
 * documented source is the provenance the reference records. A hash the
 * package itself declares is content, not provenance: the compatibility
 * baseline pins neuralspotx by commit and by sha256, and a configuration
 * reference that dropped the pinned default would be documenting a different
 * package. Membership is decided by looking the hash up in the source at HEAD
 * rather than by a file allowlist, so a build machine's own commit sha still
 * fails wherever it appears.
 *
 * Committed content is read from git rather than from the working tree: the
 * prebuild chain rewrites these files, so by the time a check runs the tree
 * is a fresh generation and the question being asked is about the commit.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PAGES_DIR, SOURCE_PATH } from './build-reference.mjs';
import { PAGE_DIRS } from './build-cli-reference.mjs';
import { SOURCE_REF_TOKEN } from '../src/integrations/source-ref.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

const SCANNED = [
  'astro-site/src/data',
  'astro-site/public',
  'astro-site/src/generated',
  `astro-site/${PAGES_DIR}`,
  ...PAGE_DIRS.map((directory) => `astro-site/${directory}`),
];

/* The roots a build machine actually has. A bare leading slash is not enough,
 * because every route on this site starts with one, and a Windows drive letter
 * is not worth matching: `X:\` is what a colon before an escaped newline looks
 * like in JSON, and the one docstring that mentions Windows paths is prose. */
const ABSOLUTE =
  /(?:^|[^\w./])\/(?:Users|home|root|tmp|var|opt|private|mnt|srv|builds|workspace|runner)\//;

const git = (args) =>
  execFileSync('git', args, { cwd: repo, encoding: 'utf8', maxBuffer: 1 << 28 });

const tracked = git(['ls-files', '-z', ...SCANNED]).split('\0').filter(Boolean);
if (tracked.length === 0) {
  throw new Error(`git tracks no files under ${SCANNED.join(', ')}; the check would pass vacuously.`);
}

const sourceTree = git(['rev-parse', `HEAD:${SOURCE_PATH}`]).trim();
const HASH = /\b[0-9a-f]{40}\b/g;
const REF = /\/blob\/([^/"'\s)]+)\//g;

/**
 * The compatibility baseline pins neuralspotx by commit and by digest, and those
 * pins are config defaults that reach schema.json. Only that file may vouch for a
 * hash; anything else under src/ carrying one is still a leak.
 */
const PINNED_HASHES = `${SOURCE_PATH}/data/compatibility-baseline-v1.json`;

const declared = new Map();
const declaredInSource = (hash) => {
  if (!declared.has(hash)) {
    let found = false;
    try {
      found = git(['grep', '-l', '--fixed-strings', hash, 'HEAD', '--', PINNED_HASHES]).length > 0;
    } catch {
      /* git grep exits 1 when nothing matches, which is the answer, not a fault. */
    }
    declared.set(hash, found);
  }
  return declared.get(hash);
};

const failures = [];
for (const file of tracked) {
  /* A binary asset has no paths to leak and no encoding to assume. */
  if (/\.(png|jpe?g|gif|webp|avif|ico|woff2?|pdf)$/i.test(file)) continue;
  const body = git(['show', `HEAD:${file}`]);
  body.split('\n').forEach((line, index) => {
    const at = `${file}:${index + 1}`;
    if (ABSOLUTE.test(line)) failures.push(`${at}: ${line.trim().slice(0, 160)}`);
    if (line.includes(repo)) failures.push(`${at}: carries the checkout path.`);
    for (const [hash] of line.matchAll(HASH)) {
      if (hash !== sourceTree && !declaredInSource(hash)) {
        failures.push(`${at}: carries the hash ${hash}.`);
      }
    }
    for (const [, ref] of line.matchAll(REF)) {
      if (ref !== SOURCE_REF_TOKEN) failures.push(`${at}: a source link names the ref ${ref}.`);
    }
  });
}

if (failures.length > 0) {
  console.error('Committed artifacts carry something that is not portable:\n');
  for (const failure of failures.slice(0, 40)) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `${tracked.length} committed files under ${SCANNED.join(', ')} carry no absolute path, ` +
    `no ref and no hash but the ${SOURCE_PATH} tree ${sourceTree.slice(0, 7)}.`,
);
