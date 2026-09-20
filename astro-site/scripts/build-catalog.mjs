#!/usr/bin/env node
/*
 * Writes src/data/catalog.json: the board and engine identifiers Home renders.
 *
 * Home names hardware, and a hand-typed list of board ids on a landing page
 * drifts from the registry silently. This is what stops it: the ids and the
 * counts come from the package source, and check-output.mjs asserts the built
 * page carries every one of them.
 *
 * Extraction is `scripts/dump-catalog.py`, which parses rather than imports.
 * The provenance is the git tree of the documented source, the same identity
 * the Python reference carries and for the same reasons; see `sourceTree`.
 *
 * Only identity travels. Channel is the registry's own word ("stable",
 * "preview"). Everything else Home says about a board or an engine, including
 * the "(experimental)" label and each engine's "best for" line, is quoted from
 * docs/guide/boards.md and docs/guide/engines.md and stays in the page.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { SOURCE_PATH, sourceTree } from './build-reference.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const CATALOG_FILE = 'src/data/catalog.json';

/** The interpreter the documentation job sets up. `python` on a bare Windows. */
function pythonCommand({ exec = (cmd, args) => execFileSync(cmd, args, { encoding: 'utf8' }) } = {}) {
  for (const command of ['python3', 'python']) {
    try {
      exec(command, ['--version']);
      return command;
    } catch {
      /* Try the next spelling. */
    }
  }
  throw new Error('No python3 or python on PATH. The catalog is read from the package source.');
}

/**
 * The committed artifact, from the extractor's output and the source tree.
 *
 * Separated from the IO so the shape is assertable without a Python process.
 */
export function catalogFrom(dump, tree) {
  const { boards, engines } = dump;
  if (!Array.isArray(boards) || boards.length === 0) {
    throw new Error('Catalog dump carries no boards.');
  }
  if (!Array.isArray(engines) || engines.length === 0) {
    throw new Error('Catalog dump carries no engines.');
  }
  const duplicates = (items) =>
    items.map((item) => item.id).filter((id, index, all) => all.indexOf(id) !== index);
  for (const [label, items] of [
    ['board', boards],
    ['engine', engines],
  ]) {
    const repeated = duplicates(items);
    if (repeated.length > 0) {
      throw new Error(`Catalog dump repeats ${label} ${repeated.join(', ')}.`);
    }
  }
  return {
    generatedFrom: { sourceTree: tree, sourcePath: SOURCE_PATH },
    counts: { boards: boards.length, engines: engines.length },
    boards,
    engines,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const outRoot = process.argv.includes('--out')
    ? path.resolve(process.argv[process.argv.indexOf('--out') + 1])
    : site;

  const python = pythonCommand();
  const dump = JSON.parse(
    execFileSync(python, [path.join(site, 'scripts/dump-catalog.py'), '--source', path.join(repo, SOURCE_PATH)], {
      cwd: repo,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'inherit'],
    }),
  );

  const tree = sourceTree(repo, { allowDirty: Boolean(process.env.DOCS_ALLOW_DIRTY_SOURCE) });
  const catalog = catalogFrom(dump, tree);

  const target = path.join(outRoot, CATALOG_FILE);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, `${JSON.stringify(catalog, null, 2)}\n`, 'utf8');

  console.log(
    `catalog: ${catalog.counts.boards} boards, ${catalog.counts.engines} engines ` +
      `from ${SOURCE_PATH} tree ${tree.slice(0, 12)} to ${CATALOG_FILE}.`,
  );
}
