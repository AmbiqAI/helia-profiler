#!/usr/bin/env node
/*
 * Narrow a griffe dump of helia_profiler to the published API surface.
 *
 * pyref renders one page per module in the dump and documents a symbol where
 * it is defined, so a raw dump of this package produces 187 pages, most of
 * them internal, and the re-export table on the package page links to paths
 * that hold nothing: `helia_profiler.__init__` aliases `ProfileResult` to
 * `helia_profiler.results.ProfileResult`, which is itself an alias to
 * `helia_profiler.results.models.ProfileResult`, and griffe records one hop.
 *
 * This pass resolves the alias chain to the defining node and re-homes the
 * symbol at the module the public import path names -- the one-hop target --
 * so the page a reader lands on matches the import they write. A symbol whose
 * public home is a private module is hoisted to the package itself.
 *
 * `__api_stability__` decides what survives: stable and experimental stay,
 * implementation is dropped from the tree and therefore from the nav, the
 * pages, reference.json and both llms exports.
 */
import { readFile, writeFile } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const PACKAGE = 'helia_profiler';
const KEPT_TIERS = new Set(['stable', 'experimental']);

const dump = JSON.parse(await readFile(join(root, 'spike/griffe.json'), 'utf8'));
const tiers = JSON.parse(
  await readFile(join(root, 'src/data/api-tiers.json'), 'utf8'),
);
const pkg = dump[PACKAGE];

/** The node at a dotted path, or undefined. */
const nodeAt = (path) => {
  const parts = path.split('.');
  if (parts.shift() !== PACKAGE) return undefined;
  let node = pkg;
  for (const part of parts) {
    node = node?.members?.[part];
    if (!node || typeof node !== 'object') return undefined;
  }
  return node;
};

/** Follow target_path until a node that is not an alias. */
function resolveAlias(path, seen = new Set()) {
  if (seen.has(path)) return undefined;
  seen.add(path);
  const node = nodeAt(path);
  if (!node) return undefined;
  if (node.kind !== 'alias') return { path, node };
  if (typeof node.target_path !== 'string') return undefined;
  return resolveAlias(node.target_path, seen);
}

const inScope = tiers.all.filter((name) => KEPT_TIERS.has(tiers.stability[name]));
const report = { rendered: [], unresolved: [], modules: [] };

/* Module skeletons, keyed by the public path a symbol is re-homed at. */
const modules = new Map();
const moduleFor = (path) => {
  if (modules.has(path)) return modules.get(path);
  const source = nodeAt(path);
  const skeleton = {
    kind: 'module',
    name: path.split('.').at(-1),
    path,
    docstring: source?.docstring,
    filepath: source?.filepath,
    labels: source?.labels ?? [],
    members: {},
  };
  modules.set(path, skeleton);
  return skeleton;
};
const pkgModule = moduleFor(PACKAGE);

for (const name of inScope) {
  const entry = pkg.members[name];
  if (!entry || typeof entry !== 'object') {
    report.unresolved.push({ name, reason: 'not a member of the package' });
    continue;
  }

  if (entry.kind === 'module') {
    /* A submodule published through __all__ is a page, not a symbol. */
    const target = moduleFor(`${PACKAGE}.${name}`);
    target.members = {};
    report.rendered.push({ name, home: target.path, kind: 'module' });
    continue;
  }

  const resolved = resolveAlias(entry.target_path ?? `${PACKAGE}.${name}`);
  if (!resolved) {
    report.unresolved.push({
      name,
      reason: `alias chain from ${entry.target_path} does not end at a definition`,
    });
    continue;
  }

  /* The public home is the one-hop target's module; a private one means the
   * package itself is the import path readers are given. */
  const oneHop = entry.target_path ?? resolved.path;
  const homePath = oneHop.split('.').slice(0, -1).join('.');
  const isPrivate = homePath
    .split('.')
    .slice(1)
    .some((part) => part.startsWith('_'));
  const home = moduleFor(isPrivate ? PACKAGE : homePath);

  home.members[name] = {
    ...resolved.node,
    name,
    path: `${home.path}.${name}`,
  };
  report.rendered.push({
    name,
    home: home.path,
    kind: resolved.node.kind,
    definedAt: resolved.path,
    rehomed: resolved.path !== `${home.path}.${name}`,
  });
}

/* The package page's re-export table is built from __all__ and the alias
 * target beside it, so both are rewritten to the scoped homes. */
const rendered = report.rendered.filter((item) => item.home !== PACKAGE);
pkgModule.members.__all__ = {
  kind: 'attribute',
  name: '__all__',
  path: `${PACKAGE}.__all__`,
  value: {
    cls: 'ExprList',
    elements: rendered.map((item) => JSON.stringify(item.name)),
  },
};
for (const item of rendered) {
  pkgModule.members[item.name] = {
    kind: 'alias',
    name: item.name,
    path: `${PACKAGE}.${item.name}`,
    target_path: `${item.home}.${item.name}`,
  };
}

/* Hang every module off its parent so collectModules walks one tree. */
for (const [path, module] of modules) {
  if (path === PACKAGE) continue;
  const parentPath = path.split('.').slice(0, -1).join('.');
  moduleFor(parentPath).members[module.name] = module;
}

report.modules = [...modules.keys()].sort();

await writeFile(
  join(root, 'spike/griffe.scoped.json'),
  `${JSON.stringify({ [PACKAGE]: pkgModule })}\n`,
  'utf8',
);
await writeFile(
  join(root, 'spike/scope-report.json'),
  `${JSON.stringify(report, null, 2)}\n`,
  'utf8',
);

console.log(
  `scope: ${inScope.length} in scope, ${report.rendered.length} placed, ${report.unresolved.length} unresolved, ${modules.size} module pages.`,
);
for (const item of report.unresolved) console.log(`  unresolved: ${item.name} (${item.reason})`);
