// SPDX-License-Identifier: BSD-3-Clause
/**
 * Renders the PMU catalogue into the PMU counters page as literal Markdown.
 *
 * A component would render the same tables into HTML, but the discoverability
 * integration reduces component output to its prose, so the `.md` rendition
 * and llms-full.txt would carry no counter names at all. Writing the tables
 * into the page source is what makes them reach an agent. The page is
 * generated from src/templates/pmu-counters.mdx and committed; the stale
 * check compares it with a fresh render.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const site = path.resolve(here, "..");

export const TEMPLATE = "src/templates/pmu-counters.mdx";
export const PAGE = "src/content/docs/guide/pmu-counters.mdx";
export const CATALOG = "src/data/pmu-catalog.json";

const cell = (value) => String(value).replace(/\|/g, "\\|").replace(/\n/g, " ");

export function socsTable(catalog) {
  const lines = [
    "| SoC | Profiling domains | Counter groups | Per-operator record budget |",
    "|---|---|---|---|",
  ];
  for (const soc of catalog.socs) {
    lines.push(
      `| \`${soc.soc}\` | ${soc.domains.join(", ")} | ${soc.groups.map((g) => `\`${g}\``).join(", ")} | ${soc.pmuMaxOps} |`,
    );
  }
  return lines.join("\n");
}

export function groupsTables(catalog) {
  const out = [];
  for (const group of catalog.groups) {
    const defaults = new Set(group.default);
    out.push(`#### \`${group.group}\`: ${group.counters.length} counters`, "");
    out.push(
      `\`default\` selects ${group.default.map((n) => `\`${n}\``).join(", ")}.`,
      "",
      "| Counter | Event | Description |",
      "|---|---|---|",
    );
    for (const counter of group.counters) {
      const mark = defaults.has(counter.name) ? " (default)" : "";
      out.push(
        `| \`${counter.name}\`${mark} | ${counter.eventId} | ${cell(counter.description)} |`,
      );
    }
    out.push("");
  }
  out.push(
    `Generated from the counter registry at source tree \`${catalog.generatedFrom.sourceTree.slice(0, 7)}\`: ${catalog.counts.counters} counters in ${catalog.counts.groups} groups across ${catalog.counts.socs} SoCs.`,
  );
  return out.join("\n");
}

export function render(template, catalog) {
  if (
    !template.includes("{/* PMU_CATALOG:socs */}") ||
    !template.includes("{/* PMU_CATALOG:groups */}")
  ) {
    throw new Error(`${TEMPLATE} lacks the PMU_CATALOG markers.`);
  }
  return template
    .replace("{/* PMU_CATALOG:socs */}", socsTable(catalog))
    .replace("{/* PMU_CATALOG:groups */}", groupsTables(catalog));
}

export function renderInto(outRoot) {
  const template = fs.readFileSync(path.join(site, TEMPLATE), "utf8");
  const catalog = JSON.parse(
    fs.readFileSync(path.join(outRoot, CATALOG), "utf8"),
  );
  const target = path.join(outRoot, PAGE);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, render(template, catalog), "utf8");
  return target;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const outRoot = process.argv.includes("--out")
    ? path.resolve(process.argv[process.argv.indexOf("--out") + 1])
    : site;
  const target = renderInto(outRoot);
  console.log(`pmu catalogue rendered into ${path.relative(outRoot, target)}`);
}
