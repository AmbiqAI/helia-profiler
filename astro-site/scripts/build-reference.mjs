#!/usr/bin/env node
/*
 * The Python reference pipeline: scoped dump -> pyref -> per-module Markdown.
 *
 * The griffe dump itself is produced by dump-python.mjs, which needs uv and a
 * pinned griffe; this script assumes it has run and fails with that
 * instruction if it has not.
 *
 * pyref writes the pages, reference.json, the per-module JSON and the two
 * llms files. It does not write per-module Markdown, so the last step here
 * cuts llms-full.txt back into one file per page. That is the agent-facing
 * rendition of a page: helia-ui's own discoverability integration writes a
 * `<route>/index.md` next to every page, but it reduces the reference
 * components to their prose and drops signatures, parameter tables and enum
 * members (helia-ui#122), so it cannot be the completeness target.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const ROUTE_PREFIX = 'reference/api';
export const SOURCE_PATH = 'src/helia_profiler';
export const PAGES_DIR = 'src/content/docs/reference/api';
export const SIDEBAR_FILE = 'src/generated/api-sidebar.json';

/*
 * The commit of the documented source, not HEAD: the model records it, and
 * HEAD would make every unrelated commit look like reference drift.
 *
 * A source archive, a container copy or a shallow clone can all answer `git
 * log` with nothing, which would ship a page claiming a provenance it does
 * not have. Each of those is named here rather than papered over.
 */
export function sourceCommit(repoRoot, { run = gitRunner } = {}) {
  let inside;
  try {
    inside = run(repoRoot, ['rev-parse', '--is-inside-work-tree']);
  } catch (error) {
    throw new Error(
      `Cannot read the source commit: ${repoRoot} is not a git checkout ` +
        `(${error.message}). The reference records the commit of ${SOURCE_PATH}; ` +
        'build it from a git checkout, and in CI with actions/checkout fetch-depth: 0.',
    );
  }
  if (inside !== 'true') {
    throw new Error(
      `Cannot read the source commit: ${repoRoot} is not a git checkout. ` +
        'Build the reference from a git checkout, and in CI with actions/checkout ' +
        'fetch-depth: 0.',
    );
  }

  const commit = run(repoRoot, ['log', '-1', '--format=%H', '--', SOURCE_PATH]);
  if (!/^[0-9a-f]{40}$/.test(commit)) {
    throw new Error(
      `Cannot read the source commit: git log over ${SOURCE_PATH} returned ` +
        `"${commit}". A shallow clone has no commit that touches it; set ` +
        'fetch-depth: 0 on actions/checkout.',
    );
  }
  return commit;
}

function gitRunner(cwd, args) {
  return execFileSync('git', args, {
    cwd,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

/**
 * Cut pyref's llms-full.txt back into one Markdown document per page.
 *
 * renderModuleMarkdown opens every module with `# <module path>` and the
 * artifact joins them with a horizontal rule, so the page boundaries are the
 * module paths the model already lists.
 */
export function splitLlmsFull(llmsFull, modulePaths) {
  const ordered = [...modulePaths].sort((a, b) => b.length - a.length);
  const heading = new RegExp(
    `^# (${ordered.map((dotted) => dotted.replace(/\./g, '\\.')).join('|')})$`,
    'gm',
  );
  const cuts = [...llmsFull.matchAll(heading)];
  const sections = new Map();
  cuts.forEach((match, position) => {
    const end = cuts[position + 1]?.index ?? llmsFull.length;
    const body = llmsFull
      .slice(match.index, end)
      .replace(/\n+---\n*$/, '')
      .trimEnd();
    sections.set(match[1], `${body}\n`);
  });
  const missing = [...modulePaths].filter((dotted) => !sections.has(dotted));
  if (missing.length > 0) {
    throw new Error(`llms-full.txt has no section for ${missing.join(', ')}.`);
  }
  return sections;
}

/**
 * Where each module's artifacts live, keyed by module path.
 *
 * pyref slugs a module path into a route and writes the module's JSON there.
 * Reading that back is how the Markdown lands beside it: reimplementing the
 * slug rule here would be a second copy of it, free to drift, and the rule is
 * not reachable through the package's exports map (helia-ui#123).
 */
export function artifactPaths(artifactRoot) {
  const found = new Map();
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        walk(entryPath);
        continue;
      }
      if (!entry.name.endsWith('.json') || entryPath === path.join(artifactRoot, 'reference.json')) {
        continue;
      }
      const model = JSON.parse(fs.readFileSync(entryPath, 'utf8'));
      const dotted = model.modules?.[0]?.path;
      if (dotted) found.set(dotted, entryPath.replace(/\.json$/, '.md'));
    }
  };
  walk(artifactRoot);
  return found;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const check = process.argv.includes('--check');
  const outRoot = process.argv.includes('--out')
    ? path.resolve(process.argv[process.argv.indexOf('--out') + 1])
    : site;

  const generated = path.join(site, '.generated');
  if (!fs.existsSync(path.join(generated, 'griffe.json'))) {
    throw new Error('No .generated/griffe.json. Run `npm run reference:dump` first.');
  }

  const run = (command, args, options = {}) =>
    execFileSync(command, args, { cwd: site, stdio: 'inherit', ...options });

  const commit = sourceCommit(repo);
  run('node', ['scripts/scope-dump.mjs', '--commit', commit]);

  const publicDir = path.join(outRoot, 'public');
  const pagesDir = path.join(outRoot, PAGES_DIR);

  run('npx', [
    'helia-ui-pyref',
    '--input',
    path.join(generated, 'griffe.scoped.json'),
    '--out',
    pagesDir,
    '--public',
    publicDir,
    '--base',
    '/helia-profiler/',
    '--site',
    'https://ambiqai.github.io',
    '--route-prefix',
    ROUTE_PREFIX,
    '--sidebar',
    path.join(outRoot, SIDEBAR_FILE),
    /* Without this every source link in the model would carry the absolute
     * path of the machine that produced the dump. */
    '--source-root',
    repo,
    '--source-url',
    'https://github.com/AmbiqAI/helia-profiler/blob/{commit}/{path}#L{line}'.replace(
      '{commit}',
      commit,
    ),
    '--commit',
    commit,
    /* __version__ is the one dunder the package publishes; the default
     * filters drop every underscore name but __init__. */
    '--filter',
    '!^_',
    '--filter',
    '^__init__$',
    '--filter',
    '^__version__$',
    ...(check ? ['--check'] : []),
  ]);

  const report = JSON.parse(fs.readFileSync(path.join(generated, 'scope-report.json'), 'utf8'));
  const modulePaths = report.pages.map((page) => page.path);
  const artifactRoot = path.join(publicDir, ROUTE_PREFIX);
  const llmsFull = fs.readFileSync(path.join(artifactRoot, 'llms-full.txt'), 'utf8');
  const sections = splitLlmsFull(llmsFull, modulePaths);
  const markdownFor = artifactPaths(artifactRoot);

  let drifted = 0;
  for (const [dotted, body] of sections) {
    const file = markdownFor.get(dotted);
    if (!file) throw new Error(`pyref wrote no JSON artifact for ${dotted}.`);
    const current = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null;
    if (current === body) continue;
    if (check) {
      console.error(`${current === null ? 'missing' : 'stale'}: ${path.relative(outRoot, file)}`);
      drifted += 1;
      continue;
    }
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, body, 'utf8');
  }
  if (drifted > 0) {
    console.error('Run `npm run reference:build` and commit the result.');
    process.exit(1);
  }

  console.log(
    `reference: ${report.counts.symbols} symbols and ${report.counts.modulePages} module page ` +
      `across ${sections.size} pages, source commit ${commit.slice(0, 7)}.`,
  );
}
