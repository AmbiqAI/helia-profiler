#!/usr/bin/env node
/*
 * Fails when the committed CLI, configuration and issue-code reference is not
 * what the package produces.
 *
 * The comparison is between what git holds and a fresh generation into a
 * scratch directory. Regenerating in place and then comparing the result with
 * itself passes on any input: the prebuild chain rewrites these same files
 * before any check runs, so the working tree is never the committed state by
 * the time anything looks at it.
 *
 * Provenance is the git tree of the documented source, which is the same for
 * every commit that does not change src/, so the comparison can be exact:
 * what is committed must be byte-for-byte what the source produces. That is a
 * stricter gate than `check_reference.py --check`, which compares meaning and
 * is the gate that names what changed; this one catches a page or a Markdown
 * rendition edited by hand, which the semantic check never reads.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { sourceTree } from './build-reference.mjs';
import { DATA_DIR, GENERATED, PUBLIC_DIRS } from './build-cli-reference.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

const git = (args, options = {}) =>
  execFileSync('git', args, { cwd: repo, encoding: 'utf8', maxBuffer: 1 << 28, ...options });

const TRACKED = GENERATED.map((entry) => `astro-site/${entry}`);

/* A local edit to a generated file is the same failure as a stale commit and
 * says so earlier. CI checks out clean, so this only ever fires locally. */
const uncommitted = git(['status', '--porcelain', '--', ...TRACKED]).trim();
if (uncommitted) {
  console.error('Generated CLI reference files have uncommitted changes:\n');
  console.error(uncommitted);
  console.error('\nCommit them, or run `npm run prepare:docs` and commit the result.');
  process.exit(1);
}

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'hpx-cli-reference-'));
try {
  execFileSync('node', [path.join(site, 'scripts/build-cli-reference.mjs'), '--out', scratch], {
    cwd: site,
    stdio: 'inherit',
  });

  const committed = new Map();
  for (const tracked of git(['ls-files', '-z', ...TRACKED])
    .split('\0')
    .filter(Boolean)) {
    committed.set(path.relative('astro-site', tracked), git(['show', `HEAD:${tracked}`]));
  }

  const regenerated = new Map();
  const walk = (directory, prefix) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);
      const relative = path.posix.join(prefix, entry.name);
      if (entry.isDirectory()) walk(entryPath, relative);
      else regenerated.set(relative, fs.readFileSync(entryPath, 'utf8'));
    }
  };
  for (const entry of GENERATED) {
    const target = path.join(scratch, entry);
    if (fs.statSync(target, { throwIfNoEntry: false })?.isDirectory()) walk(target, entry);
    else if (fs.existsSync(target)) regenerated.set(entry, fs.readFileSync(target, 'utf8'));
  }

  const failures = [];
  if (committed.size === 0) {
    failures.push(`git holds no generated CLI reference under ${GENERATED.join(', ')}.`);
  }
  for (const [file, body] of regenerated) {
    if (!committed.has(file)) failures.push(`${file}: generated but not committed.`);
    else if (committed.get(file) !== body) {
      failures.push(`${file}: the committed copy differs from what the source produces.`);
    }
  }
  for (const file of committed.keys()) {
    if (!regenerated.has(file)) failures.push(`${file}: committed but no longer generated.`);
  }

  /* Provenance on its own, because it is the one field the extractors stamp
   * rather than derive: the tree the committed artifacts claim to document,
   * against the tree this checkout holds. */
  const expected = sourceTree(repo);
  const provenanced = [
    `${DATA_DIR}/cli.json`,
    `${DATA_DIR}/schema.json`,
    `${DATA_DIR}/issues.json`,
    `${PUBLIC_DIRS.cli}/cli.json`,
    `${PUBLIC_DIRS.configuration}/schema.json`,
    `${PUBLIC_DIRS.issues}/issues.json`,
  ];
  for (const file of provenanced) {
    const body = committed.get(file);
    if (body === undefined) {
      failures.push(`${file}: not committed.`);
      continue;
    }
    const recorded = JSON.parse(body).generatedFrom?.sourceTree;
    if (recorded !== expected) {
      failures.push(`${file}: records tree ${recorded}, src/helia_profiler is tree ${expected}.`);
    }
  }

  if (failures.length > 0) {
    console.error('The committed CLI reference is stale:\n');
    for (const failure of failures) console.error(`- ${failure}`);
    console.error('\nRun `npm run prepare:docs` and commit the result.');
    process.exit(1);
  }

  console.log(
    `CLI reference is current: ${committed.size} committed files match a fresh generation, ` +
      `source tree ${expected.slice(0, 7)}.`,
  );
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
