/*
 * Decides whether the candidate artifact may replace what is already live.
 *
 * The site has one deployment and several routes into it: a release build from
 * a tag, a docs-only push to main, and a manual dispatch from any ref. Those
 * can arrive out of order, so ordering is decided from the live artifact
 * rather than from the trigger: a re-run of an old release, or an artifact
 * that sat in a queue, must not overwrite newer documentation.
 *
 * Refusing is a skip, not a failure. Nothing is broken when a newer build
 * already owns the site, and failing the job would page someone for it.
 *
 * Inputs, all from the environment:
 *   DOCS_LIVE_BUILD_INFO_URL  where the live build-info.json is fetched from
 *   DOCS_LIVE_BUILD_INFO      the live payload inline, used by the tests
 *   DOCS_BUILD_INFO           path to the candidate build-info.json
 *   DOCS_BUILD_INFO_JSON      the candidate payload inline
 *
 * Writes publish=true|false and reason=<text> to $GITHUB_OUTPUT when set.
 */
import fs from 'node:fs';
import process from 'node:process';

const VERSION = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/;

/** @param {unknown} value */
const parseVersion = (value) => {
  const match = VERSION.exec(String(value ?? '').trim());
  if (!match) return null;
  return {
    release: [Number(match[1]), Number(match[2]), Number(match[3])],
    prerelease: match[4] ?? null,
  };
};

/* Semver ordering, reduced to what a product version needs: numeric release
 * parts, then the rule that a prerelease sorts below its own release. */
const comparePrerelease = (a, b) => {
  if (a === b) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  const left = a.split('.');
  const right = b.split('.');
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const one = left[index];
    const other = right[index];
    if (one === undefined) return -1;
    if (other === undefined) return 1;
    const numeric = /^\d+$/.test(one) && /^\d+$/.test(other);
    const order = numeric
      ? Number(one) - Number(other)
      : one < other
        ? -1
        : one > other
          ? 1
          : 0;
    if (order !== 0) return order < 0 ? -1 : 1;
  }
  return 0;
};

const compareVersions = (a, b) => {
  for (let index = 0; index < 3; index += 1) {
    if (a.release[index] !== b.release[index]) {
      return a.release[index] < b.release[index] ? -1 : 1;
    }
  }
  return comparePrerelease(a.prerelease, b.prerelease);
};

const decide = (candidate, live) => {
  if (live === null) {
    return { publish: true, reason: 'No readable build-info.json is live yet.' };
  }
  const liveVersion = parseVersion(live.version);
  if (!liveVersion) {
    return {
      publish: true,
      reason: `Live build-info.json carries no usable version (${JSON.stringify(live.version ?? null)}).`,
    };
  }
  const candidateVersion = parseVersion(candidate.version);
  const order = compareVersions(candidateVersion, liveVersion);
  if (order < 0) {
    return {
      publish: false,
      reason: `Candidate ${candidate.version} is older than the live ${live.version}.`,
    };
  }
  if (order > 0) {
    return {
      publish: true,
      reason: `Candidate ${candidate.version} is newer than the live ${live.version}.`,
    };
  }
  const candidateTime = Date.parse(candidate.commitTime);
  const liveTime = Date.parse(live.commitTime);
  if (Number.isNaN(liveTime)) {
    return {
      publish: true,
      reason: `Live build-info.json carries no usable commit time for ${live.version}.`,
    };
  }
  if (Number.isNaN(candidateTime)) {
    throw new Error(
      `Candidate build-info.json has an unreadable commitTime: ${JSON.stringify(candidate.commitTime)}`,
    );
  }
  if (candidateTime < liveTime) {
    return {
      publish: false,
      reason: `Candidate ${candidate.version} was committed ${candidate.commitTime}, older than the live ${live.commitTime}.`,
    };
  }
  return {
    publish: true,
    reason: `Candidate ${candidate.version} at ${candidate.commitTime} is at least as new as the live ${live.commitTime}.`,
  };
};

const readCandidate = () => {
  const inline = process.env.DOCS_BUILD_INFO_JSON;
  const source = inline ?? fs.readFileSync(process.env.DOCS_BUILD_INFO ?? 'dist/build-info.json', 'utf8');
  const candidate = JSON.parse(source);
  if (!parseVersion(candidate.version)) {
    throw new Error(
      `Candidate build-info.json has no usable version: ${JSON.stringify(candidate.version ?? null)}`,
    );
  }
  return candidate;
};

/* Anything short of a parseable payload means "nothing comparable is live":
 * a 404 before the first Astro deployment, an HTML error page from a CDN, a
 * truncated file. None of those are a reason to hold a good build back. */
const readLive = async () => {
  const inline = process.env.DOCS_LIVE_BUILD_INFO;
  if (inline !== undefined) {
    try {
      return JSON.parse(inline);
    } catch {
      return null;
    }
  }
  const url = process.env.DOCS_LIVE_BUILD_INFO_URL;
  if (!url) return null;
  try {
    const response = await fetch(url, { redirect: 'follow' });
    if (!response.ok) {
      console.log(`Live build-info.json fetch returned ${response.status}.`);
      return null;
    }
    return JSON.parse(await response.text());
  } catch (error) {
    console.log(`Live build-info.json is unreadable: ${error.message}`);
    return null;
  }
};

const candidate = readCandidate();
const decision = decide(candidate, await readLive());

console.log(
  `${decision.publish ? 'Proceed' : 'Skip'}: ${decision.reason} Candidate ${candidate.version} (${candidate.commit ?? 'unknown commit'}).`,
);
if (process.env.GITHUB_OUTPUT) {
  fs.appendFileSync(
    process.env.GITHUB_OUTPUT,
    `publish=${decision.publish}\nreason=${decision.reason}\n`,
  );
}
