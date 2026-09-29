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
  /* Not the same as "nothing is live". The site may well be there and newer;
   * replacing it on a guess is the one outcome that loses documentation. */
  if (live === UNREADABLE) {
    return { publish: false, reason: 'Could not read the live site.' };
  }
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

/* Two different answers hide behind a failed fetch, and they call for
 * opposite decisions.
 *
 * "Nothing is live": the URL is genuinely not there. That is 404 and 410, and
 * it is also a 200 whose body is not build-info, which is what the MkDocs site
 * serves at this path before cutover. Publishing over that is the point.
 *
 * "Cannot tell": a 5xx, a rate limit, a DNS or connection failure. The live
 * site may be newer than this build. Pages and its CDN are the sort of thing
 * that is briefly unavailable, so this retries before giving up, and giving up
 * means skipping rather than overwriting.
 */
const UNREADABLE = Symbol('unreadable');
const ABSENT_STATUS = new Set([404, 410]);
const ATTEMPTS = 4;
/* Overridden by the tests so they do not pay the real backoff. */
const BACKOFF_MS = Number(process.env.DOCS_LIVE_FETCH_BACKOFF_MS ?? 1000);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const parseLive = (body) => {
  try {
    return JSON.parse(body);
  } catch {
    console.log('Live response is not build-info.json; treating the site as pre-Astro.');
    return null;
  }
};

const readLive = async () => {
  const inline = process.env.DOCS_LIVE_BUILD_INFO;
  if (inline !== undefined) return parseLive(inline);

  const url = process.env.DOCS_LIVE_BUILD_INFO_URL;
  if (!url) return null;

  for (let attempt = 1; attempt <= ATTEMPTS; attempt += 1) {
    let failure;
    try {
      const response = await fetch(url, { redirect: 'follow' });
      if (ABSENT_STATUS.has(response.status)) {
        console.log(`Live build-info.json fetch returned ${response.status}; nothing is live.`);
        return null;
      }
      if (response.ok) return parseLive(await response.text());
      failure = `HTTP ${response.status}`;
    } catch (error) {
      failure = error.message;
    }
    console.log(`Live build-info.json fetch attempt ${attempt} of ${ATTEMPTS} failed: ${failure}`);
    if (attempt < ATTEMPTS) await sleep(BACKOFF_MS * 2 ** (attempt - 1));
  }
  return UNREADABLE;
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
