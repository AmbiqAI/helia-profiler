import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const lock = JSON.parse(readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
const floors = {
  vite: '8.3.3',
  sharp: '0.35.5',
  'smol-toml': '1.9.0',
  'source-map-js': '1.2.2',
  'postcss-selector-parser': '7.1.6',
  'fast-uri': '3.1.9',
  undici: '8.12.2',
  zod: '4.6.6',
};

function atLeast(version, minimum) {
  if (!/^\d+\.\d+\.\d+$/.test(version)) return false;
  const actual = version.split('.').map(Number);
  const required = minimum.split('.').map(Number);
  for (let i = 0; i < 3; i++) {
    if (actual[i] !== required[i]) return actual[i] > required[i];
  }
  return true;
}

for (const [name, minimum] of Object.entries(floors)) {
  test(`${name} stays above the advisory floor in every lock occurrence (#602)`, () => {
    const entries = Object.entries(lock.packages).filter(([path]) =>
      path.endsWith(`/node_modules/${name}`) || path === `node_modules/${name}`,
    );
    assert.ok(entries.length, `${name} was removed; review whether its advisory guard is still needed`);
    for (const [path, metadata] of entries) {
      assert.ok(atLeast(metadata.version, minimum), `${path}: ${metadata.version} must be >=${minimum}`);
    }
  });
}

test('floor comparison rejects older and prerelease versions', () => {
  assert.equal(atLeast('6.1.4', '7.1.6'), false);
  assert.equal(atLeast('7.1.5', '7.1.6'), false);
  assert.equal(atLeast('7.1.6-rc.1', '7.1.6'), false);
  assert.equal(atLeast('7.1.6', '7.1.6'), true);
  assert.equal(atLeast('7.1.10', '7.1.6'), true);
});
