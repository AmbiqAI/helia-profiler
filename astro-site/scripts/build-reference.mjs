#!/usr/bin/env node
/*
 * The Python reference pipeline: griffe dump -> scoped dump -> pyref.
 *
 * `--check` is passed through to pyref, which re-renders in memory and fails
 * on drift; that is the CI gate against a reference that no longer matches the
 * source it documents.
 */
import { execFileSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const check = process.argv.includes('--check');

const run = (cmd, args) =>
  execFileSync(cmd, args, { cwd: root, stdio: 'inherit' });

run('node', ['scripts/scope-dump.mjs']);

/* The commit of the documented source, not HEAD: the model records it, so
 * HEAD would make every commit look like reference drift under --check. */
const commit = execFileSync(
  'git',
  ['log', '-1', '--format=%H', '--', 'src/helia_profiler'],
  { cwd: resolve(root, '..'), encoding: 'utf8' },
).trim();

run('npx', [
  'helia-ui-pyref',
  '--input',
  'spike/griffe.scoped.json',
  '--out',
  'src/content/docs/reference/api',
  '--public',
  'public',
  '--base',
  '/helia-profiler/',
  '--site',
  'https://ambiqai.github.io',
  '--sidebar',
  'src/generated/api-sidebar.json',
  '--source-root',
  resolve(root, '..'),
  '--source-url',
  'https://github.com/AmbiqAI/helia-profiler/blob/main/{path}#L{line}',
  '--commit',
  commit,
  /* __version__ is the one dunder the package publishes; the default filters
   * drop every underscore name but __init__. */
  '--filter',
  '!^_',
  '--filter',
  '^__init__$',
  '--filter',
  '^__version__$',
  ...(check ? ['--check'] : []),
]);

const scope = JSON.parse(await readFile(join(root, 'spike/scope-report.json'), 'utf8'));
console.log(
  `reference: ${scope.rendered.length} symbols across ${scope.modules.length} modules.`,
);
