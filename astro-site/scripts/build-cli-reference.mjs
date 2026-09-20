#!/usr/bin/env node
/*
 * The CLI, configuration and issue-code reference: extract, then map.
 *
 * The extractors in tools/docs/ import the package and ask click and pydantic
 * what the contract is; they never read rendered `--help`, so nothing here
 * depends on terminal width, rich styling or locale. They live outside src/
 * because src/ is the wheel and the documented public API, and build tooling
 * is neither.
 *
 * This script owns everything downstream of the JSON: the command pages, the
 * configuration page, the issue-code page, the sidebar that claims them, and
 * the Markdown rendition of each page served beside its JSON. The pages are
 * thin on purpose - a page is frontmatter and one component - so a change to
 * how a command renders is one edit in src/components, not fifteen
 * regenerated files.
 *
 * `--out <dir>` writes the whole set somewhere else, which is how the stale
 * check regenerates without touching the working tree.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { sourceTree } from './build-reference.mjs';
import {
  commandDescription,
  commandLabel,
  commandSlug,
  configurationDescription,
  issuesDescription,
  renderCommandMarkdown,
  renderConfigurationMarkdown,
  renderIssuesMarkdown,
} from '../src/lib/reference-cli.mjs';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const BASE = '/helia-profiler/';
export const DATA_DIR = 'src/data';
export const PAGES_DIR = 'src/content/docs/reference';
export const SIDEBAR_FILE = 'src/generated/cli-sidebar.json';
export const COMPONENTS_DIR = 'src/components';

/** Where each artifact is served, and which page it belongs to. */
export const PUBLIC_DIRS = {
  cli: 'public/reference/cli',
  configuration: 'public/reference/configuration',
  issues: 'public/reference/issue-codes',
};

/** The page directories this script owns, under the shared Reference route. */
export const PAGE_DIRS = [
  `${PAGES_DIR}/cli`,
  `${PAGES_DIR}/configuration`,
  `${PAGES_DIR}/issue-codes`,
];

/** Every path this script writes, for the stale check to compare against git. */
export const GENERATED = [
  ...PAGE_DIRS,
  SIDEBAR_FILE,
  `${DATA_DIR}/cli.json`,
  `${DATA_DIR}/schema.json`,
  `${DATA_DIR}/issues.json`,
  ...Object.values(PUBLIC_DIRS),
];

const write = (file, body) => {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, body, 'utf8');
};

/** A YAML double-quoted scalar. JSON string escapes are a subset of them. */
const yaml = (value) => JSON.stringify(value);

/**
 * A page: frontmatter, the component that renders it, and a pointer to the
 * artifacts.
 *
 * The footer is not decoration. An agent that lands on the HTML has no way to
 * learn that a complete Markdown rendition exists, and the rendition Starlight
 * writes beside the page reduces the reference parts to their prose
 * (helia-ui#122), so it is the wrong one to find.
 */
function page({ pagePath, title, description, component, props, artifacts }) {
  const importPath = path.posix.relative(
    path.posix.dirname(pagePath.split(path.sep).join('/')),
    `${COMPONENTS_DIR}/${component}.astro`,
  );
  const attributes = Object.entries(props ?? {})
    .map(([name, value]) => ` ${name}=${yaml(value)}`)
    .join('');
  const links = artifacts
    .map(([label, url]) => `[\`${label}\`](${url})`)
    .join(' · ');
  return [
    '---',
    `title: ${yaml(title)}`,
    `description: ${yaml(description)}`,
    '---',
    '',
    `import ${component} from '${importPath}';`,
    '',
    `<${component}${attributes} />`,
    '',
    '---',
    '',
    `Machine-readable: ${links}, complete and lossless.`,
    '',
  ].join('\n');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const outRoot = process.argv.includes('--out')
    ? path.resolve(process.argv[process.argv.indexOf('--out') + 1])
    : site;

  const tree = sourceTree(repo, { allowDirty: Boolean(process.env.DOCS_ALLOW_DIRTY_SOURCE) });
  const dataDir = path.join(outRoot, DATA_DIR);

  /* --isolated --no-dev so the resolved typer, click and pydantic recorded in
   * the artifacts are the package's own pins and not whatever a developer
   * happens to have installed. */
  execFileSync(
    'uv',
    [
      'run',
      '--isolated',
      '--no-dev',
      'python',
      'tools/docs/check_reference.py',
      '--write',
      '--source-tree',
      tree,
      '--data-dir',
      dataDir,
    ],
    { cwd: repo, stdio: 'inherit' },
  );

  const readData = (name) => JSON.parse(fs.readFileSync(path.join(dataDir, name), 'utf8'));
  const cli = readData('cli.json');
  const schema = readData('schema.json');
  const issues = readData('issues.json');

  const url = (kind, file) => `${BASE}${PUBLIC_DIRS[kind].slice('public/'.length)}/${file}`;

  for (const [kind, name, body] of [
    ['cli', 'cli.json', `${JSON.stringify(cli, null, 2)}\n`],
    ['configuration', 'schema.json', `${JSON.stringify(schema, null, 2)}\n`],
    ['issues', 'issues.json', `${JSON.stringify(issues, null, 2)}\n`],
    ['configuration', 'configuration.md', renderConfigurationMarkdown(schema)],
    ['issues', 'issue-codes.md', renderIssuesMarkdown(issues)],
  ]) {
    write(path.join(outRoot, PUBLIC_DIRS[kind], name), body);
  }

  const entries = cli.root.commands;
  /* `hpx` itself is a page too. Its one global option lives nowhere else, and
   * a reference that documented every subcommand but not the program would
   * leave `--version` in the JSON and on no page. */
  for (const node of [cli.root, ...entries]) {
    const root = node.path.length === 0;
    write(
      path.join(outRoot, PUBLIC_DIRS.cli, `${node.name}.md`),
      renderCommandMarkdown(node, cli.generatedFrom),
    );
    const pagePath = root
      ? path.join(PAGES_DIR, 'cli', 'index.mdx')
      : path.join(PAGES_DIR, 'cli', node.name, 'index.mdx');
    write(
      path.join(outRoot, pagePath),
      page({
        pagePath,
        title: commandLabel(node.path),
        description: commandDescription(node),
        component: 'CliCommand',
        props: root ? {} : { command: node.name },
        artifacts: [
          ['cli.json', url('cli', 'cli.json')],
          [`${node.name}.md`, url('cli', `${node.name}.md`)],
        ],
      }),
    );
  }

  const configPage = path.join(PAGES_DIR, 'configuration', 'index.mdx');
  write(
    path.join(outRoot, configPage),
    page({
      pagePath: configPage,
      title: 'Configuration',
      description: configurationDescription(schema),
      component: 'ConfigurationReference',
      artifacts: [
        ['schema.json', url('configuration', 'schema.json')],
        ['configuration.md', url('configuration', 'configuration.md')],
      ],
    }),
  );

  const issuesPage = path.join(PAGES_DIR, 'issue-codes', 'index.mdx');
  write(
    path.join(outRoot, issuesPage),
    page({
      pagePath: issuesPage,
      title: 'Issue codes',
      description: issuesDescription(issues),
      component: 'IssueCodeReference',
      artifacts: [
        ['issues.json', url('issues', 'issues.json')],
        ['issue-codes.md', url('issues', 'issue-codes.md')],
      ],
    }),
  );

  /* One array rather than three exports: astro.config spreads it into the
   * Reference sidebar, and the output check reads the same file to decide
   * which routes the sidebar claims. */
  const sidebar = [
    {
      label: 'Command line',
      items: [
        { label: 'Overview', slug: 'reference/cli' },
        ...entries.map((node) => ({
          label: commandLabel(node.path),
          slug: commandSlug(node.name),
        })),
      ],
    },
    { label: 'Configuration', slug: 'reference/configuration' },
    { label: 'Issue codes', slug: 'reference/issue-codes' },
  ];
  write(path.join(outRoot, SIDEBAR_FILE), `${JSON.stringify(sidebar, null, 2)}\n`);

  const counts = cli.counts;
  console.log(
    `cli reference: ${counts.top_level_entries + 1} command pages (${counts.leaf_commands} leaves, ` +
      `${counts.groups} groups, ${counts.options} options, ${counts.arguments} arguments), ` +
      `${schema.counts.declaredFields} config fields, ${issues.counts.issues} issue codes, ` +
      `source tree ${tree.slice(0, 7)}.`,
  );
}
