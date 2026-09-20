/*
 * Writes the build provenance the site imports and the deploy guard reads.
 *
 * Two copies on purpose: src/data/ is what astro.config and the version
 * component import at build time, public/ is what ends up at /build-info.json
 * so the next deployment can compare itself against what is already live.
 *
 * Runs in prebuild. A clean checkout that builds before this has run fails
 * `astro check` on the missing import.
 */
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(site, '..');

const git = (...args) => {
  try {
    return execFileSync('git', args, {
      cwd: root,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
  } catch (error) {
    throw new Error(`git ${args.join(' ')} failed: ${error.message}`);
  }
};

/* A source archive or a container copy has no git data, and a shallow clone
 * can answer some queries and not others. Both would otherwise ship a site
 * that claims a provenance it does not have. */
if (git('rev-parse', '--is-inside-work-tree') !== 'true') {
  throw new Error(`${root} is not a git checkout; build provenance is unavailable.`);
}

const commit = git('rev-parse', 'HEAD');
if (!/^[0-9a-f]{40}$/.test(commit)) {
  throw new Error(`Refusing to build: HEAD resolved to "${commit}".`);
}

const commitTime = git('show', '-s', '--format=%cI', commit);
if (!commitTime) {
  throw new Error('Refusing to build: HEAD has no commit time.');
}

const versionSource = fs.readFileSync(
  path.join(root, 'src/helia_profiler/_version.py'),
  'utf8',
);
const version = versionSource.match(/__version__\s*=\s*"([^"]+)"/)?.[1];
if (!version) throw new Error('Package version missing from _version.py.');

const tags = git('tag', '--points-at', commit).split('\n').filter(Boolean);
const releaseTag = tags.find((tag) => tag === `v${version}`) ?? null;

/* `git describe` needs tags, which a shallow fetch does not bring. Missing
 * tags leave the distance unknown rather than silently reporting zero. */
let commitsSinceTag = null;
try {
  const described = git('describe', '--tags', '--match', 'v*', '--long', commit);
  commitsSinceTag = Number(described.split('-').at(-2));
} catch {
  commitsSinceTag = null;
}

const shortCommit = commit.slice(0, 7);
const modified = Boolean(git('status', '--porcelain', '--untracked-files=normal'));
const display =
  commitsSinceTag === 0
    ? `v${version}`
    : `v${version} + ${commitsSinceTag ?? 'unknown'} commits (${shortCommit})`;

/* The workflow passes the ref it checked out. Nothing else knows it: a tag
 * checkout leaves git in detached HEAD, which reports no branch name. */
const sourceRef =
  process.env.DOCS_SOURCE_REF?.trim() ||
  releaseTag ||
  git('rev-parse', '--abbrev-ref', 'HEAD');

const buildInfo = {
  product: 'heliaPROFILER',
  version,
  display,
  commit,
  shortCommit,
  commitTime,
  commitsSinceTag,
  releaseTag,
  sourceRef,
  sourceUrl: `https://github.com/AmbiqAI/helia-profiler/tree/${commit}`,
  buildTime: new Date().toISOString(),
  modified,
};

const json = `${JSON.stringify(buildInfo, null, 2)}\n`;
for (const directory of ['src/data', 'public']) {
  fs.mkdirSync(path.join(site, directory), { recursive: true });
  fs.writeFileSync(path.join(site, directory, 'build-info.json'), json);
}

console.log(
  `Docs source: ${display}, ref ${sourceRef}, committed ${commitTime}${modified ? ' (local modifications)' : ''}`,
);
