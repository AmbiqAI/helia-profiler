/*
 * The pieces of the Python reference pipeline that only fail somewhere
 * inconvenient: a build machine with no git history, a page whose symbol
 * carries no docstring, and the cut from llms-full.txt to per-module
 * Markdown. Each of those is a build failure or a silently wrong artifact, so
 * they are asserted here rather than met in CI.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { tierMap } from './dump-python.mjs';
import { sourceCommit, splitLlmsFull } from './build-reference.mjs';
import { groupSummary, scope, tierNote } from './scope-dump.mjs';

test('the source commit lookup fails by name outside a git checkout', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'hpx-nongit-'));
  try {
    assert.throws(
      () => sourceCommit(directory),
      (error) => {
        assert.match(error.message, /not a git checkout/);
        assert.match(error.message, /fetch-depth: 0/);
        return true;
      },
    );
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('the source commit lookup refuses an empty commit', () => {
  const run = (cwd, args) => (args[0] === 'rev-parse' ? 'true' : '');
  assert.throws(
    () => sourceCommit('/anywhere', { run }),
    (error) => {
      assert.match(error.message, /returned ""/);
      assert.match(error.message, /fetch-depth: 0/);
      return true;
    },
  );
});

test('a page description survives a group and a module with no docstring', () => {
  const summary = groupSummary({ path: 'helia_profiler.nodocs', members: [] }, undefined);
  assert.ok(summary.trim().length > 0);
  assert.match(summary, /nodocs/);
});

const dumpWithoutDocstrings = {
  helia_profiler: {
    kind: 'module',
    name: 'helia_profiler',
    path: 'helia_profiler',
    members: {
      Bare: { kind: 'class', name: 'Bare', path: 'helia_profiler.bare.Bare', members: {} },
    },
  },
};

test('a symbol with no docstring still gets a tier badge and leaves the page described', () => {
  const { scoped, report } = scope({
    dump: dumpWithoutDocstrings,
    tiers: { all: ['Bare'], stability: { Bare: 'stable' } },
    groups: {
      package: 'helia_profiler',
      importFrom: 'helia_profiler',
      groups: [{ path: 'helia_profiler', members: ['Bare'] }],
    },
  });

  assert.deepEqual(report.problems, []);
  const page = scoped.helia_profiler;
  const pageText = page.docstring.parsed.map((section) => section.value).join('\n\n');
  assert.ok(pageText.trim().length > 0, 'the page would render with an empty description');
  assert.match(page.docstring.parsed[0].value, /helia_profiler/);

  const symbol = page.members.Bare;
  assert.equal(symbol.path, 'helia_profiler.Bare');
  assert.deepEqual(symbol.docstring.parsed, [{ kind: 'text', value: tierNote(['stable']) }]);
});

test('the scoping pass names a published symbol that no page claims', () => {
  const { report } = scope({
    dump: dumpWithoutDocstrings,
    tiers: { all: ['Bare'], stability: { Bare: 'stable' } },
    groups: {
      package: 'helia_profiler',
      importFrom: 'helia_profiler',
      groups: [{ path: 'helia_profiler', members: [] }],
    },
  });

  assert.deepEqual(report.problems, ['Bare: in scope but on no page of the grouping manifest.']);
});

test('llms-full.txt is cut back into one document per page', () => {
  const llmsFull = [
    '# helia_profiler\n\nRoot page.',
    '# helia_profiler.results\n\nResults page.',
    '# helia_profiler.results.manifest\n\nManifest page.',
  ].join('\n\n---\n\n');

  const sections = splitLlmsFull(llmsFull, [
    'helia_profiler',
    'helia_profiler.results',
    'helia_profiler.results.manifest',
  ]);

  assert.equal(sections.get('helia_profiler'), '# helia_profiler\n\nRoot page.\n');
  assert.equal(sections.get('helia_profiler.results'), '# helia_profiler.results\n\nResults page.\n');
  assert.equal(
    sections.get('helia_profiler.results.manifest'),
    '# helia_profiler.results.manifest\n\nManifest page.\n',
  );
});

test('a page missing from llms-full.txt is a failure, not an empty file', () => {
  assert.throws(
    () => splitLlmsFull('# helia_profiler\n\nRoot page.\n', ['helia_profiler', 'helia_profiler.gone']),
    /no section for helia_profiler\.gone/,
  );
});

/* `__api_stability__` as griffe dumps it: a dict of comprehensions, one per
 * tier, each over a literal set of names. */
const stabilityDump = (tiers) => ({
  helia_profiler: {
    members: {
      __all__: {
        value: {
          cls: 'ExprList',
          elements: Object.values(tiers).flat().map((name) => `'${name}'`),
        },
      },
      ...Object.fromEntries(
        Object.entries(tiers).map(([tier, names]) => [
          `_${tier.toUpperCase()}_API`,
          { value: { cls: 'ExprSet', elements: names.map((name) => `'${name}'`) } },
        ]),
      ),
      __api_stability__: {
        value: {
          cls: 'ExprDict',
          values: Object.keys(tiers).map((tier) => ({
            cls: 'ExprDictComp',
            generators: [{ iterable: { name: `_${tier.toUpperCase()}_API` } }],
            value: `'${tier}'`,
          })),
        },
      },
    },
  },
});

test('the tier map is read from the definition of __api_stability__', () => {
  const tiers = tierMap(stabilityDump({ stable: ['A', 'B'], experimental: ['C'] }));

  assert.deepEqual(tiers.tiers, { stable: 2, experimental: 1 });
  assert.deepEqual(tiers.stability, { A: 'stable', B: 'stable', C: 'experimental' });
  assert.deepEqual(tiers.all, ['A', 'B', 'C']);
});

test('a name in two tiers, or in none, stops the dump', () => {
  const twice = stabilityDump({ stable: ['A'], experimental: ['A'] });
  assert.throws(() => tierMap(twice), /in both the stable and experimental tiers/);

  const untiered = stabilityDump({ stable: ['A'] });
  untiered.helia_profiler.members.__all__.value.elements.push("'B'");
  assert.throws(() => tierMap(untiered), /without a stability tier: B/);
});
