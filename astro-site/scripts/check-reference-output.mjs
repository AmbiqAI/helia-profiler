/*
 * Reads the built Python reference back and asserts what it promised.
 *
 * The target of the completeness assertions is pyref's own artifact set under
 * dist/reference/api/: reference.json, llms-full.txt and the per-module
 * Markdown beside each module's JSON. Starlight's own `<route>/index.md` and
 * the site-level llms-full.txt are deliberately not checked for symbols: the
 * discoverability integration reduces the reference components to their prose
 * and drops signatures, parameter tables and enum members (helia-ui#122), so
 * asserting against them would either fail or lower the bar.
 *
 * check-output.mjs owns the site-wide contracts. This file owns the reference.
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');
const dist = path.join(site, 'dist');
const base = '/helia-profiler/';
const ROUTE_PREFIX = 'reference/api';
const HTML_BUDGET = 250_000;
const GZIP_BUDGET = 40_000;

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
  return condition;
};
const read = (...segments) => fs.readFileSync(path.join(...segments), 'utf8');
const readJson = (...segments) => JSON.parse(read(...segments));

const artifacts = path.join(dist, ROUTE_PREFIX);
if (!fs.existsSync(path.join(artifacts, 'reference.json'))) {
  throw new Error(`No Python reference at ${artifacts}. Run npm run build first.`);
}

const model = readJson(artifacts, 'reference.json');
const llmsFull = read(artifacts, 'llms-full.txt');
const buildInfo = readJson(dist, 'build-info.json');
const sidebar = readJson(site, 'src/generated/api-sidebar.json');

/* Ground truth for scope and tier is the package, not this file. */
const tiersFile = path.join(site, '.generated/api-tiers.json');
if (!fs.existsSync(tiersFile)) {
  throw new Error(`No ${tiersFile}. Run npm run reference:dump first.`);
}
const tiers = readJson(tiersFile);
const tierOf = (name) => tiers.stability[name];
const inScope = tiers.all.filter((name) => ['stable', 'experimental'].includes(tierOf(name)));
const implementation = tiers.all.filter((name) => tierOf(name) === 'implementation');

/* Every module of the model, and the artifacts it was written to. Routes are
 * read back from pyref's own per-module JSON rather than re-derived from the
 * module path, which would be a second copy of its slug rule. */
const modules = [];
const visit = (module) => {
  modules.push(module);
  for (const child of module.submodules ?? []) visit(child);
};
for (const module of model.modules ?? []) visit(module);

const artifactOf = new Map();
const walk = (directory) => {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      walk(entryPath);
    } else if (entry.name.endsWith('.json') && entryPath !== path.join(artifacts, 'reference.json')) {
      const dotted = readJson(entryPath).modules?.[0]?.path;
      if (dotted) artifactOf.set(dotted, entryPath.slice(0, -'.json'.length));
    }
  }
};
walk(artifacts);

const pages = modules.map((module) => {
  const stem = artifactOf.get(module.path);
  const route = stem ? `${base}${path.relative(dist, stem)}/` : null;
  return { module, stem, route };
});
for (const page of pages) {
  check(page.stem !== null, `${page.module.path}: pyref wrote no JSON artifact for it.`);
}

/* Scope: the 85 published names, each rendered once, none of the 13
 * implementation names anywhere structural. */
const symbols = [];
const flatten = (symbol, into) => {
  into.push(symbol);
  for (const member of symbol.members ?? []) flatten(member, into);
};
for (const page of pages) {
  for (const symbol of page.module.symbols ?? []) symbols.push({ ...symbol, page });
}

const seen = new Map();
for (const symbol of symbols) {
  if (seen.has(symbol.name)) {
    failures.push(`${symbol.name}: rendered on ${seen.get(symbol.name)} and ${symbol.page.module.path}.`);
  }
  seen.set(symbol.name, symbol.page.module.path);
  check(
    ['stable', 'experimental'].includes(tierOf(symbol.name)),
    `${symbol.name}: rendered but its tier is ${tierOf(symbol.name) ?? 'none'}.`,
  );
  check(
    (symbol.description ?? '').includes(`**API tier:** \`${tierOf(symbol.name)}\``),
    `${symbol.name}: no ${tierOf(symbol.name)} tier badge on the rendered symbol.`,
  );
}

/* A submodule that __all__ publishes is a page, not a symbol: `examples` is
 * `from helia_profiler import examples` and its contents are not tiered. */
const modulePages = pages
  .map((page) => page.module.path.split('.').at(-1))
  .filter((name) => inScope.includes(name));
const missing = inScope.filter((name) => !seen.has(name) && !modulePages.includes(name));
check(missing.length === 0, `In scope but not rendered: ${missing.join(', ')}.`);
check(
  symbols.length + modulePages.length === inScope.length,
  `Rendered ${symbols.length} symbols and ${modulePages.length} module pages for ${inScope.length} names in scope.`,
);

const allMembers = [];
for (const symbol of symbols) flatten(symbol, allMembers);
const sidebarLabels = [];
const walkSidebar = (entry) => {
  if (entry.label) sidebarLabels.push(entry.label);
  for (const child of entry.items ?? []) walkSidebar(child);
};
walkSidebar(sidebar);
const llmsHeadings = [...llmsFull.matchAll(/^#+ (\S+)$/gm)].map((match) => match[1]);

for (const name of implementation) {
  check(!seen.has(name), `${name} is an implementation name but is a documented symbol.`);
  check(
    !allMembers.some((member) => member.name === name),
    `${name} is an implementation name but is a rendered member.`,
  );
  check(
    !sidebarLabels.includes(name),
    `${name} is an implementation name but is a sidebar entry.`,
  );
  check(
    !llmsHeadings.some((heading) => heading === name || heading.endsWith(`.${name}`)),
    `${name} is an implementation name but has a section in llms-full.txt.`,
  );
}

/* Completeness against the agent-facing artifacts. */
const declaredNames = (symbol) => [
  ...(symbol.params ?? []).map((param) => param.name),
  ...(symbol.members ?? []).map((member) => member.name),
];
const perModule = new Map(pages.map((page) => [page.module.path, page.stem ? read(`${page.stem}.md`) : '']));

for (const symbol of symbols) {
  const markdown = perModule.get(symbol.page.module.path) ?? '';
  for (const [label, text] of [
    ['llms-full.txt', llmsFull],
    [`${path.relative(dist, symbol.page.stem ?? '')}.md`, markdown],
  ]) {
    check(text.includes(`# ${symbol.id}`), `${symbol.id}: no section in ${label}.`);
    check(
      Boolean(symbol.signature) && text.includes(symbol.signature),
      `${symbol.id}: signature missing from ${label}.`,
    );
    for (const member of declaredNames(symbol)) {
      check(
        text.includes(member),
        `${symbol.id}: declared name "${member}" missing from ${label}.`,
      );
    }
  }
}

/* Page budget, description frontmatter and provenance. */
const sourceCommit = execFileSync(
  'git',
  ['log', '-1', '--format=%H', '--', 'src/helia_profiler'],
  { cwd: repo, encoding: 'utf8' },
).trim();
check(
  model.generatedFrom?.sourceCommit === sourceCommit,
  `reference.json was generated from ${model.generatedFrom?.sourceCommit}, src/helia_profiler is at ${sourceCommit}.`,
);
check(
  /^[0-9a-f]{40}$/.test(buildInfo.commit ?? ''),
  `build-info.json commit is unusable: ${buildInfo.commit}`,
);

let largest = { route: '', html: 0, gzip: 0 };
for (const page of pages) {
  if (!page.route) continue;
  const file = path.join(dist, page.route.slice(base.length), 'index.html');
  if (!check(fs.existsSync(file), `${page.route}: no page in the artifact.`)) continue;
  const html = read(file);
  const bytes = Buffer.byteLength(html);
  const gzip = gzipSync(html).length;
  check(
    bytes <= HTML_BUDGET && gzip <= GZIP_BUDGET,
    `${page.route} is over budget: ${bytes} B HTML, ${gzip} B gzip.`,
  );
  if (bytes > largest.html) largest = { route: page.route, html: bytes, gzip };

  const description = /<meta[^>]*\bname=["']description["'][^>]*\bcontent=["']([^"']*)["']/i.exec(
    html,
  )?.[1];
  check(
    Boolean(description?.trim()),
    `${page.route}: empty description frontmatter. helia-ui's discoverability integration fails the build on one.`,
  );
  check(html.includes(sourceCommit), `${page.route}: does not record the source commit.`);
}

/* Nav: helia-ui's discoverability integration files a page the sidebar never
 * names under "## Other pages" in llms.txt, which is the signal that a route
 * is built but unreachable. */
const llmsIndex = read(dist, 'llms.txt');
const otherPages = llmsIndex.split(/^## Other pages$/m)[1] ?? '';
const sidebarSlugs = [];
const collectSlugs = (entry) => {
  if (entry.slug) sidebarSlugs.push(`${base}${entry.slug}/`);
  if (entry.link) sidebarSlugs.push(entry.link);
  for (const child of entry.items ?? []) collectSlugs(child);
};
collectSlugs(sidebar);
for (const page of pages) {
  if (!page.route) continue;
  check(sidebarSlugs.includes(page.route), `${page.route}: no Reference sidebar entry claims it.`);
  check(
    !otherPages.includes(page.route),
    `${page.route} is under "## Other pages" in llms.txt, so the sidebar never names it.`,
  );
}
if (failures.length > 0) {
  console.error('Python reference assertions failed:\n');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

const badged = {};
for (const symbol of symbols) badged[tierOf(symbol.name)] = (badged[tierOf(symbol.name)] ?? 0) + 1;
console.log(
  `Python reference verified: ${inScope.length} published names as ${symbols.length} symbols ` +
    `(${JSON.stringify(badged)}) and ${modulePages.length} module page across ${pages.length} pages; ` +
    `${implementation.length} implementation names absent; largest page ${largest.route} ` +
    `${largest.html} B HTML, ${largest.gzip} B gzip; source commit ${sourceCommit.slice(0, 7)}.`,
);
