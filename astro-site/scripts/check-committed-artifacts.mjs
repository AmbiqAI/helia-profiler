#!/usr/bin/env node
/*
 * No committed artifact may carry an absolute filesystem path.
 *
 * The griffe dump records the absolute path of every source file on the
 * machine that produced it, so anything derived from it leaks that machine's
 * layout unless pyref's --source-root strips it. The dump itself is not
 * committed; this is the assertion that nothing downstream of it is either.
 *
 * Committed content is read from git rather than from the working tree: the
 * prebuild chain rewrites these files, so by the time a check runs the tree
 * is a fresh generation and the question being asked is about the commit.
 */
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

const SCANNED = ['astro-site/src/data', 'astro-site/public', 'astro-site/src/generated'];

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

const failures = [];
for (const file of tracked) {
  /* A binary asset has no paths to leak and no encoding to assume. */
  if (/\.(png|jpe?g|gif|webp|avif|ico|woff2?|pdf)$/i.test(file)) continue;
  const body = git(['show', `HEAD:${file}`]);
  body.split('\n').forEach((line, index) => {
    if (ABSOLUTE.test(line)) failures.push(`${file}:${index + 1}: ${line.trim().slice(0, 160)}`);
    if (line.includes(repo)) failures.push(`${file}:${index + 1}: carries the checkout path.`);
  });
}

if (failures.length > 0) {
  console.error('Committed artifacts carry an absolute filesystem path:\n');
  for (const failure of failures.slice(0, 40)) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(`No absolute paths in ${tracked.length} committed files under ${SCANNED.join(', ')}.`);
