/*
 * Resolve the build provenance of the generated reference at build time.
 *
 * The committed pages and artifacts carry placeholders where a git ref or the
 * source tree would go. A committed branch name would link to that branch
 * whatever this build is of, and a committed commit sha or tree sha would
 * rewrite every generated file on every change under src/, including changes
 * that leave the documented content alone. Both are properties of the build,
 * not of the documented content, so they are substituted here from
 * build-info.json, which carries the ref the workflow checked out (a release
 * tag, a branch, or main) and the tree of `src/helia_profiler` at that commit.
 *
 * This runs over the artifact rather than through a Markdown plugin because
 * Astro 7 defaults to the Sätteri processor, where remark plugins need
 * @astrojs/markdown-remark and a processor switch for the whole site. It has
 * to be the last integration: Starlight and helia-ui write the Markdown
 * renditions and the llms exports in astro:build:done too, and hooks run in
 * the order the integrations are declared.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const SOURCE_REF_TOKEN = '__DOCS_SOURCE_REF__';
export const SOURCE_TREE_TOKEN = '__DOCS_SOURCE_TREE__';
export const TOKENS = [SOURCE_REF_TOKEN, SOURCE_TREE_TOKEN];

const REWRITTEN = new Set(['.html', '.json', '.md', '.txt', '.xml']);

/**
 * The ref this build documents.
 *
 * build-info.mjs has already resolved it: the workflow's source_ref, else the
 * release tag, else the branch that was checked out. Recomputing a guess here
 * would send a workflow_dispatch build of docs-migration to main.
 */
export const sourceRef = (buildInfo) =>
  buildInfo?.sourceRef || buildInfo?.releaseTag || 'main';

/**
 * The git tree of `src/helia_profiler` this build documents.
 *
 * The stale checks prove the committed reference is byte-for-byte what the
 * source at HEAD produces, so naming HEAD's tree at build time makes the same
 * claim a committed stamp would, without the churn. A build-info.json with no
 * tree would publish the placeholder, so that is refused by name.
 */
export function sourceTreeOf(buildInfo) {
  const tree = buildInfo?.sourceTree;
  if (!/^[0-9a-f]{40}$/.test(tree ?? '')) {
    throw new Error(
      `build-info.json carries no usable sourceTree (${JSON.stringify(tree ?? null)}). ` +
        'It is written by npm run prepare:docs, which runs in prebuild.',
    );
  }
  return tree;
}

/** Every placeholder the committed files carry, paired with this build's value. */
export const substitutions = (buildInfo) => [
  [SOURCE_REF_TOKEN, sourceRef(buildInfo)],
  [SOURCE_TREE_TOKEN, sourceTreeOf(buildInfo)],
];

export const substitute = (body, pairs) =>
  pairs.reduce((text, [token, value]) => text.replaceAll(token, value), body);

const tokenised = (body) => TOKENS.some((token) => body.includes(token));

export function readBuildInfo(siteDir) {
  const file = path.join(siteDir, 'src/data/build-info.json');
  if (!fs.existsSync(file)) {
    throw new Error(`No ${file}. It is written by npm run prepare:docs, which runs in prebuild.`);
  }
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

export function sourceRefArtifacts() {
  let siteDir;
  return {
    name: 'helia-profiler:source-ref',
    hooks: {
      'astro:config:setup': ({ config, updateConfig }) => {
        siteDir = fileURLToPath(config.root);
        /* The dev server never reaches astro:build:done, and a source link
         * reading `__DOCS_SOURCE_REF__` in a preview is a link nobody can
         * follow. Rewriting the module source covers dev; the build:done pass
         * still owns public/ and the renditions written after it. */
        updateConfig({
          vite: {
            plugins: [
              {
                name: 'helia-profiler:source-ref-mdx',
                enforce: 'pre',
                transform(code, id) {
                  if (!id.endsWith('.mdx') || !tokenised(code)) return null;
                  return {
                    code: substitute(code, substitutions(readBuildInfo(siteDir))),
                    map: null,
                  };
                },
              },
            ],
          },
        });
      },
      'astro:build:done': ({ dir }) => {
        const pairs = substitutions(readBuildInfo(siteDir));
        const root = fileURLToPath(dir);
        let rewritten = 0;
        const walk = (directory) => {
          for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
            const entryPath = path.join(directory, entry.name);
            if (entry.isDirectory()) {
              walk(entryPath);
              continue;
            }
            if (!REWRITTEN.has(path.extname(entry.name))) continue;
            const body = fs.readFileSync(entryPath, 'utf8');
            if (!tokenised(body)) continue;
            fs.writeFileSync(entryPath, substitute(body, pairs), 'utf8');
            rewritten += 1;
          }
        };
        walk(root);
        const [[, ref], [, tree]] = pairs;
        console.log(
          `source ref ${ref} and source tree ${tree.slice(0, 7)} written into ${rewritten} files.`,
        );
      },
    },
  };
}
