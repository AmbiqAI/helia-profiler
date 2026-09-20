// Stage the committed JSON the way the plan serves it: src/data/ for the
// build-time import, public/reference/ for the fetchable URL.
import { mkdirSync, copyFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const data = join(root, '..', 'data');

const targets = [
  ['cli.json', join(root, 'src', 'data'), join(root, 'public', 'reference', 'cli')],
  ['schema.json', join(root, 'src', 'data'), join(root, 'public', 'reference', 'configuration')],
];

for (const [name, dataDir, publicDir] of targets) {
  for (const dir of [dataDir, publicDir]) {
    mkdirSync(dir, { recursive: true });
    copyFileSync(join(data, name), join(dir, name));
  }
}
console.log('staged cli.json and schema.json');
