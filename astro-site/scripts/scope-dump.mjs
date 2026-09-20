#!/usr/bin/env node
/*
 * Narrow a griffe dump of helia_profiler to the published API surface and lay
 * it out as the pages the site serves.
 *
 * Two problems make this a pass of its own rather than a pyref flag.
 *
 * Scope: pyref's `--filter` applies its name regexes to every node, so a
 * keep-list of the 85 published names would also delete every method, field
 * and enum member below them. The tier map decides membership here instead,
 * before pyref sees the tree: stable and experimental stay, implementation is
 * dropped from the tree and therefore from the nav, the pages, reference.json
 * and both llms exports.
 *
 * Shape: pyref renders one page per module, and a raw dump of this package is
 * 187 modules, one of which (`helia_profiler.results`) renders 447,982 B of
 * HTML on its own -- 1.8x the page budget. The grouping manifest replaces the
 * module tree with curated pages. A page's module path is its route; its
 * module name is its title.
 *
 * Every name in scope is published from the package root, so a symbol's id is
 * its canonical import path (`helia_profiler.ProfileConfig`) whatever page it
 * is grouped onto. That is the string a reader types and an agent greps for,
 * and it keeps the ids stable if the grouping is revised.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

export const PACKAGE = 'helia_profiler';
export const KEPT_TIERS = new Set(['stable', 'experimental']);

const TIER_LABEL = { stable: 'Stable', experimental: 'Experimental' };

/** The tier badge carried on every symbol and every page. */
export const tierNote = (tiers) => {
  const shown = [...new Set(tiers)].filter(Boolean);
  if (shown.length === 0) return '';
  return `**API tier:** ${shown.map((tier) => `\`${tier}\``).join(', ')}`;
};

/**
 * The description frontmatter helia-ui derives from a module page comes from
 * the first sentence of its docstring, and its discoverability integration
 * fails the whole build, late, on a page without one. A curated page has no
 * docstring of its own, so the manifest supplies the summary and this is the
 * backstop for a group added without one.
 */
export const moduleName = (group) => group.path.split('.').at(-1);

export const groupSummary = (group, sourceModule) =>
  group.summary?.trim() ||
  sourceModule?.docstring?.parsed?.find((section) => section.kind === 'text')?.value?.trim() ||
  `Published ${PACKAGE} API: ${moduleName(group)}.`;

/**
 * Re-home a symbol and everything under it at its canonical import path.
 *
 * griffe records a member at the path it is defined at, three packages deep
 * in places. The name a reader imports is `helia_profiler.<Name>`, so the
 * whole subtree is renumbered from there and the ids in the page, the model
 * and the Markdown all read as the import does. The `source` field still
 * carries the real file and line.
 */
export function rehome(node, canonical, definedAt) {
  const path = node.path === definedAt ? canonical : node.path?.startsWith(`${definedAt}.`)
    ? `${canonical}${node.path.slice(definedAt.length)}`
    : node.path;
  const members = node.members;
  if (!members || typeof members !== 'object') return { ...node, path };
  return {
    ...node,
    path,
    members: Object.fromEntries(
      Object.entries(members).map(([name, member]) => [
        name,
        member && typeof member === 'object' ? rehome(member, canonical, definedAt) : member,
      ]),
    ),
  };
}

/** A docstring with `note` appended as a trailing paragraph. */
export const withNote = (docstring, note) => {
  if (!note) return docstring;
  const parsed = Array.isArray(docstring?.parsed) ? [...docstring.parsed] : [];
  /* Appended, never prepended: helia-ui takes the page description and the
   * member-table summary from the first text section. */
  parsed.push({ kind: 'text', value: note });
  return { ...(docstring ?? {}), parsed };
};

/**
 * The provenance line every page carries.
 *
 * It names the git tree of `src/helia_profiler`, not a commit and not a ref.
 * A commit sha would rewrite every generated file on any change under src/,
 * and would not survive the squash merges this repository uses; the tree sha
 * is the identity of the source that was read, and two commits carrying the
 * same source produce the same bytes here.
 */
export const provenanceNote = (tree) =>
  tree ? `Generated from the \`src/helia_profiler\` tree \`${tree}\`.` : '';

export function scope({ dump, tiers, groups: manifest, tree = '' }) {
  const pkg = dump[PACKAGE];
  if (!pkg) throw new Error(`The dump has no package "${PACKAGE}".`);

  const report = { pages: [], symbols: [], tiers: {}, problems: [] };
  const fail = (message) => report.problems.push(message);

  const inScope = tiers.all.filter((name) => KEPT_TIERS.has(tiers.stability[name]));

  /* The node at a dotted path in the source dump, or undefined. */
  const nodeAt = (dotted) => {
    const parts = dotted.split('.');
    if (parts.shift() !== PACKAGE) return undefined;
    let node = pkg;
    for (const part of parts) {
      node = node?.members?.[part];
      if (!node || typeof node !== 'object') return undefined;
    }
    return node;
  };

  /* Follow target_path until a node that is not an alias. griffe records one
   * hop per re-export, and this package re-exports through two or three. */
  const resolveAlias = (dotted, seen = new Set()) => {
    if (seen.has(dotted)) return undefined;
    seen.add(dotted);
    const node = nodeAt(dotted);
    if (!node) return undefined;
    if (node.kind !== 'alias') return { path: dotted, node };
    if (typeof node.target_path !== 'string') return undefined;
    return resolveAlias(node.target_path, seen);
  };

  const pages = new Map();
  for (const group of manifest.groups) {
    if (pages.has(group.path)) fail(`${group.path}: two groups claim this page.`);
    const source = nodeAt(group.path);
    const summary = groupSummary(group, source);
    pages.set(group.path, {
      group,
      node: {
        kind: 'module',
        name: moduleName(group),
        path: group.path,
        labels: [],
        ...(source?.filepath ? { filepath: source.filepath } : {}),
        docstring: { parsed: [{ kind: 'text', value: summary }] },
        members: {},
      },
    });
  }

  /* A module published through __all__ is a page, not a symbol: `examples` is
   * `from helia_profiler import examples`, and its contents are not tiered.
   * The manifest claims it by naming the page with the module's own path. */
  const claimedModules = new Set(
    manifest.groups
      .filter((group) => group.kind === 'module')
      .map((group) => group.path.split('.').at(-1)),
  );

  const claimed = new Map();
  for (const group of manifest.groups) {
    for (const name of group.members) {
      if (claimed.has(name)) {
        fail(`${name}: claimed by both ${claimed.get(name)} and ${group.path}.`);
      }
      claimed.set(name, group.path);
    }
  }

  for (const name of inScope) {
    if (claimedModules.has(name)) continue;
    if (!claimed.has(name)) fail(`${name}: in scope but on no page of the grouping manifest.`);
  }
  for (const [name, page] of claimed) {
    if (!KEPT_TIERS.has(tiers.stability[name])) {
      fail(`${name}: grouped onto ${page} but its tier is ${tiers.stability[name] ?? 'none'}.`);
    }
  }

  for (const group of manifest.groups) {
    const page = pages.get(group.path);
    const own = tiers.stability[moduleName(group)];
    const pageTiers = group.kind === 'module' && KEPT_TIERS.has(own) ? [own] : [];
    for (const name of group.members) {
      const entry = pkg.members?.[name];
      if (!entry || typeof entry !== 'object') {
        fail(`${name}: not a member of ${PACKAGE}.`);
        continue;
      }
      const resolved = resolveAlias(entry.target_path ?? `${PACKAGE}.${name}`);
      if (!resolved) {
        fail(`${name}: the alias chain from ${entry.target_path} ends at no definition.`);
        continue;
      }
      const tier = tiers.stability[name];
      pageTiers.push(TIER_LABEL[tier]?.toLowerCase() ?? tier);
      page.node.members[name] = {
        ...rehome(resolved.node, `${PACKAGE}.${name}`, resolved.node.path ?? resolved.path),
        name,
        docstring: withNote(resolved.node.docstring, tierNote([tier])),
      };
      report.symbols.push({
        name,
        tier,
        page: group.path,
        kind: resolved.node.kind,
        definedAt: resolved.path,
      });
    }

    /* The page's own badge: the tiers a reader will meet on it. */
    const note = [
      `Every name on this page is imported from \`${manifest.importFrom}\`.`,
      tierNote(pageTiers),
      provenanceNote(tree),
    ]
      .filter(Boolean)
      .join('\n\n');
    page.node.docstring = withNote(page.node.docstring, note);
    report.pages.push({
      path: group.path,
      symbols: group.members.length,
      tiers: [...new Set(pageTiers)].sort(),
    });
  }

  /* The package page's re-export table: __all__ plus one alias per name, both
   * rewritten to the scoped ids so every entry links to the page that
   * documents it. */
  const root = pages.get(PACKAGE);
  if (!root) fail(`The grouping manifest has no page for ${PACKAGE}.`);
  if (root) {
    const elsewhere = report.symbols.filter((symbol) => symbol.page !== PACKAGE);
    for (const symbol of elsewhere) {
      root.node.members[symbol.name] = {
        kind: 'alias',
        name: symbol.name,
        path: `${PACKAGE}.${symbol.name}`,
        target_path: `${PACKAGE}.${symbol.name}`,
      };
    }
    root.node.members.__all__ = {
      kind: 'attribute',
      name: '__all__',
      path: `${PACKAGE}.__all__`,
      value: {
        cls: 'ExprList',
        elements: [...report.symbols.map((symbol) => symbol.name), ...claimedModules].map((name) =>
          JSON.stringify(name),
        ),
      },
    };
  }

  /* Hang every page off its parent so pyref walks one tree. */
  for (const [dotted, page] of pages) {
    if (dotted === PACKAGE) continue;
    const parentPath = dotted.split('.').slice(0, -1).join('.');
    const parent = pages.get(parentPath);
    if (!parent) {
      fail(`${dotted}: its parent page ${parentPath} is not in the grouping manifest.`);
      continue;
    }
    parent.node.members[page.node.name] = page.node;
  }

  for (const symbol of report.symbols) {
    report.tiers[symbol.tier] = (report.tiers[symbol.tier] ?? 0) + 1;
  }
  report.counts = {
    inScope: inScope.length,
    symbols: report.symbols.length,
    modulePages: claimedModules.size,
    pages: report.pages.length,
  };

  return { scoped: { [PACKAGE]: root?.node }, report };
}

/* Run as a script: read the dump produced by dump-python.mjs, write the
 * scoped tree pyref renders. */
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const generated = path.join(site, '.generated');
  const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

  const treeFlag = process.argv.indexOf('--tree');
  const { scoped, report } = scope({
    dump: readJson(path.join(generated, 'griffe.json')),
    tiers: readJson(path.join(generated, 'api-tiers.json')),
    groups: readJson(path.join(site, 'src/data/api-groups.json')),
    tree: treeFlag === -1 ? '' : process.argv[treeFlag + 1],
  });

  fs.writeFileSync(path.join(generated, 'griffe.scoped.json'), `${JSON.stringify(scoped)}\n`);
  fs.writeFileSync(
    path.join(generated, 'scope-report.json'),
    `${JSON.stringify(report, null, 2)}\n`,
  );

  if (report.problems.length > 0) {
    console.error('The grouping manifest does not match the published API:\n');
    for (const problem of report.problems) console.error(`- ${problem}`);
    process.exit(1);
  }

  console.log(
    `scope: ${report.counts.inScope} names in scope, ${report.counts.symbols} symbols and ` +
      `${report.counts.modulePages} module page${report.counts.modulePages === 1 ? '' : 's'} ` +
      `across ${report.counts.pages} pages (${JSON.stringify(report.tiers)}).`,
  );
}
