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
 * dump for the same reason: `__api_stability__` is three comprehensions over
 * three sets of names, and all four are literals in the source.
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

/**
 * The tier map, read from the definition of `__api_stability__`.
 *
 * Reading the three sets directly would be the same names in a different
 * order; reading them through the comprehensions that build the map means a
 * fourth tier, or a set dropped from it, changes this output rather than
 * being silently ignored.
 */
export function tierMap(dump) {
  const members = dump[PACKAGE]?.members;
  if (!members) throw new Error(`The dump has no package "${PACKAGE}".`);

  const names = (node, kinds) => {
    const value = node?.value;
    if (!value || !kinds.includes(value.cls) || !Array.isArray(value.elements)) return undefined;
    return value.elements.map(literal);
  };

  const all = names(members.__all__, ['ExprList', 'ExprTuple', 'ExprSet']);
  if (!all) throw new Error(`${PACKAGE}.__all__ is not a literal list of names.`);

  const comprehensions = members[STABILITY]?.value?.values;
  if (!Array.isArray(comprehensions) || comprehensions.length === 0) {
    throw new Error(`${PACKAGE}.${STABILITY} is not a dict of comprehensions over the tier sets.`);
  }

  const stability = {};
  const tiers = {};
  for (const entry of comprehensions) {
    const setName = entry?.generators?.[0]?.iterable?.name;
    const tier = literal(entry?.value);
    if (!setName || !tier) {
      throw new Error(`${PACKAGE}.${STABILITY} has an entry this cannot read statically.`);
    }
    const members_ = names(members[setName], ['ExprSet', 'ExprList', 'ExprTuple']);
    if (!members_) throw new Error(`${PACKAGE}.${setName} is not a literal set of names.`);
    tiers[tier] = members_.length;
    for (const name of members_) {
      if (stability[name]) {
        throw new Error(`${name} is in both the ${stability[name]} and ${tier} tiers.`);
      }
      stability[name] = tier;
    }
  }

  const untiered = all.filter((name) => !stability[name]);
  if (untiered.length > 0) {
    throw new Error(`${PACKAGE}.__all__ names without a stability tier: ${untiered.join(', ')}`);
  }
  const unpublished = Object.keys(stability).filter((name) => !all.includes(name));
  if (unpublished.length > 0) {
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
