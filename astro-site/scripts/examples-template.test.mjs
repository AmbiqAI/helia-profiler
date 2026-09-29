/* Every example page carries the nine template headings, in order, so a reader
 * (or an agent) can rely on the same shape on every page (#344). */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const examplesDir = path.join(here, '..', 'src', 'content', 'docs', 'examples');
const repoRoot = path.join(here, '..', '..');

export const TEMPLATE_HEADINGS = [
  'Goal',
  'Hardware and wiring',
  'Software prerequisites',
  'Config file',
  'Run',
  'Expected output',
  'How to read it',
  'Variations',
  'Validated on',
];

export function examplePages(dir = examplesDir) {
  return fs
    .readdirSync(dir)
    .filter((name) => name.endsWith('.mdx') && name !== 'index.mdx')
    .sort()
    .map((name) => path.join(dir, name));
}

export function h2Headings(source) {
  const body = source.replace(/^---[\s\S]*?---\s*/, '').replace(/```[\s\S]*?```/g, '');
  return [...body.matchAll(/^## (.+)$/gm)].map((m) => m[1].trim());
}

export function templateGaps(source) {
  const found = h2Headings(source);
  const gaps = [];
  let cursor = 0;
  for (const heading of TEMPLATE_HEADINGS) {
    const at = found.indexOf(heading, cursor);
    if (at === -1) {
      gaps.push(found.includes(heading) ? `${heading} is out of order` : `${heading} is missing`);
      continue;
    }
    cursor = at + 1;
  }
  return gaps;
}

test('every example page carries the nine template headings in order', () => {
  const pages = examplePages();
  assert.ok(pages.length > 0, 'no example pages found');
  const failures = [];
  for (const page of pages) {
    const gaps = templateGaps(fs.readFileSync(page, 'utf8'));
    if (gaps.length) failures.push(`${path.basename(page)}: ${gaps.join('; ')}`);
  }
  assert.deepEqual(failures, []);
});

test('a page with a heading missing or out of order is reported', () => {
  const ok = TEMPLATE_HEADINGS.map((h) => `## ${h}\n\ntext\n`).join('\n');
  assert.deepEqual(templateGaps(`---\ntitle: x\n---\n${ok}`), []);
  assert.deepEqual(templateGaps(ok.replace('## Run\n', '')), ['Run is missing']);
  const swapped = ok.replace('## Goal\n', '## TEMP\n').replace('## Run\n', '## Goal\n').replace('## TEMP\n', '## Run\n');
  assert.ok(templateGaps(swapped).length > 0);
  assert.deepEqual(templateGaps(`## Goal\n\n\`\`\`\n## Run\n\`\`\`\n`), TEMPLATE_HEADINGS.slice(1).map((h) => `${h} is missing`));
});

/* A config an example names has to exist in the repository, so the download
 * link and the `--config` line both work as written. */
export function configPaths(source) {
  return [...new Set([...source.matchAll(/\b((?:examples|configs)\/[\w./-]+\.ya?ml)\b/g)].map((m) => m[1]))];
}

test('every config path on an example page resolves to a file in the repository', () => {
  const missing = [];
  let seen = 0;
  for (const page of examplePages()) {
    for (const rel of configPaths(fs.readFileSync(page, 'utf8'))) {
      seen += 1;
      if (!fs.existsSync(path.join(repoRoot, rel))) missing.push(`${path.basename(page)}: ${rel}`);
    }
  }
  assert.ok(seen > 0, 'no config paths found on any example page');
  assert.deepEqual(missing, []);
});

test('config paths are extracted from prose, code and links', () => {
  const src = 'Use `examples/quickstart/hpx_rt.yml` or [it](https://x/configs/mlperf_tiny/kws_rt_power.yaml); not examples/notes.txt';
  assert.deepEqual(configPaths(src), ['examples/quickstart/hpx_rt.yml', 'configs/mlperf_tiny/kws_rt_power.yaml']);
});
