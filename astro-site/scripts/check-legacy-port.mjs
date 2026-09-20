/*
 * The committed content collection has to be what the port script produces.
 *
 * The pages under src/content/docs are generated from docs/, so a hand edit
 * there is lost the next time the port runs and a reviewer has no way to see
 * it coming. This regenerates into a scratch directory and compares byte for
 * byte, the same way check-reference-stale.mjs does for the API reference.
 *
 * The authored pages the port carries but does not write (see PRESERVE in
 * port-legacy-docs.mjs) are seeded into the scratch tree from the committed
 * one, so they are outside what this asserts.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.resolve(here, '..');
const committed = path.join(site, 'src/content/docs');

const walk = (dir, prefix = '') =>
  fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    return entry.isDirectory() ? walk(path.join(dir, entry.name), rel) : [rel];
  });

const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'hpx-port-check-'));
try {
  execFileSync(
    process.execPath,
    [path.join(here, 'port-legacy-docs.mjs'), '--out', scratch],
    { cwd: site, stdio: 'pipe' },
  );

  const fresh = new Set(walk(scratch));
  const have = new Set(walk(committed));
  const failures = [];

  for (const rel of [...fresh].sort()) {
    if (!have.has(rel)) {
      failures.push(`${rel}: regenerated but not committed.`);
      continue;
    }
    const a = fs.readFileSync(path.join(scratch, rel));
    const b = fs.readFileSync(path.join(committed, rel));
    if (!a.equals(b)) failures.push(`${rel}: committed copy differs from a fresh run.`);
  }
  for (const rel of [...have].sort()) {
    if (!fresh.has(rel)) failures.push(`${rel}: committed but not regenerated.`);
  }

  if (failures.length > 0) {
    console.error('Ported pages are stale:\n');
    for (const failure of failures) console.error(`- ${failure}`);
    console.error('\nRun `node scripts/port-legacy-docs.mjs` and commit the result.');
    process.exit(1);
  }

  console.log(
    `Ported pages current: ${fresh.size} file(s) reproduced byte for byte from docs/.`,
  );
} finally {
  fs.rmSync(scratch, { recursive: true, force: true });
}
