import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { beforeEach, test } from 'node:test';

const require = createRequire(import.meta.url);
const astroRoot = dirname(require.resolve('astro/package.json'));
const { loadRemoteImage, revalidateRemoteImage } = await import(
  pathToFileURL(join(astroRoot, 'dist/assets/build/remote.js')).href
);
const astroRequire = createRequire(join(astroRoot, 'package.json'));
const CachePolicy = astroRequire('http-cache-semantics');
const imageUrl = 'https://example.invalid/logo.png';

beforeEach((t) => {
  for (const method of ['satisfiesWithoutRevalidation', 'evaluateRequest', 'revalidatedPolicy']) {
    t.mock.method(CachePolicy.prototype, method, () => {
      assert.fail(`Astro reached shared response reuse API ${method}; reassess #602`);
    });
  }
});

// #602: an upstream helper/API change must trigger reassessment of this boundary.
test('documentation configuration requests static output without a server adapter', async () => {
  const { resolveConfig } = await import(
    pathToFileURL(join(astroRoot, 'dist/core/config/index.js')).href
  );
  const { astroConfig } = await resolveConfig({
    root: fileURLToPath(new URL('../', import.meta.url)),
    logLevel: 'silent',
  }, 'build');
  assert.equal(astroConfig.output, 'static');
  assert.equal(astroConfig.adapter, undefined);
});

for (const cacheControl of [
  'private, max-age=3600',
  'no-store',
  'no-cache',
  'max-age=1, must-revalidate',
  'max-age=1, proxy-revalidate',
  's-maxage=1',
]) {
  test(`Astro expires restricted/stale remote images: ${cacheControl}`, async () => {
    const calls = [];
    const fetchImage = async (url, options = {}) => {
      const request = new Request(url, options);
      calls.push({ url: request.url, headers: request.headers });
      return new Response(new Uint8Array([1, 2, 3]), {
        headers: { 'cache-control': cacheControl, age: '120', etag: '"image-v1"' },
      });
    };
    const first = await loadRemoteImage(imageUrl, fetchImage);
    assert.ok(first.expires <= Date.now());
    const second = await revalidateRemoteImage(imageUrl, { etag: '"image-v1"' }, fetchImage);
    assert.ok(second.expires <= Date.now());
    assert.equal(calls.length, 2);
    for (const call of calls) {
      assert.equal(String(call.url), imageUrl);
      for (const name of ['authorization', 'cookie', 'cache-control']) {
        assert.equal(call.headers.get(name), null);
      }
    }
    assert.equal(calls[1].headers.get('if-none-match'), '"image-v1"');
  });
}

test('Astro preserves fresh public image caching and conditional validation', async () => {
  const first = await loadRemoteImage(imageUrl, async () => new Response(new Uint8Array([1]), {
    headers: { 'cache-control': 'public, max-age=3600', etag: '"image-v1"' },
  }));
  assert.ok(first.expires > Date.now() + 3_500_000);
  const validated = await revalidateRemoteImage(imageUrl, { etag: first.etag }, async () =>
    new Response(null, { status: 304, headers: { 'cache-control': 'public, max-age=3600' } }),
  );
  assert.equal(validated.data, null);
  assert.equal(validated.etag, '"image-v1"');
  assert.ok(validated.expires > Date.now() + 3_500_000);
});
