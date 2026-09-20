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

import { SOURCE_REF_TOKEN } from '../src/integrations/source-ref.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const ROUTE_PREFIX = 'reference/api';
export const BASE = '/helia-profiler/';
export const SITE = 'https://ambiqai.github.io';
export const SOURCE_PATH = 'src/helia_profiler';
export const PAGES_DIR = 'src/content/docs/reference/api';
export const SIDEBAR_FILE = 'src/generated/api-sidebar.json';

/*
 * The git tree of the documented source, not a commit and not a ref.
 *
 * The reference records what it was generated from, and a commit sha is the
 * wrong identity for that: it changes on every commit under src/, rewriting
 * every generated file, and it does not survive the squash merges this
 * repository uses. Two commits whose `src/helia_profiler` tree is the same
 * produce the same bytes here.
 *
 * A source archive or a container copy has no git data at all, which would
 * ship a page claiming a provenance it does not have, so that is named rather
 * than papered over.
 */
export function sourceTree(repoRoot, { run = gitRunner, allowDirty = false } = {}) {
  let inside;
  try {
    inside = run(repoRoot, ['rev-parse', '--is-inside-work-tree']);
  } catch (error) {
    throw new Error(
      `Cannot read the source tree: ${repoRoot} is not a git checkout ` +
        `(${error.message}). The reference records the git tree of ${SOURCE_PATH}; ` +
        'build it from a git checkout, and in CI with actions/checkout fetch-depth: 0.',
    );
  }
  if (inside !== 'true') {
    throw new Error(
      `Cannot read the source tree: ${repoRoot} is not a git checkout. ` +
        'Build the reference from a git checkout, and in CI with actions/checkout ' +
        'fetch-depth: 0.',
    );
  }

  /* griffe reads the working tree, so uncommitted source would be documented
   * under the tree of HEAD, which is a different thing. The escape hatch is
   * for local preview; CI regenerates from the commit and compares. */
  if (!allowDirty) {
    const dirty = run(repoRoot, ['status', '--porcelain', '--', SOURCE_PATH]);
    if (dirty) {
      throw new Error(
        `${SOURCE_PATH} has uncommitted changes, so the reference would document ` +
          'source that no tree contains:\n' +
          `${dirty}\nCommit them, or set DOCS_ALLOW_DIRTY_SOURCE=1 for a local preview.`,
      );
    }
  }

  const tree = run(repoRoot, ['rev-parse', `HEAD:${SOURCE_PATH}`]);
  if (!/^[0-9a-f]{40}$/.test(tree)) {
    throw new Error(
      `Cannot read the source tree: git rev-parse HEAD:${SOURCE_PATH} returned ` +
        `"${tree}". An empty checkout has no such tree; set fetch-depth: 0 on ` +
        'actions/checkout.',
    );
  }
  return tree;
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
 * Where each module's artifacts live: the path stem they share, keyed by
 * module path.
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
      if (dotted) found.set(dotted, entryPath.slice(0, -'.json'.length));
    }
  };
  walk(artifactRoot);
  return found;
}

/** The served URL of a module artifact, from its slug under the route prefix. */
export const urlOf = (slug, extension) =>
  `${BASE}${ROUTE_PREFIX}/${slug.split(path.sep).join('/')}.${extension}`;

/**
 * Record the source tree on every JSON artifact.
 *
 * pyref's `--commit` writes `generatedFrom.sourceCommit`, which is the
 * identity this reference deliberately does not carry.
 */
export function stampSourceTree(artifactRoot, tree) {
  const stamp = (file) => {
    const model = JSON.parse(fs.readFileSync(file, 'utf8'));
    delete model.generatedFrom?.sourceCommit;
    model.generatedFrom = { ...model.generatedFrom, sourceTree: tree };
    fs.writeFileSync(file, `${JSON.stringify(model, null, 2)}\n`, 'utf8');
  };
  const walk = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const entryPath = path.join(directory, entry.name);
      if (entry.isDirectory()) walk(entryPath);
      else if (entry.name.endsWith('.json')) stamp(entryPath);
    }
  };
  walk(artifactRoot);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const outRoot = process.argv.includes('--out')
    ? path.resolve(process.argv[process.argv.indexOf('--out') + 1])
    : site;

  const generated = path.join(site, '.generated');
  if (!fs.existsSync(path.join(generated, 'griffe.json'))) {
    throw new Error('No .generated/griffe.json. Run `npm run reference:dump` first.');
  }

  const run = (command, args, options = {}) =>
    execFileSync(command, args, { cwd: site, stdio: 'inherit', ...options });

  const tree = sourceTree(repo, { allowDirty: Boolean(process.env.DOCS_ALLOW_DIRTY_SOURCE) });
  run('node', ['scripts/scope-dump.mjs', '--tree', tree]);

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
    BASE,
    '--site',
    SITE,
    '--route-prefix',
    ROUTE_PREFIX,
    '--sidebar',
    path.join(outRoot, SIDEBAR_FILE),
    /* Without this every source link in the model would carry the absolute
     * path of the machine that produced the dump. */
    '--source-root',
    repo,
    /* No ref is resolved here. A committed page that named a branch would
     * always link to that branch, and one that named a commit would change on
     * every commit; the ref is substituted at render time from
     * build-info.json, which knows what this build is of. */
    '--source-url',
    `https://github.com/AmbiqAI/helia-profiler/blob/${SOURCE_REF_TOKEN}/{path}#L{line}`,
    /* __version__ is the one dunder the package publishes; the default
     * filters drop every underscore name but __init__. */
    '--filter',
    '!^_',
    '--filter',
    '^__init__$',
    '--filter',
    '^__version__$',
  ]);

  const report = JSON.parse(fs.readFileSync(path.join(generated, 'scope-report.json'), 'utf8'));
  const modulePaths = report.pages.map((page) => page.path);
  const artifactRoot = path.join(publicDir, ROUTE_PREFIX);
  const llmsFull = fs.readFileSync(path.join(artifactRoot, 'llms-full.txt'), 'utf8');
  const sections = splitLlmsFull(llmsFull, modulePaths);
  const artifactStems = artifactPaths(artifactRoot);

  for (const [dotted, body] of sections) {
    const stem = artifactStems.get(dotted);
    if (!stem) throw new Error(`pyref wrote no JSON artifact for ${dotted}.`);
    fs.writeFileSync(`${stem}.md`, body, 'utf8');

    /* The page links both artifacts for the module it renders. An agent that
     * arrives at the HTML otherwise has to guess that the complete Markdown
     * exists, and the rendition Starlight writes beside the page is the lossy
     * one (helia-ui#122). */
    const slug = path.relative(artifactRoot, stem);
    const page = path.join(pagesDir, slug, 'index.mdx');
    const footer =
      `\n---\n\nMachine-readable: [\`${path.basename(slug)}.json\`](${urlOf(slug, 'json')})` +
      ` · [\`${path.basename(slug)}.md\`](${urlOf(slug, 'md')}), complete and lossless.\n`;
    fs.appendFileSync(page, footer, 'utf8');
  }

  /* pyref advertises each module's JSON and not its Markdown, because it does
   * not write the Markdown. */
  const llmsIndex = path.join(artifactRoot, 'llms.txt');
  fs.writeFileSync(
    llmsIndex,
    fs
      .readFileSync(llmsIndex, 'utf8')
      .replace(
        /\(\[JSON\]\((\S+?)\.json\)\)$/gm,
        (_match, url) => `([JSON](${url}.json), [Markdown](${url}.md))`,
      ),
    'utf8',
  );

  stampSourceTree(artifactRoot, tree);

  console.log(
    `reference: ${report.counts.symbols} symbols and ${report.counts.modulePages} module page ` +
      `across ${sections.size} pages, source tree ${tree.slice(0, 7)}.`,
  );
}
