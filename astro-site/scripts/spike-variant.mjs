#!/usr/bin/env node
/*
 * Switch the scaffold between the Astro-only baseline and the React plus
 * Tailwind variant, so the cost measurement in SPIKE-REPORT.md is reproducible
 * rather than a pair of numbers someone once saw.
 *
 * The two variants differ in three places and this script owns all three: the
 * dependency block in package.json, the Examples index page, and the flag
 * astro.config.mjs reads to decide whether to load @astrojs/react. The lockfile
 * for each variant is kept under spike/ and copied into place, because lockfile
 * size is one of the numbers being compared.
 */
import { copyFile, readFile, rm, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const name = process.argv[2];

const spec = JSON.parse(
  await readFile(join(root, 'spike/variants.json'), 'utf8'),
);
if (!Object.hasOwn(spec.variants, name)) {
  console.error(
    `usage: spike-variant.mjs <${Object.keys(spec.variants).join('|')}>`,
  );
  process.exit(1);
}
const variant = spec.variants[name];

const sorted = (entries) =>
  Object.fromEntries(Object.entries(entries).sort(([a], [b]) => a.localeCompare(b)));

const pkgPath = join(root, 'package.json');
const pkg = JSON.parse(await readFile(pkgPath, 'utf8'));
pkg.dependencies = sorted({
  ...spec.shared.dependencies,
  ...(variant.dependencies ?? {}),
});
pkg.devDependencies = sorted({
  ...spec.shared.devDependencies,
  ...(variant.devDependencies ?? {}),
});
await writeFile(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`, 'utf8');

await writeFile(
  join(root, 'src/data/variant.json'),
  `${JSON.stringify({ name, react: name === 'react' }, null, 2)}\n`,
  'utf8',
);

await copyFile(
  join(root, `spike/examples-index.${name}.mdx`),
  join(root, 'src/content/docs/examples/index.mdx'),
);

/* The island is a .tsx file: leaving it in src/ for the baseline would make
 * `astro check` demand React types the baseline does not install. */
const island = join(root, 'src/components/ExamplesTable.tsx');
if (name === 'react') await copyFile(join(root, 'spike/ExamplesTable.tsx'), island);
else await rm(island, { force: true });

const lockSource = join(root, variant.lockfile);
const lockTarget = join(root, 'package-lock.json');
if (existsSync(lockSource)) await copyFile(lockSource, lockTarget);
else await rm(lockTarget, { force: true });

console.log(
  `variant: ${name} (react=${name === 'react'}), lockfile ${existsSync(lockSource) ? variant.lockfile : 'absent'}`,
);
