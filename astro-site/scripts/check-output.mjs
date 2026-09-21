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
check(
  read(dist, "index.html").includes(buildInfo.display),
  `Home does not show the build version "${buildInfo.display}".`,
);
check(
  read(dist, "index.html").includes(buildInfo.shortCommit),
  `Home does not show the source commit ${buildInfo.shortCommit}.`,
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

/*
 * Mermaid. The diagrams are rendered at build time by a headless browser, and
 * the failure mode is silent: with the browser missing or the markdown
 * processor swapped back, the fence renders as a code block and the page still
 * builds, still passes every other assertion here and still deploys. So both
 * halves are asserted, the SVG being there and the fence not.
 */
const MERMAID_ROUTES = [
  /* Engines and transports replaced their flowcharts with decision tables (#340). */
  "guide/concepts/capture/",
  "guide/concepts/",
];
for (const segment of MERMAID_ROUTES) {
  const file = path.join(dist, segment, "index.html");
  if (
    !check(
      exists(file),
      `Mermaid route ${base}${segment} is not in the artifact.`,
    )
  ) {
    continue;
  }
  const html = read(file);
  check(
    /<svg[^>]*\baria-roledescription="flowchart-v2"/.test(html),
    `${base}${segment}: no build-time mermaid SVG.`,
  );
}
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
