/*
 * Reads the artifact back and asserts what the site promised.
 *
 * Everything here is checked against dist/ rather than against the source,
 * because the artifact is what gets deployed and what a reviewer downloads
 * from a pull request run. Nothing in this file needs a network or a browser.
 *
 * helia-ui-check-discoverability runs after this one (see package.json) and
 * owns the per-page tags. This file owns the contracts that are specific to
 * this site: provenance, the section shape, the legacy redirects, the sitemap
 * count and the 404 page.
 */
import fs from "node:fs";
import path from "node:path";
import { measure, overBudget } from "./lib/page-budget.mjs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const site = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(site, "dist");
const base = "/helia-profiler/";
const origin = "https://ambiqai.github.io";

const failures = [];
const check = (condition, message) => {
  if (!condition) failures.push(message);
  return condition;
};

const read = (...segments) => fs.readFileSync(path.join(...segments), "utf8");
const exists = (...segments) => fs.existsSync(path.join(...segments));

if (!exists(dist, "index.html")) {
  throw new Error(`No build at ${dist}. Run npm run build first.`);
}

const walk = (directory) =>
  fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(entryPath) : [entryPath];
  });

const REDIRECT = /<meta[^>]*\bhttp-equiv=["']refresh["']/i;
const htmlFiles = walk(dist).filter((file) => file.endsWith(".html"));
const pages = new Map(htmlFiles.map((file) => [file, read(file)]));

/* Astro's bare 404.html has no source page, so it is not a content route; the
 * 404/ directory Starlight builds from 404.mdx is. */
const contentPages = htmlFiles.filter(
  (file) =>
    path.relative(dist, file) !== "404.html" && !REDIRECT.test(pages.get(file)),
);
const routeOf = (file) =>
  `${base}${path.relative(dist, file).replace(/index\.html$/, "")}`;

/* Provenance. */
const buildInfo = JSON.parse(read(dist, "build-info.json"));
/* The artifact has to come from the checkout being validated. A stale dist/,
 * left by an earlier build or restored from a cache, would otherwise pass
 * every other assertion here while carrying another commit's provenance. */
const head = execFileSync("git", ["rev-parse", "HEAD"], {
  cwd: path.resolve(site, ".."),
  encoding: "utf8",
}).trim();
check(
  buildInfo.commit === head,
  `Artifact was built from ${buildInfo.commit}, the checkout is at ${head}.`,
);
check(
  buildInfo.product === "heliaPROFILER",
  "build-info.json names the wrong product.",
);
check(
  /^\d+\.\d+\.\d+/.test(buildInfo.version ?? ""),
  `build-info.json version is unusable: ${buildInfo.version}`,
);
check(
  /^[0-9a-f]{40}$/.test(buildInfo.commit ?? ""),
  `build-info.json commit is unusable: ${buildInfo.commit}`,
);
check(Boolean(buildInfo.sourceRef), "build-info.json carries no source ref.");
check(
  !Number.isNaN(Date.parse(buildInfo.commitTime ?? "")),
  `build-info.json commitTime is unusable: ${buildInfo.commitTime}`,
);
check(
  !Number.isNaN(Date.parse(buildInfo.buildTime ?? "")),
  `build-info.json buildTime is unusable: ${buildInfo.buildTime}`,
);
/* Home shows the released version and nothing about the build that produced
 * the page: a commit hash or a commits-since-tag count is provenance for the
 * deploy guard, not for a reader. */
const home = read(dist, "index.html");
const versionLine =
  /<p class="[^"]*\bdocs-version\b[^"]*"[^>]*>([\s\S]*?)<\/p>/.exec(home)?.[1] ?? "";
check(versionLine !== "", "Home has no version line.");
check(
  versionLine.includes(`v${buildInfo.version}`),
  `Home does not show the version v${buildInfo.version}.`,
);
check(
  !versionLine.includes(buildInfo.shortCommit),
  `Home shows the source commit ${buildInfo.shortCommit}; the site carries the version only.`,
);

/* Home names hardware, so it is held to the registry rather than to whatever
 * was typed into the page. src/data/catalog.json is read out of
 * src/helia_profiler by scripts/build-catalog.mjs; an engine added there is a
 * failing build until Home names it, and the two figures on the page are the
 * registry's counts. The figures are typed into the page as text rather than
 * imported, because the Markdown rendition drops a JSX expression and the
 * rendition is the copy an agent reads; this check is what keeps the typed
 * figure honest. Read against the artifact, like everything else here.
 *
 * The tree assertion below cannot fire in CI, where prepare:docs regenerates
 * the catalog from the same HEAD just before the build; a stale committed
 * catalog is caught by check-committed-artifacts.mjs. It stays for a local
 * dist/ built from another checkout. */
const catalog = JSON.parse(read(site, "src/data/catalog.json"));
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
check(
  catalog.generatedFrom?.sourceTree ===
    execFileSync("git", ["rev-parse", `HEAD:${catalog.generatedFrom?.sourcePath}`], {
      cwd: path.resolve(site, ".."),
      encoding: "utf8",
    }).trim(),
  `src/data/catalog.json was generated from tree ${catalog.generatedFrom?.sourceTree}, ` +
    `which is not the committed ${catalog.generatedFrom?.sourcePath}. Run npm run catalog:build.`,
);
const missingEngines = catalog.engines
  .map((entry) => entry.id)
  .filter((id) => !new RegExp(`<code[^>]*>${escape(id)}</code>`).test(home));
check(
  missingEngines.length === 0,
  `Home does not name ${missingEngines.length} engine(s) the registry carries: ${missingEngines.join(", ")}.`,
);
/* The Toolchain enum carries one alias pair, gcc and arm-none-eabi-gcc, which
 * vocab.py documents as the same GNU Arm toolchain; the page counts toolchains,
 * not spellings. */
const toolchainValues = JSON.parse(read(site, "src/data/schema.json")).$defs?.Toolchain?.enum;
if (!Array.isArray(toolchainValues)) {
  throw new Error("src/data/schema.json carries no $defs.Toolchain enum to count toolchains from.");
}
const toolchains = toolchainValues.filter((value) => value !== "gcc").length;
const stableBoards = catalog.boards.filter((board) => board.channel === "stable").length;
for (const [label, expected] of [
  ["boards", catalog.counts.boards],
  ["engines", catalog.counts.engines],
  ["toolchains", toolchains],
]) {
  check(
    new RegExp(`>${expected} ${label}<`).test(home),
    `Home shows no figure of ${expected} ${label}, which is what the registry counts.`,
  );
}
check(
  home.includes(`${stableBoards} of them on the stable channel`),
  `Home does not say ${stableBoards} boards are on the stable channel, which is what the registry counts.`,
);

/* Section shape: five in the top navigation, a scoped sidebar on four of
 * them, and Home with the marker that takes the pane's column back. */
const SECTIONS = [
  ["Home", ""],
  ["Getting started", "getting-started/"],
  ["User guide", "guide/"],
  ["Examples", "examples/"],
  ["Reference", "reference/"],
];
const MOBILE_ONLY = 'data-helia-sidebar-layout="mobile-only"';
for (const [label, segment] of SECTIONS) {
  const file = path.join(dist, segment, "index.html");
  if (
    !check(
      exists(file),
      `Section "${label}" has no landing page at ${base}${segment}.`,
    )
  ) {
    continue;
  }
  const html = read(file);
  check(
    html.includes(`data-helia-sidebar-heading>${label}`),
    `Section "${label}" landing page does not name its sidebar.`,
  );
  const homeless = html.includes(MOBILE_ONLY);
  check(
    label === "Home" ? homeless : !homeless,
    label === "Home"
      ? "Home renders a sidebar at desktop width."
      : `Section "${label}" has no sidebar of its own.`,
  );
  for (const [other] of SECTIONS) {
    check(
      html.includes(`>${other}</span>`),
      `Section "${label}" landing page does not link to "${other}".`,
    );
  }
}

/* Legacy routes. */
const redirects = JSON.parse(read(site, "src/data/redirects.json"));
for (const [route, target] of Object.entries(redirects.redirects)) {
  const file = path.join(dist, route, "index.html");
  if (!check(exists(file), `Legacy route ${route} has no redirect stub.`))
    continue;
  check(
    read(file).includes(`url=${target}`),
    `Legacy route ${route} does not forward to ${target}.`,
  );
  check(
    exists(dist, target.slice(base.length), "index.html"),
    `Legacy route ${route} forwards to ${target}, which is not in the artifact.`,
  );
}
for (const route of redirects.served) {
  const file = path.join(dist, route.slice(1), "index.html");
  if (
    !check(
      exists(file),
      `Legacy route ${route} is claimed as served but has no page.`,
    )
  ) {
    continue;
  }
  check(
    !REDIRECT.test(read(file)),
    `Legacy route ${route} is claimed as served but is a redirect stub.`,
  );
}

/* Every flowchart became a decision table or a block diagram (#340, #343) and
 * the mermaid renderer is gone, so a fence that reaches a page can only come
 * out as a code block. */
for (const file of htmlFiles) {
  check(
    !/<pre[^>]*>[\s\S]{0,200}?language-mermaid/.test(pages.get(file)),
    `${routeOf(file)}: a mermaid fence reached the page as a code block.`,
  );
}

/* Every content page meets the same byte budget the reference pages do. */
for (const file of contentPages) {
  const reasons = overBudget(measure(pages.get(file)));
  check(
    reasons.length === 0,
    `${routeOf(file)}: over the page budget (${reasons.join("; ")}).`,
  );
}

/* Canonical URLs and Markdown renditions on every content route. */
for (const file of contentPages) {
  const route = routeOf(file);
  const canonical =
    /<link[^>]*\brel=["']canonical["'][^>]*\bhref=["']([^"']+)["']/i.exec(
      pages.get(file),
    )?.[1];
  check(
    canonical === `${origin}${route}`,
    `${route}: canonical is ${canonical ?? "missing"}.`,
  );
  check(
    exists(file.replace(/index\.html$/, "index.md")),
    `${route}: no Markdown rendition at index.md.`,
  );
}

/* The sitemap covers every content route. */
const sitemapIndex = read(dist, "sitemap-index.xml");
const shards = [...sitemapIndex.matchAll(/<loc>([^<]+)<\/loc>/g)].map((match) =>
  match[1].slice(`${origin}${base}`.length),
);
const locations = shards.flatMap((shard) =>
  [...read(dist, shard).matchAll(/<loc>([^<]+)<\/loc>/g)].map(
    (match) => match[1],
  ),
);
const expected = contentPages.map((file) => `${origin}${routeOf(file)}`).sort();
check(
  JSON.stringify([...locations].sort()) === JSON.stringify(expected),
  `Sitemap lists ${locations.length} routes, artifact has ${expected.length} content routes.`,
);

/* llms.txt indexes the section landing pages. */
const llms = read(dist, "llms.txt");
for (const [label, segment] of SECTIONS) {
  check(
    llms.includes(`${origin}${base}${segment}index.md`),
    `llms.txt does not list the "${label}" landing page.`,
  );
}

/* The 404 page: no timed jump to Home, and both its links resolve. */
const notFound = read(dist, "404.html");
check(
  /name=["']robots["'][^>]*noindex/i.test(notFound),
  "404 page is missing robots noindex.",
);
check(!REDIRECT.test(notFound), "404 page carries a meta refresh.");
/* It is served from its own URL and it is not a content route, so a canonical
 * anywhere else names a page the artifact does not contain. */
const notFoundCanonicals = [
  ...notFound.matchAll(
    /<link[^>]*\brel=["']canonical["'][^>]*\bhref=["']([^"']+)["']/gi,
  ),
].map((match) => match[1]);
check(
  JSON.stringify(notFoundCanonicals) ===
    JSON.stringify([`${origin}${base}404.html`]),
  `404 page canonical is ${JSON.stringify(notFoundCanonicals)}.`,
);
for (const target of [base, `${base}reference/`]) {
  check(
    notFound.includes(`href="${target}"`),
    `404 page does not link to ${target}.`,
  );
  check(
    exists(dist, target.slice(base.length), "index.html"),
    `404 page links to ${target}, which is not in the artifact.`,
  );
}

if (failures.length > 0) {
  console.error("Artifact assertions failed:\n");
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log(
  `Artifact verified: ${contentPages.length} content routes, ${locations.length} sitemap entries, ` +
    `${Object.keys(redirects.redirects).length} legacy redirects, built from ${buildInfo.display} ` +
    `on ref ${buildInfo.sourceRef}.`,
);
