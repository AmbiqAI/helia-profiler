#!/usr/bin/env node
/*
 * Reads the built CLI, configuration and issue-code reference back and asserts
 * what it promised.
 *
 * The completeness target is the pair the agent-facing URLs serve: the JSON
 * artifact and the Markdown rendition the mapping layer writes beside it.
 * Starlight writes a `<route>/index.md` of its own next to every page, but it
 * reduces the reference parts to their prose and drops the parameter tables
 * (helia-ui#122), so asserting against it would either fail or lower the bar.
 *
 * Every assertion is anchored, not a substring search. `enabled` is a config
 * key on four different models and a word in a dozen sentences, so "the file
 * contains the string" is a test that passes on a page documenting none of
 * them: a command path has to be a heading, and a parameter has to be the name
 * cell of a row in its own section.
 *
 * Asserting the same names against the built HTML as well is what keeps the
 * two renditions honest. The component and the Markdown renderer share their
 * rows (src/lib/reference-cli.mjs); this is the check that they still do.
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';

import { BASE, PUBLIC_DIRS, SIDEBAR_FILE } from './build-cli-reference.mjs';
import {
  commandLabel,
  commandsOn,
  configurationEntries,
  fieldRows,
  panelsOf,
  paramName,
} from '../src/lib/reference-cli.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');
const dist = path.join(site, 'dist');
const HTML_BUDGET = 250_000;
const GZIP_BUDGET = 40_000;

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
  return condition;
};
const read = (...segments) => fs.readFileSync(path.join(...segments), 'utf8');
const readJson = (...segments) => JSON.parse(read(...segments));

/** The served directory of an artifact kind, inside dist. */
const served = (kind) => path.join(dist, PUBLIC_DIRS[kind].slice('public/'.length));

if (!fs.existsSync(path.join(served('cli'), 'cli.json'))) {
  throw new Error(`No CLI reference at ${served('cli')}. Run npm run build first.`);
}

const cli = readJson(served('cli'), 'cli.json');
const schema = readJson(served('configuration'), 'schema.json');
const issues = readJson(served('issues'), 'issues.json');
const sidebar = readJson(site, SIDEBAR_FILE);

const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const hasHeading = (text, title) => new RegExp(`^#{1,6} ${escape(title)}$`, 'm').test(text);

/** The section a heading opens, up to the next heading of the same rank. */
function sectionOf(text, title, level = 2) {
  const start = new RegExp(`^#{${level}} ${escape(title)}$`, 'm').exec(text);
  if (!start) return '';
  const rest = text.slice(start.index + start[0].length);
  const end = new RegExp(`^#{1,${level}} `, 'm').exec(rest);
  return end ? rest.slice(0, end.index) : rest;
}

/** A table row whose name cell opens with this exact declaration. */
const hasRow = (text, name) =>
  new RegExp(`^\\|\\s*\`${escape(name)}[,\`]`, 'm').test(text);

/* --- pages ------------------------------------------------------------ */

const pages = [
  ...cli.root.commands.map((node) => ({
    route: `${BASE}reference/cli/${node.name}/`,
    file: path.join(dist, 'reference/cli', node.name, 'index.html'),
    markdown: path.join(served('cli'), `${node.name}.md`),
    artifacts: [
      [`${BASE}reference/cli/cli.json`, path.join(served('cli'), 'cli.json')],
      [`${BASE}reference/cli/${node.name}.md`, path.join(served('cli'), `${node.name}.md`)],
    ],
    node,
  })),
  {
    route: `${BASE}reference/configuration/`,
    file: path.join(dist, 'reference/configuration/index.html'),
    markdown: path.join(served('configuration'), 'configuration.md'),
    artifacts: [
      [
        `${BASE}reference/configuration/schema.json`,
        path.join(served('configuration'), 'schema.json'),
      ],
      [
        `${BASE}reference/configuration/configuration.md`,
        path.join(served('configuration'), 'configuration.md'),
      ],
    ],
  },
  {
    route: `${BASE}reference/issue-codes/`,
    file: path.join(dist, 'reference/issue-codes/index.html'),
    markdown: path.join(served('issues'), 'issue-codes.md'),
    artifacts: [
      [`${BASE}reference/issue-codes/issues.json`, path.join(served('issues'), 'issues.json')],
      [
        `${BASE}reference/issue-codes/issue-codes.md`,
        path.join(served('issues'), 'issue-codes.md'),
      ],
    ],
  },
];

let largest = { route: '', html: 0, gzip: 0 };
const html = new Map();

for (const page of pages) {
  if (!check(fs.existsSync(page.file), `${page.route}: no page in the artifact.`)) continue;
  const body = read(page.file);
  html.set(page.route, body);
  const bytes = Buffer.byteLength(body);
  const gzip = gzipSync(body).length;
  check(
    bytes <= HTML_BUDGET && gzip <= GZIP_BUDGET,
    `${page.route} is over budget: ${bytes} B HTML, ${gzip} B gzip.`,
  );
  if (bytes > largest.html) largest = { route: page.route, html: bytes, gzip };

  const description = /<meta[^>]*\bname=["']description["'][^>]*\bcontent=["']([^"']*)["']/i.exec(
    body,
  )?.[1];
  check(
    Boolean(description?.trim()),
    `${page.route}: empty description frontmatter. helia-ui's discoverability integration ` +
      'fails the build on one.',
  );

  for (const [url, file] of page.artifacts) {
    check(body.includes(`href="${url}"`), `${page.route}: does not link its artifact at ${url}.`);
    check(fs.existsSync(file), `${url} is not in the artifact.`);
  }
}

/* --- completeness: commands ------------------------------------------- */

let commandAssertions = 0;
let paramAssertions = 0;
for (const page of pages.filter((entry) => entry.node)) {
  if (!fs.existsSync(page.markdown)) continue;
  const markdown = read(page.markdown);
  const body = html.get(page.route) ?? '';
  for (const command of commandsOn(page.node)) {
    const label = commandLabel(command.path);
    check(hasHeading(markdown, label), `${label}: no section heading in ${page.markdown}.`);
    check(body.includes(`id="${label}"`), `${label}: no anchor on ${page.route}.`);
    check(body.includes(command.usage), `${label}: usage line missing from ${page.route}.`);
    commandAssertions += 1;

    const section = sectionOf(markdown, label);
    const params = [...command.arguments, ...panelsOf(command).flatMap(([, list]) => list)];
    for (const param of params) {
      check(
        hasRow(section, param.declaration),
        `${label}: ${param.declaration} is not a row in its section of ${page.markdown}.`,
      );
      check(
        body.includes(`<code>${paramName(param)}</code>`),
        `${label}: ${param.declaration} is not a parameter row on ${page.route}.`,
      );
      paramAssertions += 1;
    }
    if (command.epilog) {
      check(
        markdown.includes(command.epilog.replace(/\s+$/, '')),
        `${label}: the epilog is not reproduced verbatim in ${page.markdown}.`,
      );
    }
  }
}
check(
  commandAssertions === cli.counts.leaf_commands + cli.counts.groups - 1,
  `Asserted ${commandAssertions} command paths, cli.json declares ` +
    `${cli.counts.leaf_commands + cli.counts.groups - 1} below the root.`,
);
check(
  paramAssertions === cli.counts.options + cli.counts.arguments - cli.root.options.length,
  `Asserted ${paramAssertions} parameters, cli.json declares ` +
    `${cli.counts.options + cli.counts.arguments} including the root's own.`,
);

/* --- completeness: configuration and issue codes ---------------------- */

const configMarkdown = read(path.join(served('configuration'), 'configuration.md'));
const configHtml = html.get(`${BASE}reference/configuration/`) ?? '';
let fieldAssertions = 0;
for (const entry of configurationEntries(schema)) {
  check(hasHeading(configMarkdown, entry.cls), `${entry.cls}: no section in configuration.md.`);
  const section = sectionOf(configMarkdown, entry.cls);
  for (const row of fieldRows(schema, entry.cls)) {
    check(hasRow(section, row.name), `${entry.cls}.${row.name}: not a row in configuration.md.`);
    check(
      configHtml.includes(`<code>${row.name}</code>`),
      `${entry.cls}.${row.name}: not a key row on the configuration page.`,
    );
    fieldAssertions += 1;
  }
}
check(
  fieldAssertions === schema.counts.declaredFields,
  `Asserted ${fieldAssertions} config fields, schema.json declares ` +
    `${schema.counts.declaredFields}.`,
);

const issuesMarkdown = read(path.join(served('issues'), 'issue-codes.md'));
const issuesHtml = html.get(`${BASE}reference/issue-codes/`) ?? '';
const allCodes = [
  ...issues.issues.map((issue) => issue.code),
  ...issues.comparability.map((issue) => issue.code),
  ...issues.families.flatMap((family) => family.codes),
];
for (const code of allCodes) {
  check(hasRow(issuesMarkdown, code), `${code}: not a row in issue-codes.md.`);
  check(issuesHtml.includes(`<code>${code}</code>`), `${code}: not a row on the issue-code page.`);
}
check(
  allCodes.length ===
    issues.counts.issues + issues.counts.comparability + issues.counts.familyCodes,
  `Asserted ${allCodes.length} issue codes against a declared count of ` +
    `${issues.counts.issues + issues.counts.comparability + issues.counts.familyCodes}.`,
);

/* --- provenance and nav ----------------------------------------------- */

const sourceTree = execFileSync('git', ['rev-parse', 'HEAD:src/helia_profiler'], {
  cwd: repo,
  encoding: 'utf8',
}).trim();
for (const [name, artifact] of [
  ['cli.json', cli],
  ['schema.json', schema],
  ['issues.json', issues],
]) {
  check(
    artifact.generatedFrom?.sourceTree === sourceTree,
    `${name} was generated from tree ${artifact.generatedFrom?.sourceTree}, ` +
      `src/helia_profiler is tree ${sourceTree}.`,
  );
  check(
    !('sourceCommit' in (artifact.generatedFrom ?? {})),
    `${name} records a source commit, which does not survive a squash merge.`,
  );
  for (const tool of ['typer', 'click', 'pydantic']) {
    check(
      Boolean(artifact.generatedFrom?.[tool]),
      `${name} records no resolved ${tool} version.`,
    );
  }
}

/* helia-ui's discoverability integration files a page the sidebar never names
 * under "## Other pages" in llms.txt, which is the signal that a route is
 * built but unreachable. */
const llmsIndex = read(dist, 'llms.txt');
const otherPages = llmsIndex.split(/^## Other pages$/m)[1] ?? '';
const sidebarSlugs = [];
const collect = (entry) => {
  if (entry.slug) sidebarSlugs.push(`${BASE}${entry.slug}/`);
  if (entry.link) sidebarSlugs.push(entry.link);
  for (const child of entry.items ?? []) collect(child);
};
for (const entry of sidebar) collect(entry);
for (const page of pages) {
  check(sidebarSlugs.includes(page.route), `${page.route}: no Reference sidebar entry claims it.`);
  check(
    !otherPages.includes(page.route),
    `${page.route} is under "## Other pages" in llms.txt, so the sidebar never names it.`,
  );
}

if (failures.length > 0) {
  console.error('CLI reference assertions failed:\n');
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `CLI reference verified: ${pages.length} pages; ${commandAssertions} command headings and ` +
    `${paramAssertions} parameters against cli.json and the Markdown renditions; ` +
    `${fieldAssertions} config fields across ${schema.counts.classes} models; ` +
    `${allCodes.length} issue codes; largest page ${largest.route} ${largest.html} B HTML, ` +
    `${largest.gzip} B gzip; typer ${cli.generatedFrom.typer}, click ${cli.generatedFrom.click}, ` +
    `pydantic ${schema.generatedFrom.pydantic}; source tree ${sourceTree.slice(0, 7)}.`,
);
