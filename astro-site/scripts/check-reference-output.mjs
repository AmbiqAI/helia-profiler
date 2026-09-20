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

import { SOURCE_REF_TOKEN } from '../src/integrations/source-ref.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');
const dist = path.join(site, 'dist');
const base = '/helia-profiler/';
const origin = 'https://ambiqai.github.io';
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
const exists = (...segments) => fs.existsSync(path.join(...segments));

const TEXT = new Set(['.html', '.json', '.md', '.txt', '.xml']);
const walkFiles = (directory) =>
  fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) return walkFiles(entryPath);
    return TEXT.has(path.extname(entry.name)) ? [entryPath] : [];
  });

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

/* Completeness against the agent-facing artifacts.
 *
 * Members are asserted as their own heading, not as a substring: "enabled"
 * occurs in four unrelated places in llms-full.txt, so a substring test
 * passes on a page that documents none of them. Parameters are asserted
 * inside the symbol's own section, since the model only carries a parameter
 * when the docstring documents it and the signature carries the rest. */
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const heading = (text, id, level) =>
  new RegExp(`^#{${level},6} ${escape(id)}$`, 'm').test(text);
const sectionOf = (text, id) => {
  const start = new RegExp(`^## ${escape(id)}$`, 'm').exec(text);
  if (!start) return '';
  const rest = text.slice(start.index + start[0].length);
  const end = /^## /m.exec(rest);
  return end ? rest.slice(0, end.index) : rest;
};

const perModule = new Map(
  pages.map((page) => [page.module.path, page.stem ? read(`${page.stem}.md`) : '']),
);

let memberAssertions = 0;
for (const symbol of symbols) {
  const markdown = perModule.get(symbol.page.module.path) ?? '';
  const members = [];
  for (const member of symbol.members ?? []) flatten(member, members);

  for (const [label, text] of [
    ['llms-full.txt', llmsFull],
    [`${path.relative(dist, symbol.page.stem ?? '')}.md`, markdown],
  ]) {
    check(heading(text, symbol.id, 2), `${symbol.id}: no section heading in ${label}.`);
    check(
      Boolean(symbol.signature) && text.includes(symbol.signature),
      `${symbol.id}: signature missing from ${label}.`,
    );
    for (const member of members) {
      check(heading(text, member.id, 3), `${member.id}: no member heading in ${label}.`);
      memberAssertions += 1;
    }
    const section = sectionOf(text, symbol.id);
    for (const param of symbol.params ?? []) {
      check(
        section.includes(param.name),
        `${symbol.id}: parameter "${param.name}" missing from its section in ${label}.`,
      );
    }
  }
}

/* Page budget, description frontmatter and provenance. */
const sourceTree = execFileSync('git', ['rev-parse', 'HEAD:src/helia_profiler'], {
  cwd: repo,
  encoding: 'utf8',
}).trim();
check(
  model.generatedFrom?.sourceTree === sourceTree,
  `reference.json was generated from tree ${model.generatedFrom?.sourceTree}, src/helia_profiler is tree ${sourceTree}.`,
);
check(
  !('sourceCommit' in (model.generatedFrom ?? {})),
  'reference.json records a source commit, which does not survive a squash merge.',
);
check(
  /^[0-9a-f]{40}$/.test(buildInfo.commit ?? ''),
  `build-info.json commit is unusable: ${buildInfo.commit}`,
);

/* The ref is resolved at build time, so no generated file may still carry the
 * placeholder and every source link has to name the ref this build is of. */
const expectedRef = buildInfo.releaseTag || 'main';
const stillTokenised = walkFiles(dist).filter((file) =>
  read(file).includes(SOURCE_REF_TOKEN),
);
check(
  stillTokenised.length === 0,
  `${stillTokenised.length} built files still carry ${SOURCE_REF_TOKEN}, starting with ` +
    `${stillTokenised[0] && path.relative(dist, stillTokenised[0])}.`,
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
  check(html.includes(sourceTree), `${page.route}: does not record the source tree.`);
  check(
    !/blob\/[0-9a-f]{40}\//.test(html),
    `${page.route}: a source link names a commit sha rather than the build's ref.`,
  );
  const links = [...html.matchAll(/blob\/([^/"]+)\//g)].map((match) => match[1]);
  check(
    links.every((ref) => ref === expectedRef),
    `${page.route}: source links name ${[...new Set(links)].join(', ')}, expected ${expectedRef}.`,
  );

  /* The complete Markdown is the artifact an agent wants and nothing else on
   * the page points at it: the rendition Starlight writes beside the page is
   * the lossy one (helia-ui#122). */
  for (const extension of ['json', 'md']) {
    const artifact = `${base}${path.relative(dist, page.stem)}.${extension}`;
    check(
      html.includes(`href="${artifact}"`),
      `${page.route}: does not link its ${extension} artifact at ${artifact}.`,
    );
    check(exists(dist, artifact.slice(base.length)), `${artifact} is not in the artifact.`);
  }
}

/* pyref's own index advertises each module's JSON; the Markdown beside it is
 * the complete rendition and has to be advertised with it. */
const referenceIndex = read(artifacts, 'llms.txt');
for (const page of pages) {
  if (!page.stem) continue;
  const url = `${origin}${base}${path.relative(dist, page.stem)}.md`;
  check(
    referenceIndex.includes(url),
    `${page.module.path}: reference/api/llms.txt does not advertise ${url}.`,
  );
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
    `${implementation.length} implementation names absent; ${memberAssertions} member headings; ` +
    `largest page ${largest.route} ${largest.html} B HTML, ${largest.gzip} B gzip; ` +
    `source tree ${sourceTree.slice(0, 7)} at ref ${expectedRef}.`,
);
