/*
 * The guard decides whether documentation gets replaced, so its states are
 * asserted here rather than discovered in production. The live payload is
 * served over HTTP by a throwaway server so the fetch path, including the 404,
 * is the one that runs in the deploy job.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { after, before, test } from 'node:test';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';

const scripts = path.dirname(fileURLToPath(import.meta.url));
const fixtures = path.join(scripts, 'fixtures');
const guard = path.join(scripts, 'deploy-guard.mjs');

/** Serves a fixture per route; anything else is the pre-cutover 404. */
const responses = {
  '/live/build-info.json': ['application/json', 'live-0.2.0.json'],
  '/garbage/build-info.json': ['text/html', 'live-unparseable.txt'],
};

let server;
let origin;

before(async () => {
  server = http.createServer((request, response) => {
    const entry = responses[request.url];
    if (!entry) {
      response.writeHead(404, { 'content-type': 'text/plain' });
      response.end('Not Found');
      return;
    }
    const [type, file] = entry;
    response.writeHead(200, { 'content-type': type });
    response.end(fs.readFileSync(path.join(fixtures, file)));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

after(() => server.close());

/* The fixture server runs in this process, so the guard has to be spawned
 * asynchronously: a synchronous spawn would block the event loop that serves
 * its own fetch. */
const runGuard = promisify(execFile);

const run = async (livePath, candidate) => {
  const output = path.join(
    fs.mkdtempSync(path.join(os.tmpdir(), 'deploy-guard-')),
    'output',
  );
  fs.writeFileSync(output, '');
  const { stdout: log } = await runGuard(process.execPath, [guard], {
    encoding: 'utf8',
    env: {
      ...process.env,
      GITHUB_OUTPUT: output,
      DOCS_LIVE_BUILD_INFO_URL: `${origin}${livePath}`,
      DOCS_BUILD_INFO: path.join(fixtures, candidate),
    },
  });
  const decision = Object.fromEntries(
    fs
      .readFileSync(output, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => [line.slice(0, line.indexOf('=')), line.slice(line.indexOf('=') + 1)]),
  );
  return { ...decision, log };
};

test('no live build-info means nothing to order against', async () => {
  const { publish } = await run('/absent/build-info.json', 'candidate-0.2.0-newer.json');
  assert.equal(publish, 'true');
});

test('an unparseable live build-info is treated as absent', async () => {
  const { publish } = await run('/garbage/build-info.json', 'candidate-0.2.0-newer.json');
  assert.equal(publish, 'true');
});

test('the same version with an older commit time is refused', async () => {
  const { publish, reason } = await run('/live/build-info.json', 'candidate-0.2.0-older.json');
  assert.equal(publish, 'false');
  assert.match(reason, /older than the live/);
});

test('a lower version is refused', async () => {
  const { publish, reason } = await run('/live/build-info.json', 'candidate-0.1.9.json');
  assert.equal(publish, 'false');
  assert.match(reason, /0\.1\.9 is older than the live 0\.2\.0/);
});

test('the same version with a newer commit time proceeds', async () => {
  const { publish } = await run('/live/build-info.json', 'candidate-0.2.0-newer.json');
  assert.equal(publish, 'true');
});

test('a higher version proceeds', async () => {
  const { publish } = await run('/live/build-info.json', 'candidate-0.3.0.json');
  assert.equal(publish, 'true');
});

test('refusing is a skip, not a failure', async () => {
  const { log } = await run('/live/build-info.json', 'candidate-0.1.9.json');
  assert.match(log, /^Skip: /);
});
