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
const UNSTABLE = '/unstable/build-info.json';

const responses = {
  '/live/build-info.json': ['application/json', 'live-0.2.0.json'],
  '/garbage/build-info.json': ['text/html', 'live-unparseable.txt'],
};

let server;
let origin;
/* No listener was ever bound here, so a request to it is refused rather than
 * answered: the DNS and connection failures the guard has to survive. */
let deadOrigin;
let unstableRequests = 0;

before(async () => {
  server = http.createServer((request, response) => {
    if (request.url === UNSTABLE) {
      unstableRequests += 1;
      response.writeHead(503, { 'content-type': 'text/plain' });
      response.end('Service Unavailable');
      return;
    }
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

  const closed = http.createServer();
  await new Promise((resolve) => closed.listen(0, '127.0.0.1', resolve));
  deadOrigin = `http://127.0.0.1:${closed.address().port}`;
  await new Promise((resolve) => closed.close(resolve));
});

after(() => server.close());

/* The fixture server runs in this process, so the guard has to be spawned
 * asynchronously: a synchronous spawn would block the event loop that serves
 * its own fetch. */
const runGuard = promisify(execFile);

const run = async (livePath, candidate, { url } = {}) => {
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
      DOCS_LIVE_BUILD_INFO_URL: url ?? `${origin}${livePath}`,
      DOCS_BUILD_INFO: path.join(fixtures, candidate),
      /* The retry policy is asserted by attempt count, not by wall time. */
      DOCS_LIVE_FETCH_BACKOFF_MS: '5',
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

test('a failing live site is retried, then refused rather than overwritten', async () => {
  unstableRequests = 0;
  const { publish, reason, log } = await run(UNSTABLE, 'candidate-0.3.0.json');
  assert.equal(publish, 'false');
  assert.match(reason, /could not read the live site/i);
  assert.equal(unstableRequests, 4);
  assert.match(log, /^Skip: /m);
});

test('a refused connection is refused rather than overwritten', async () => {
  const { publish, reason } = await run(null, 'candidate-0.3.0.json', {
    url: `${deadOrigin}/build-info.json`,
  });
  assert.equal(publish, 'false');
  assert.match(reason, /could not read the live site/i);
});

test('refusing is a skip, not a failure', async () => {
  const { log } = await run('/live/build-info.json', 'candidate-0.1.9.json');
  assert.match(log, /^Skip: /m);
});
