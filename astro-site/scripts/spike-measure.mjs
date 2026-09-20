#!/usr/bin/env node
/*
 * Build the current variant and record the four numbers the interactivity
 * decision turns on: dist size, largest page HTML and its gzip, build wall
 * time, and lockfile size. Written to spike/results/<variant>.json.
 *
 * The build is run from a clean dist and a clean .astro cache, because a warm
 * content-layer cache changes the wall time by more than the difference being
 * measured.
 */
import { execFileSync } from 'node:child_process';
import { gzipSync } from 'node:zlib';
import { mkdir, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const variant = JSON.parse(
  await readFile(join(root, 'src/data/variant.json'), 'utf8'),
);

async function walk(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await walk(full)));
    else out.push(full);
  }
  return out;
}

/* Outside the timer: the reference pages are an input to the build being
 * measured, not part of it. */
execFileSync('node', ['scripts/build-reference.mjs'], { cwd: root, stdio: 'inherit' });

await rm(join(root, 'dist'), { recursive: true, force: true });
await rm(join(root, '.astro'), { recursive: true, force: true });

const started = Date.now();
execFileSync('npx', ['astro', 'build'], { cwd: root, stdio: 'inherit' });
const wallMs = Date.now() - started;

const files = await walk(join(root, 'dist'));
let distBytes = 0;
for (const file of files) distBytes += (await stat(file)).size;

const pages = [];
for (const file of files.filter((name) => name.endsWith('.html'))) {
  const body = await readFile(file);
  pages.push({
    path: relative(join(root, 'dist'), file),
    html: body.length,
    gzip: gzipSync(body, { level: 9 }).length,
  });
}
pages.sort((a, b) => b.html - a.html);

const js = files.filter((name) => name.endsWith('.js'));
let jsBytes = 0;
for (const file of js) jsBytes += (await stat(file)).size;

const css = files.filter((name) => name.endsWith('.css'));
let cssBytes = 0;
for (const file of css) cssBytes += (await stat(file)).size;

/* What a reader of the Examples page actually downloads: the scripts that page
 * references, not every chunk in the build. */
async function pageScripts(page) {
  const html = await readFile(join(root, 'dist', page), 'utf8');
  const names = new Set(
    [...html.matchAll(/_astro\/([\w.-]+\.js)/g)].map((match) => match[1]),
  );
  let bytes = 0;
  let gzip = 0;
  for (const name of names) {
    const body = await readFile(join(root, 'dist/_astro', name));
    bytes += body.length;
    gzip += gzipSync(body, { level: 9 }).length;
  }
  return { files: names.size, bytes, gzip };
}

const lock = await stat(join(root, 'package-lock.json')).catch(() => null);

const result = {
  variant: variant.name,
  node: process.version,
  buildWallMs: wallMs,
  distBytes,
  distFiles: files.length,
  htmlPages: pages.length,
  clientJsFiles: js.length,
  clientJsBytes: jsBytes,
  cssBytes,
  lockfileBytes: lock?.size ?? null,
  largestPages: pages.slice(0, 5),
  examplesPage: pages.find((page) => page.path.startsWith('examples/')) ?? null,
  examplesPageScripts: await pageScripts('examples/index.html'),
};

await mkdir(join(root, 'spike/results'), { recursive: true });
await writeFile(
  join(root, `spike/results/${variant.name}.json`),
  `${JSON.stringify(result, null, 2)}\n`,
  'utf8',
);
console.log(JSON.stringify(result, null, 2));
