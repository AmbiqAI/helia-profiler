#!/usr/bin/env node
/*
 * Fails when the committed Python reference is not what the source produces.
 *
 * The comparison is between what git holds and a fresh generation into a
 * scratch directory. Regenerating in place and then comparing the result with
 * itself, as the spike did, passes on any input: the prebuild chain rewrites
 * the same files before the check runs, so the working tree is never the
 * committed state by the time anything looks at it.
 *
 * Provenance is normalised out of the byte comparison and asserted on its
 * own. Every source link carries the commit of src/helia_profiler, so a
 * commit that touches nothing but a docstring's neighbours would otherwise
 * report every page as content drift and say nothing about what changed.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PAGES_DIR, ROUTE_PREFIX, SIDEBAR_FILE, SOURCE_PATH, sourceCommit } from './build-reference.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

/** Everything the pipeline writes and git is expected to hold. */
const GENERATED = [PAGES_DIR, SIDEBAR_FILE, `public/${ROUTE_PREFIX}`];

const git = (args, options = {}) =>
  execFileSync('git', args, { cwd: repo, encoding: 'utf8', maxBuffer: 1 << 28, ...options });

/* A 40-hex commit is provenance wherever it appears: generatedFrom in the
 * model, the source links on every symbol, the line on every page. */
const withoutProvenance = (text) => text.replace(/\b[0-9a-f]{40}\b/g, '<source-commit>');

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'hpx-reference-'));
try {
  execFileSync('node', [path.join(site, 'scripts/build-reference.mjs'), '--out', scratch], {
    cwd: site,
    stdio: 'inherit',
  });

  const committed = new Map();
  for (const tracked of git(['ls-files', '-z', ...GENERATED.map((entry) => `astro-site/${entry}`)])
    .split('\0')
    .filter(Boolean)) {
    const relative = path.relative('astro-site', tracked);
    committed.set(relative, git(['show', `HEAD:${tracked}`]));
  }

  const regenerated = new Map();
  const walk = (directory, prefix) => {
    if (!fs.existsSync(directory)) return;
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
    failures.push(`git holds no generated reference under ${GENERATED.join(', ')}.`);
  }
  for (const [file, body] of regenerated) {
    if (!committed.has(file)) failures.push(`${file}: generated but not committed.`);
    else if (withoutProvenance(committed.get(file)) !== withoutProvenance(body)) {
      failures.push(`${file}: the committed copy differs from what the source produces.`);
    }
  }
  for (const file of committed.keys()) {
    if (!regenerated.has(file)) failures.push(`${file}: committed but no longer generated.`);
  }

  /* Provenance on its own: what the committed pages claim they document, and
   * whether that commit is in the history this build is made from. */
  const expected = sourceCommit(repo);
  const claimed = new Set();
  for (const [file, body] of committed) {
    for (const [, commit] of body.matchAll(/\b([0-9a-f]{40})\b/g)) claimed.add(commit);
    if (file.startsWith(`public/${ROUTE_PREFIX}/`) && file.endsWith('reference.json')) {
      const model = JSON.parse(body);
      if (model.generatedFrom?.sourceCommit !== expected) {
        failures.push(
          `${file}: generated from ${model.generatedFrom?.sourceCommit}, ` +
            `${SOURCE_PATH} is at ${expected}.`,
        );
      }
    }
  }
  for (const commit of claimed) {
    if (commit === expected) continue;
    failures.push(`A committed artifact records ${commit}; ${SOURCE_PATH} is at ${expected}.`);
  }
  const head = git(['rev-parse', 'HEAD']).trim();
  try {
    git(['merge-base', '--is-ancestor', expected, head]);
  } catch {
    failures.push(`${expected} is not an ancestor of HEAD (${head}); the checkout may be shallow.`);
  }

  if (failures.length > 0) {
    console.error('The committed Python reference is stale:\n');
    for (const failure of failures) console.error(`- ${failure}`);
    console.error('\nRun `npm run prepare:docs` and commit the result.');
    process.exit(1);
  }

  console.log(
    `Python reference is current: ${committed.size} committed files match a fresh ` +
      `generation, source commit ${expected.slice(0, 7)}.`,
  );
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
