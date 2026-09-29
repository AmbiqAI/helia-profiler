import assert from 'node:assert/strict';
import test from 'node:test';

import {
  commandDescription,
  commandsOn,
  configurationEntries,
  panelsOf,
  paramName,
  paramRow,
  plainHelp,
  renderCommandMarkdown,
  renderConfigurationMarkdown,
  renderIssuesMarkdown,
  severityCell,
} from '../src/lib/reference-cli.mjs';

const option = (overrides = {}) => ({
  name: 'engine',
  kind: 'option',
  declaration: '--engine',
  opts: ['--engine'],
  aliases: [],
  secondary_opts: [],
  metavar: null,
  type: { name: 'text', class: 'StringParamType' },
  python_type: 'str',
  default: null,
  required: false,
  help: 'Engine to use.',
  panel: null,
  is_flag: false,
  multiple: false,
  count: false,
  nargs: 1,
  hidden: false,
  envvar: [],
  ...overrides,
});

const leaf = (overrides = {}) => ({
  path: ['profile'],
  name: 'profile',
  kind: 'command',
  usage: 'hpx profile [OPTIONS] MODEL',
  short_help: 'Profile a model.',
  help: 'Profile a model on hardware.',
  epilog: null,
  deprecated: false,
  hidden: false,
  source: { path: 'src/helia_profiler/cli/app.py', line: 93 },
  arguments: [],
  options: [option()],
  panels: [],
  ...overrides,
});

const generatedFrom = {
  sourceTree: '0'.repeat(40),
  helia_profiler: '0.1.6',
  typer: '0.26.8',
  click: '8.3.3',
  pydantic: '2.13.4',
};

test('a command with no help text still gets a description', () => {
  const bare = leaf({ short_help: null, help: null });
  assert.equal(
    commandDescription(bare),
    'Options and arguments for hpx profile, generated from the hpx command line.',
  );
  const group = leaf({ kind: 'group', short_help: null, help: null, path: ['cache'] });
  assert.equal(
    commandDescription(group),
    'The hpx cache command group, generated from the hpx command line.',
  );
});

test('a description is one sentence and fits a meta tag', () => {
  assert.equal(commandDescription(leaf()), 'Profile a model.');
  const wordy = leaf({ short_help: null, help: `${'word '.repeat(60)}. Second sentence.` });
  const description = commandDescription(wordy);
  assert.ok(description.length <= 160, `description is ${description.length} characters`);
  assert.ok(description.endsWith('…'));
});

test('option help reaches the parameter table as plain text', () => {
  /* RefParams escapes the description cell, so a backtick would render as a
   * backtick rather than as a code span. */
  assert.equal(plainHelp('Pass `--offline`\n  to skip the network.'), 'Pass --offline to skip the network.');
  const row = paramRow(option({ help: 'Use `tflm`.', required: true, multiple: true }));
  assert.equal(row.description, 'Use tflm. Required. Repeatable.');
});

test('a parameter name leads with its long declaration', () => {
  assert.equal(paramName(option()), '--engine');
  assert.equal(paramName(option({ aliases: ['-e'] })), '--engine, -e');
});

test('options click never panelled come first', () => {
  const command = leaf({
    options: [
      option({ declaration: '--board', panel: 'Hardware' }),
      option({ declaration: '--verbose' }),
      option({ declaration: '--adapter', panel: 'Advanced' }),
    ],
  });
  assert.deepEqual(
    panelsOf(command).map(([panel]) => panel),
    ['Options', 'Advanced', 'Hardware'],
  );
});

test('a group page carries the group and every leaf under it', () => {
  const group = leaf({
    path: ['cache'],
    name: 'cache',
    kind: 'group',
    options: [],
    commands: [leaf({ path: ['cache', 'info'], name: 'info', options: [] })],
  });
  assert.deepEqual(
    commandsOn(group).map((command) => command.path.join(' ')),
    ['cache', 'cache info'],
  );
});

test('the Markdown rendition heads every command path and rows every parameter', () => {
  const group = leaf({
    path: ['cache'],
    name: 'cache',
    kind: 'group',
    options: [],
    epilog: 'Line one.\n\n  Line two.',
    commands: [
      leaf({
        path: ['cache', 'purge'],
        name: 'purge',
        options: [option({ declaration: '--force', aliases: ['-f'] })],
        arguments: [option({ kind: 'argument', declaration: 'target', name: 'target' })],
      }),
    ],
  });
  const markdown = renderCommandMarkdown(group, generatedFrom);
  assert.match(markdown, /^## hpx cache$/m);
  assert.match(markdown, /^## hpx cache purge$/m);
  assert.match(markdown, /^\| `--force, -f` \|/m);
  assert.match(markdown, /^\| `target` \|/m);
  /* The epilog is an example block the author laid out; reflowing it would
   * change what the reader is told to type. */
  assert.ok(markdown.includes('Line one.\n\n  Line two.'));
  assert.ok(markdown.includes(generatedFrom.sourceTree));
});

const schema = {
  generatedFrom,
  counts: { classes: 2, declaredFields: 2, unreachableFromRoot: ['MonitorBoardPreset'] },
  'x-hpx': {
    root: 'ProfileConfig',
    sections: { '': 'ProfileConfig' },
    fieldIndex: {
      ProfileConfig: [
        {
          name: 'model',
          pythonType: 'str',
          required: true,
          default: null,
          defaultFromFactory: false,
        },
      ],
      MonitorBoardPreset: [
        {
          name: 'shunt_ohms',
          pythonType: 'float',
          required: false,
          default: 0.1,
          defaultFromFactory: false,
        },
      ],
    },
    enums: { Aggregation: ['mean', 'median'] },
  },
  $defs: {
    ProfileConfig: {
      description: 'Top-level configuration.',
      properties: { model: { type: 'string', description: 'Model path.', minLength: 1 } },
    },
    MonitorBoardPreset: { properties: { shunt_ohms: { type: 'number' } } },
  },
};

test('a class the root cannot reach is still on the configuration page', () => {
  assert.deepEqual(
    configurationEntries(schema).map((entry) => [entry.cls, entry.reachable]),
    [
      ['ProfileConfig', true],
      ['MonitorBoardPreset', false],
    ],
  );
  const markdown = renderConfigurationMarkdown(schema);
  assert.match(markdown, /^## MonitorBoardPreset$/m);
  assert.match(markdown, /^\| `shunt_ohms` \|/m);
  /* Every constraint the four columns cannot hold folds into the description. */
  assert.match(markdown, /Model path\. min length 1 Required\./);
  assert.match(markdown, /^\| `mean` \|/m);
});

const issues = {
  generatedFrom,
  counts: { issues: 2, comparability: 1, families: 1, familyCodes: 1 },
  issues: [
    {
      code: 'pmu.missing',
      description: 'No PMU data.',
      severity: 'error',
      modeDependent: false,
      internalSeverity: null,
      externalSeverity: null,
      metricGroup: null,
    },
    {
      code: 'power.gate_not_lowered',
      description: 'The gate never fell.',
      severity: null,
      modeDependent: true,
      internalSeverity: 'error',
      externalSeverity: 'warning',
      metricGroup: 'power',
    },
  ],
  comparability: [
    {
      code: 'result.invalid',
      severity: 'blocking',
      description: 'A result is invalid.',
      metricGroup: null,
    },
  ],
  families: [
    {
      pattern: 'metric.power_<dimension>_mismatch',
      severity: 'metric_blocking',
      description: 'A power dimension differs.',
      metricGroup: 'power',
      dimensions: ['power_scope'],
      codes: ['metric.power_power_scope_mismatch'],
    },
  ],
};

test('a mode-dependent code shows both severities', () => {
  assert.equal(severityCell(issues.issues[0]), 'error');
  assert.equal(severityCell(issues.issues[1]), 'error (internal) / warning (external)');
});

test('the issue-code rendition lists every expanded family code', () => {
  const markdown = renderIssuesMarkdown(issues);
  assert.match(markdown, /^\| `pmu\.missing` \|/m);
  assert.match(markdown, /^\| `result\.invalid` \|/m);
  /* The wire string doubles the power prefix; a reader expanding the pattern
   * by hand would write a code that is never emitted. */
  assert.match(markdown, /^\| `metric\.power_power_scope_mismatch` \|/m);
});
