#!/usr/bin/env node
/*
 * Produce the two inputs the Python reference is generated from: the griffe
 * dump of the package in this checkout, and the API tier map the reference is
 * scoped and badged with.
 *
 * griffe is pinned to the release helia-ui's pyref reader was written against.
 * The dump is not a versioned format, so an unpinned griffe would change the
 * tree under us with no signal. A pinned griffe already on PATH is used as it
 * is, which is how CI gets it; otherwise `uv run --with` fetches it without
 * adding it to the project's own dependency set.
 *
 * griffe reads the source rather than importing it, so this needs no runtime
 * dependencies, no board and no secrets. The tier map is read out of the
 * dump for the same reason: the lazy export catalog records a literal tier
 * alongside each target.
 *
 * Both outputs land in .generated/, which is not committed: the dump embeds
 * the absolute source paths of the machine that produced it.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.resolve(site, '..');

export const GRIFFE_VERSION = '1.7.3';
export const PACKAGE = 'helia_profiler';
export const STABILITY = '__api_stability__';

const run = (command, args) =>
  execFileSync(command, args, {
    cwd: repo,
    encoding: 'utf8',
    maxBuffer: 1 << 28,
    stdio: ['ignore', 'pipe', 'pipe'],
  });

/** `griffe` at the pinned version, however this machine supplies it. */
export function griffeCommand({ exec = run } = {}) {
  try {
    const version = exec('griffe', ['--version']).trim();
    if (version.includes(GRIFFE_VERSION)) return { command: 'griffe', prefix: [], version };
  } catch {
    /* Not on PATH, or not runnable: fall through to uv. */
  }
  const prefix = ['run', '--with', `griffe==${GRIFFE_VERSION}`, 'griffe'];
  const version = exec('uv', [...prefix, '--version']).trim();
  if (!version.includes(GRIFFE_VERSION)) {
    throw new Error(`Expected griffe ${GRIFFE_VERSION}, got "${version}".`);
  }
  return { command: 'uv', prefix, version };
}

/** The string a griffe literal element stands for. */
export const literal = (value) =>
  typeof value === 'string' ? value.replace(/^['"]|['"]$/g, '') : undefined;

/** Read literal export targets and tiers without importing the package. */
export function tierMap(dump) {
  const members = dump[PACKAGE]?.members;
  if (!members) throw new Error(`The dump has no package "${PACKAGE}".`);

  const string = (value) =>
    typeof value === 'string' && /^(['"]).*\1$/s.test(value) ? literal(value) : undefined;
  const allNode = members.__all__?.value;
  if (!['ExprList', 'ExprTuple'].includes(allNode?.cls) || !Array.isArray(allNode.elements)) {
    throw new Error(`${PACKAGE}.__all__ is not a literal list of names.`);
  }
  const all = allNode.elements.map(string);
  if (all.some((name) => !name) || new Set(all).size !== all.length) {
    throw new Error(`${PACKAGE}.__all__ has invalid or duplicate names.`);
  }

  const catalog = members._EXPORTS?.value;
  if (catalog?.cls !== 'ExprDict' || !Array.isArray(catalog.keys) ||
      !Array.isArray(catalog.values) || catalog.keys.length !== catalog.values.length) {
    throw new Error(`${PACKAGE}._EXPORTS is not a literal dictionary.`);
  }
  const stability = {};
  const tiers = {};
  for (let index = 0; index < catalog.keys.length; index++) {
    const name = string(catalog.keys[index]);
    const entry = catalog.values[index];
    if (!name || Object.hasOwn(stability, name)) {
      throw new Error(`${PACKAGE}._EXPORTS has an invalid or duplicate name: ${name}`);
    }
    if (entry?.cls !== 'ExprTuple' || entry.elements?.length !== 3) {
      throw new Error(`${name}: expected a literal (module, attribute, tier) tuple.`);
    }
    const [module, attribute, tier] = entry.elements.map(string);
    if (!module?.startsWith('.') || (!attribute && entry.elements[1] !== 'None') ||
        !['stable', 'experimental', 'implementation'].includes(tier)) {
      throw new Error(`${name}: invalid module, attribute or stability tier.`);
    }
    stability[name] = tier;
    tiers[tier] = (tiers[tier] ?? 0) + 1;
  }
  const untiered = all.filter((name) => !Object.hasOwn(stability, name));
  if (untiered.length) {
    throw new Error(`${PACKAGE}.__all__ names without a stability tier: ${untiered.join(', ')}`);
  }
  const unpublished = Object.keys(stability).filter((name) => !all.includes(name));
  if (unpublished.length) {
    throw new Error(`Tiered but not in ${PACKAGE}.__all__: ${unpublished.join(', ')}`);
  }
  return { all, stability, tiers };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const generated = path.join(site, '.generated');
  const { command, prefix, version } = griffeCommand();
  console.log(`griffe: ${version}`);

  /* One docstring parser, not `auto`: `auto` parses neither style on this
   * package, and a mixed tree renders empty parameter tables rather than
   * failing. The source is held to Google style by the package's own tests. */
  const dump = run(command, [
    ...prefix,
    'dump',
    PACKAGE,
    '--search',
    'src',
    '--docstyle',
    'google',
    '-f',
  ]);

  fs.mkdirSync(generated, { recursive: true });
  fs.writeFileSync(path.join(generated, 'griffe.json'), dump, 'utf8');
  console.log(`dump: ${dump.length} bytes to .generated/griffe.json`);

  const tiers = tierMap(JSON.parse(dump));
  fs.writeFileSync(
    path.join(generated, 'api-tiers.json'),
    `${JSON.stringify(tiers, null, 2)}\n`,
    'utf8',
  );
  console.log(`tiers: ${tiers.all.length} in __all__, ${JSON.stringify(tiers.tiers)}`);
}
