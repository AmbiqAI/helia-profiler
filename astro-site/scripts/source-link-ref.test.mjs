import assert from 'node:assert/strict';
import test from 'node:test';
import { hasSourceRef } from './source-link-ref.mjs';

test('source references accept branch names containing slashes', () => {
  assert.equal(hasSourceRef('blob/codex/docs-polish/src/helia_profiler/api.py#L1', 'codex/docs-polish'), true);
  assert.equal(hasSourceRef('blob/main/src/helia_profiler/api.py#L1', 'main'), true);
  assert.equal(hasSourceRef('blob/v0.1.6/src/helia_profiler/api.py#L1', 'v0.1.6'), true);
});

test('source references reject a different or truncated branch', () => {
  assert.equal(hasSourceRef('blob/codex/src/helia_profiler/api.py#L1', 'codex/docs-polish'), false);
  assert.equal(hasSourceRef('blob/codex/docs-polish/src/helia_profiler/api.py#L1', 'codex'), false);
  assert.equal(hasSourceRef('blob/main/src/helia_profiler/api.py#L1', 'v0.1.6'), false);
});
